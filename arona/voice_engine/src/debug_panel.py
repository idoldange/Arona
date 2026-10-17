"""Debug panel under /debug (served by debug_web.py): login, TensorBoard (system + bot metrics), interactive terminal,
Python console inside the bot.

Existing tools are used wherever possible; only the glue is custom:
  - System / bot metrics: written as scalars to logs/tb and shown by TensorBoard (own subprocess, reverse-proxied under
    /debug/tensorboard/ so it sits behind the same login).
  - Terminal: xterm.js in the browser + a real PTY on the server (pywinpty / ConPTY on Windows, pty on Linux/macOS).

Login reuses the credentials of server.js: pass.txt (raw password) -> pass.hash (bcrypt, cost 10), user "idoldange".
The browser only keeps a signed session token (HttpOnly cookie), never the password. If no password can be loaded the
panel stays locked (fail closed): the shell / python endpoints are never served without a login.
"""
import ast
import asyncio
import codecs
import contextlib
import hashlib
import hmac
import inspect
import io
import json
import linecache
import os
import reprlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

import aiohttp
from aiohttp import web, WSMsgType

from console import console
from arona.voice_engine.src import voice_metrics as _vm

try:
    import psutil as _psutil
except Exception:  # pragma: no cover
    _psutil = None

# C:\arona\arona\voice_engine\src\debug_panel.py -> C:\arona
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PASS_FILE = os.path.join(ROOT, "pass.txt")
HASH_FILE = os.path.join(ROOT, "pass.hash")
DEBUG_USER = "idoldange"                       # same user name as server.js
COOKIE = "arona_debug"
SESSION_TTL = 30 * 24 * 3600                   # stay logged in for 30 days
LOGIN_MAX_FAILS, LOGIN_WINDOW = 7, 15 * 60     # same limit as server.js: 7 wrong tries / 15 min / IP
EXEC_TIMEOUT = 60
OUT_LIMIT = 200_000

NAV = [("/debug", "Home"), ("/debug/call_ui", "Call UI"), ("/debug/system", "System"), ("/debug/db", "Database"),
       ("/debug/ide", "IDE"), ("/debug/terminal", "Terminal")]
TB_PREFIX = "/debug/tensorboard"
TB_LOGDIR = os.path.join(ROOT, "logs", "tb")
TB_KEEP_RUNS = 8                               # old run-* folders under logs/tb beyond this are deleted
TB_INTERVAL = 2.0                              # seconds between metric samples


# ───────────────────────────── auth ─────────────────────────────
_UNSET = object()
_hash_cache: Dict[str, Any] = {"sig": None, "hash": _UNSET}
_fails: Dict[str, List[float]] = {}


def _file_sig():
    sig = []
    for p in (PASS_FILE, HASH_FILE):
        try:
            st = os.stat(p)
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def _load_hash() -> Optional[bytes]:
    """Same logic as server.js ensurePasswordHash(): pass.txt is the source; pass.hash holds its bcrypt hash and is
    rewritten when pass.txt no longer matches it. Blocking (bcrypt); run it in an executor."""
    try:
        import bcrypt
    except ImportError:
        console.log("[DebugWeb] 'bcrypt' is not installed (pip install bcrypt): debug panel stays locked", "ERROR")
        return None
    raw = old = None
    try:
        with open(PASS_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        pass
    try:
        with open(HASH_FILE, "r", encoding="utf-8") as f:
            old = f.read().strip().encode() or None
    except OSError:
        pass
    if not raw:
        return old
    ok = False
    if old:
        try:
            ok = bcrypt.checkpw(raw.encode(), old)
        except ValueError:
            ok = False
    if ok:
        return old
    try:
        new = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(10))
        with open(HASH_FILE, "wb") as f:
            f.write(new)
        return new
    except (ValueError, OSError) as e:
        console.log(f"[DebugWeb] could not (re)create pass.hash: {e}", "ERROR")
        return None


async def get_hash() -> Optional[bytes]:
    sig = _file_sig()
    if _hash_cache["sig"] == sig and _hash_cache["hash"] is not _UNSET:
        return _hash_cache["hash"]
    h = await asyncio.get_running_loop().run_in_executor(None, _load_hash)
    _hash_cache["sig"], _hash_cache["hash"] = _file_sig(), h
    return h


def _check_pw(pw: str, h: bytes) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(pw.encode(), h)
    except ValueError:
        return False


def _key(h: bytes) -> bytes:
    # derived from the secret hash file: changing the password invalidates every session
    return hashlib.sha256(b"arona-debug-session-v1" + h).digest()


def make_token(h: bytes) -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    return exp + "." + hmac.new(_key(h), exp.encode(), hashlib.sha256).hexdigest()


def check_token(tok: str, h: bytes) -> bool:
    try:
        exp, sig = tok.split(".", 1)
        if int(exp) < time.time():
            return False
        return hmac.compare_digest(sig, hmac.new(_key(h), exp.encode(), hashlib.sha256).hexdigest())
    except (ValueError, AttributeError):
        return False


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        return urlsplit(origin).netloc.lower() == request.host.lower()
    except ValueError:
        return False


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if not (path == "/debug" or path.startswith("/debug/")):
        return await handler(request)
    is_ws = request.headers.get("Upgrade", "").lower() == "websocket"
    # cross-site request forgery / websocket hijacking: a browser always sends Origin on those
    if (request.method != "GET" or is_ws) and not _same_origin(request):
        return web.json_response({"error": "bad origin"}, status=403)
    if path == "/debug/login":
        return await handler(request)
    h = await get_hash()
    if h is None:
        return web.Response(status=503, text="Debug panel locked: no password available (pass.txt / pass.hash) "
                                             "or the 'bcrypt' package is missing (pip install bcrypt).")
    if check_token(request.cookies.get(COOKIE, ""), h):
        return await handler(request)
    if path.startswith("/debug/api/") or path.startswith("/debug/ws/") or is_ws:
        return web.json_response({"error": "unauthorized"}, status=401)
    nxt = request.path_qs if request.method == "GET" else "/debug"
    raise web.HTTPFound("/debug/login?next=" + quote(nxt, safe=""))


def _safe_next(n: str) -> str:
    return n if n.startswith("/debug") and not n.startswith("//") and "\\" not in n else "/debug"


async def login_page(request: web.Request) -> web.Response:
    return web.Response(text=LOGIN_HTML, content_type="text/html", headers={"Cache-Control": "no-store"})


async def login_post(request: web.Request) -> web.Response:
    ip = request.remote or "?"
    now = time.time()
    fails = [t for t in _fails.get(ip, []) if now - t < LOGIN_WINDOW]
    if len(fails) >= LOGIN_MAX_FAILS:
        return web.json_response({"ok": False, "error": "Too many attempts, wait 15 minutes."}, status=429)
    if request.content_type != "application/json":
        return web.json_response({"ok": False, "error": "bad request"}, status=400)
    try:
        data = await request.json()
    except ValueError:
        return web.json_response({"ok": False, "error": "bad request"}, status=400)
    user, pw = str(data.get("username", "")), str(data.get("password", ""))
    h = await get_hash()
    if h is None:
        return web.json_response({"ok": False, "error": "panel locked: no password configured (or bcrypt missing)"}, status=503)
    ok_user = hmac.compare_digest(user.encode(), DEBUG_USER.encode())
    ok_pw = await asyncio.get_running_loop().run_in_executor(None, _check_pw, pw, h)   # always run bcrypt
    if not (ok_user and ok_pw):
        fails.append(now)
        _fails[ip] = fails
        console.log(f"[DebugWeb] failed login from {ip} ({len(fails)}/{LOGIN_MAX_FAILS})", "WARN")
        return web.json_response({"ok": False, "error": "Invalid username or password"}, status=401)
    _fails.pop(ip, None)
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, make_token(h), max_age=SESSION_TTL, httponly=True, samesite="Strict",
                    secure=request.secure, path="/")
    console.log(f"[DebugWeb] login from {ip}", "INFO")
    return resp


async def logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/debug/login")
    resp.del_cookie(COOKIE, path="/")
    raise resp


# ───────────────────────────── pages ─────────────────────────────
NAV_CSS = ("nav{display:flex;gap:4px;align-items:center;padding:8px 12px;background:#1a1b1e;border-bottom:1px solid #2b2d31;"
           "align-self:stretch;width:100%;box-sizing:border-box;font:14px system-ui,sans-serif}"
           "nav a{color:#949ba4;text-decoration:none;padding:6px 10px;border-radius:6px}"
           "nav a:hover{background:#2b2d31;color:#dbdee1}nav a.on{background:#5865f2;color:#fff}nav .sp{flex:1}")

CSS = r"""
:root{--bg:#111214;--panel:#1a1b1e;--line:#2b2d31;--text:#dbdee1;--muted:#949ba4;--accent:#5865f2;--green:#23a55a;--red:#f23f43;--yellow:#f0b232}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}
main{padding:12px;max-width:1400px;margin:0 auto}
a{color:#8ea1ff}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
h3{margin:0 0 6px;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.big{font-size:26px;font-weight:600}
.muted{color:var(--muted)}
canvas{width:100%;height:70px;display:block;margin-top:6px}
.bar{height:6px;background:var(--line);border-radius:3px;margin:3px 0 7px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--accent)}
.row{display:flex;justify-content:space-between;gap:8px}
pre,textarea,input,button{font:13px ui-monospace,Consolas,monospace}
pre{margin:0;white-space:pre-wrap;word-break:break-word}
input,textarea{background:#0d0e10;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:7px 12px;cursor:pointer}
button.alt{background:#2b2d31;color:var(--text)}button:disabled{opacity:.5;cursor:default}
.term{background:#0d0e10;border:1px solid var(--line);border-radius:8px;height:calc(100vh - 190px);min-height:240px;overflow:auto;padding:10px}
.line{display:flex;gap:8px;margin-top:8px;align-items:center}.line input{flex:1}
"""


def nav_block(active: str, with_style: bool = True) -> str:
    items = "".join('<a href="%s"%s>%s</a>' % (h, ' class="on"' if h == active else "", t) for h, t in NAV)
    return ("<style>" + NAV_CSS + "</style>" if with_style else "") + "<nav>" + items + \
        '<span class="sp"></span><a href="/debug/logout">Log out</a></nav>'


def page(title: str, active: str, body: str, script: str = "") -> web.Response:
    html = ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>" + title + " - Arona debug</title><style>" + CSS + NAV_CSS + "</style></head><body>"
            + nav_block(active, False) + "<main>" + body + "</main>"
            + ("<script>" + script + "</script>" if script else "") + "</body></html>")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


LOGIN_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login - Arona debug</title><style>
html,body{margin:0;height:100%;background:#111214;color:#dbdee1;font:14px system-ui,sans-serif}
body{display:flex;align-items:center;justify-content:center}
form{background:#1a1b1e;border:1px solid #2b2d31;border-radius:10px;padding:22px;width:300px;display:flex;flex-direction:column;gap:10px}
h2{margin:0 0 4px;font-size:16px}input{background:#0d0e10;color:#dbdee1;border:1px solid #2b2d31;border-radius:6px;padding:9px;font:14px system-ui}
button{background:#5865f2;color:#fff;border:0;border-radius:6px;padding:9px;font:14px system-ui;cursor:pointer}
#e{color:#f23f43;min-height:18px;font-size:13px}
</style></head><body>
<form id="f"><h2>Arona debug</h2>
<input id="u" placeholder="Username" autocomplete="username" autofocus>
<input id="p" type="password" placeholder="Password" autocomplete="current-password">
<button>Log in</button><div id="e"></div></form>
<script>
document.getElementById('f').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const e = document.getElementById('e'); e.textContent = '';
  try {
    const r = await fetch('/debug/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: document.getElementById('u').value, password: document.getElementById('p').value})});
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok) {
      const n = new URLSearchParams(location.search).get('next') || '/debug';
      location = (n.startsWith('/debug') && !n.startsWith('//')) ? n : '/debug';
    } else { e.textContent = j.error || ('Error ' + r.status); }
  } catch (x) { e.textContent = 'Request failed'; }
});
</script></body></html>"""


async def home_page(request: web.Request) -> web.Response:
    body = r"""
<div class="grid">
  <div class="card"><h3>Bot</h3><div id="bot" class="muted">loading...</div></div>
  <div class="card"><h3>Tools</h3>
    <p><a href="/debug/call_ui">Call UI</a> <span class="muted">- live voice-call window + what the bot hears</span></p>
    <p><a href="/debug/system">System</a> <span class="muted">- TensorBoard: CPU / RAM / GPU / network / bot metrics</span></p>
    <p><a href="/debug/db">Database</a> <span class="muted">- spreadsheet-style editor for the SQLite files in ./database</span></p>
    <p><a href="/debug/terminal">Terminal</a> <span class="muted">- interactive shell on this PC</span></p>
    <p><a href="/debug/ide">IDE</a> <span class="muted">- edit the bot source like VS Code, hot reload without restart, Python console inside the bot</span></p>
  </div>
</div>"""
    script = r"""
const el = document.getElementById('bot');
fetch('/debug/api/stats').then(r => r.json()).then(s => {
  const b = s.bot || {}, p = s.proc || {};
  const rows = [['Guilds', b.guilds], ['Gateway latency', b.latency_ms == null ? '-' : b.latency_ms + ' ms'],
    ['Gemini Live', b.gemini_live ? 'connected' : 'off'], ['In voice', b.in_voice ? 'yes' : 'no'],
    ['Asyncio tasks', p.tasks], ['Threads', p.threads], ['Uptime', p.uptime == null ? '-' : Math.round(p.uptime / 60) + ' min']];
  el.textContent = '';
  rows.forEach(([k, v]) => { const d = document.createElement('div'); d.className = 'row';
    const a = document.createElement('span'); a.className = 'muted'; a.textContent = k;
    const c = document.createElement('span'); c.textContent = v == null ? '-' : v; d.append(a, c); el.appendChild(d); });
}).catch(() => { el.textContent = 'could not load stats'; });
"""
    return page("Home", "/debug", body, script)


async def system_page(request: web.Request) -> web.Response:
    body = r"""
<style>main{max-width:none;padding:0}iframe{display:block;width:100%;height:calc(100vh - 49px);border:0;background:#202124}</style>
<iframe id="tb" title="TensorBoard"></iframe>"""
    script = r"""
// TensorBoard keeps its theme in localStorage (same origin, because it is proxied under /debug): default to dark
try { const k = '_tb_global_settings', s = JSON.parse(localStorage.getItem(k) || '{}');
  if (!s.theme) { s.theme = 'dark'; localStorage.setItem(k, JSON.stringify(s)); } } catch (e) {}
document.getElementById('tb').src = '/debug/tensorboard/';
"""
    return page("System", "/debug/system", body, script)


XTERM_CSS = "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css"
XTERM_JS = "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"
XTERM_FIT = "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"
# Subresource Integrity: this page can run commands on the PC, so the browser must refuse a tampered CDN file
SRI_CSS = "sha384-LJcOxlx9IMbNXDqJ2axpfEQKkAYbFjJfhXexLfiRJhjDU81mzgkiQq8rkV0j6dVh"
SRI_JS = "sha384-/nfmYPUzWMS6v2atn8hbljz7NE0EI1iGx34lJaNzyVjWGDzMv+ciUZUeJpKA3Glc"
SRI_FIT = "sha384-AQLWHRKAgdTxkolJcLOELg4E9rE89CPE2xMy3tIRFn08NcGKPTsELdvKomqji+DL"


async def terminal_page(request: web.Request) -> web.Response:
    body = r"""
<style>main{max-width:none}#bar{display:flex;gap:8px;align-items:center;margin-bottom:8px}
#term{height:calc(100vh - 110px);background:#0d0e10;border:1px solid #2b2d31;border-radius:8px;padding:6px}
.xterm-viewport{background:#0d0e10!important}</style>
<link rel="stylesheet" href="__CSS__" integrity="__SRI_CSS__" crossorigin="anonymous">
<div id="bar"><span class="muted" id="info">connecting...</span><span style="flex:1"></span>
<button class="alt" id="re">Reconnect</button></div>
<div id="term"></div>
<script src="__JS__" integrity="__SRI_JS__" crossorigin="anonymous"></script>
<script src="__FIT__" integrity="__SRI_FIT__" crossorigin="anonymous"></script>"""
    body = (body.replace("__CSS__", XTERM_CSS).replace("__JS__", XTERM_JS).replace("__FIT__", XTERM_FIT)
            .replace("__SRI_CSS__", SRI_CSS).replace("__SRI_JS__", SRI_JS).replace("__SRI_FIT__", SRI_FIT))
    script = r"""
const info = document.getElementById('info');
if (typeof Terminal === 'undefined') { info.textContent = 'xterm.js failed to load (CDN blocked / offline?)'; }
else {
  const term = new Terminal({cursorBlink: true, scrollback: 10000, fontSize: 14,
    fontFamily: 'Consolas, "Cascadia Mono", ui-monospace, monospace',
    theme: {background: '#0d0e10', foreground: '#dbdee1', cursor: '#dbdee1', selectionBackground: '#3b4252'}});
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit);
  term.open(document.getElementById('term')); fit.fit(); term.focus();
  let ws = null;
  const send = o => ws && ws.readyState === 1 && ws.send(JSON.stringify(o));
  function connect() {
    fit.fit();
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
                       '/debug/ws/terminal?cols=' + term.cols + '&rows=' + term.rows);
    ws.onopen = () => { info.textContent = 'connected'; term.focus(); };
    ws.onmessage = e => term.write(e.data);
    ws.onclose = () => { info.textContent = 'disconnected'; term.write('\r\n\x1b[31m[disconnected]\x1b[0m\r\n'); };
  }
  term.onData(d => send({type: 'input', data: d}));
  term.onResize(s => send({type: 'resize', cols: s.cols, rows: s.rows}));
  window.addEventListener('resize', () => fit.fit());
  document.getElementById('re').onclick = () => { if (ws) ws.close(); term.reset(); connect(); };
  connect();
}
"""
    if request.query.get("embed"):
        body = "<style>nav{display:none!important}main{padding:0!important;max-width:none}#bar{display:none}#term{height:100vh!important;border:0!important;border-radius:0!important}</style>" + body
    return page("Terminal", "/debug/terminal", body, script)


async def python_redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound("/debug/ide")


# ───────────────────────────── system stats ─────────────────────────────
_proc = _psutil.Process() if _psutil else None
_net_prev: Optional[tuple] = None
_gpu_cache: tuple = (0.0, [])
if _psutil:
    _psutil.cpu_percent(None)
    _psutil.cpu_percent(None, percpu=True)
    _proc.cpu_percent(None)


def _gpu_query() -> List[Dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    kw: Dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3, **kw).stdout
    except Exception:
        return []

    def f(v: str):
        try:
            return float(v)
        except ValueError:
            return None
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 6:
            gpus.append({"name": p[0], "util": f(p[1]), "mem_used": f(p[2]), "mem_total": f(p[3]), "temp": f(p[4]), "power": f(p[5])})
    return gpus


def _bot_ns() -> Dict[str, Any]:
    m = sys.modules.get("__main__")
    return m.__dict__ if m is not None else {}


async def collect_stats() -> Dict[str, Any]:
    global _net_prev, _gpu_cache
    now = time.time()
    d: Dict[str, Any] = {"t": now}
    if _psutil is None:
        d["error"] = "psutil is not installed (pip install psutil)"
    else:
        vm, sw = _psutil.virtual_memory(), _psutil.swap_memory()
        d["cpu"] = {"percent": _psutil.cpu_percent(None), "cores": _psutil.cpu_percent(None, percpu=True), "count": _psutil.cpu_count()}
        d["ram"] = {"used": vm.used, "total": vm.total, "percent": vm.percent}
        d["swap"] = {"used": sw.used, "total": sw.total}
        try:
            du = _psutil.disk_usage(ROOT)
            d["disk"] = {"path": os.path.splitdrive(ROOT)[0] or "/", "used": du.used, "total": du.total, "percent": du.percent}
        except Exception:
            pass
        nio = _psutil.net_io_counters()
        if _net_prev and now > _net_prev[0]:
            dt = now - _net_prev[0]
            d["net"] = {"up": max(0, (nio.bytes_sent - _net_prev[1]) / dt), "down": max(0, (nio.bytes_recv - _net_prev[2]) / dt)}
        else:
            d["net"] = {"up": 0, "down": 0}
        _net_prev = (now, nio.bytes_sent, nio.bytes_recv)
        try:
            d["proc"] = {"cpu": _proc.cpu_percent(None), "rss": _proc.memory_info().rss, "threads": _proc.num_threads(),
                         "uptime": now - _proc.create_time(), "tasks": len(asyncio.all_tasks())}
        except Exception:
            pass
    if now - _gpu_cache[0] > 1.0:
        _gpu_cache = (now, await asyncio.get_running_loop().run_in_executor(None, _gpu_query))
    d["gpus"] = _gpu_cache[1]
    bot: Dict[str, Any] = {}
    try:
        ns = _bot_ns()
        client, gw = ns.get("client"), ns.get("gemini_ws")
        if client is not None:
            bot["guilds"] = len(client.guilds)
            lat = client.latency
            bot["latency_ms"] = None if lat != lat else round(lat * 1000)
        if gw is not None:
            bot["gemini_live"] = bool(gw.ws and gw.ws.protocol.state.name == "OPEN")
            bot["in_voice"] = bool(gw.is_voice_session)
    except Exception:
        pass
    d["bot"] = bot
    return d


async def api_stats(request: web.Request) -> web.Response:
    return web.json_response(await collect_stats(), headers={"Cache-Control": "no-store"})


# ───────────────────────────── python console (inside the bot) ─────────────────────────────
_exec_lock = asyncio.Lock()


def _fmt_exc(e: BaseException, tb_frames: bool = True) -> str:
    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "<debug-console>"]
    lines = (["Traceback (most recent call last):\n"] + traceback.format_list(frames)) if frames else []
    return "".join(lines + traceback.format_exception_only(type(e), e))


async def run_python(code: str) -> Dict[str, Any]:
    """Executes code in the globals of __main__ (the running bot). Top-level await works; the value of a trailing
    expression is returned like in a REPL. stdout/stderr printed meanwhile are captured (also anything other tasks
    print while an await is pending, so keep runs short)."""
    ns = _bot_ns()
    buf = io.StringIO()
    result: Optional[str] = None
    error: Optional[str] = None
    t0 = time.perf_counter()
    linecache.cache["<debug-console>"] = (len(code), None, code.splitlines(True), "<debug-console>")
    flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    async with _exec_lock:
        try:
            tree = ast.parse(code, "<debug-console>", "exec")
            last = ast.Expression(tree.body.pop().value) if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                if tree.body:
                    r = eval(compile(tree, "<debug-console>", "exec", flags=flags), ns)
                    if inspect.isawaitable(r):
                        await asyncio.wait_for(r, EXEC_TIMEOUT)
                if last is not None:
                    r = eval(compile(ast.fix_missing_locations(last), "<debug-console>", "eval", flags=flags), ns)
                    if inspect.isawaitable(r):
                        r = await asyncio.wait_for(r, EXEC_TIMEOUT)
                    if r is not None:
                        result = repr(r)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            error = "TimeoutError: no result after %ds (the coroutine was cancelled)" % EXEC_TIMEOUT
        except BaseException as e:   # SystemExit / KeyboardInterrupt from user code must not stop the bot
            error = _fmt_exc(e)
    out = buf.getvalue()
    return {"out": out[:OUT_LIMIT], "result": result[:OUT_LIMIT] if result else result, "error": error,
            "ms": round((time.perf_counter() - t0) * 1000)}


async def api_exec(request: web.Request) -> web.Response:
    if request.content_type != "application/json":
        return web.json_response({"error": "bad request"}, status=400)
    try:
        data = await request.json()
    except ValueError:
        return web.json_response({"error": "bad request"}, status=400)
    code = str(data.get("code", ""))
    console.log(f"[DebugWeb] python console: {code[:120]!r}", "INFO")
    return web.json_response(await run_python(code))


async def api_vars(request: web.Request) -> web.Response:
    q = request.query.get("q", "").lower()
    rows = []
    for k, v in list(_bot_ns().items()):
        if k.startswith("__") or (q and q not in k.lower()):
            continue
        try:
            r = reprlib.repr(v)
        except Exception:
            r = "<unprintable>"
        rows.append({"name": k, "type": type(v).__name__, "repr": r[:80]})
    rows.sort(key=lambda r: r["name"].lower())
    return web.json_response(rows[:500])


# ───────────────────────────── interactive terminal (PTY) ─────────────────────────────
def _shell_argv() -> List[str]:
    if os.name == "nt":
        for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(p):
                return [p, "--login", "-i"]
        w = shutil.which("bash")
        if w:
            return [w, "--login", "-i"]
        ps = shutil.which("powershell")
        if ps:
            return [ps, "-NoLogo"]
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return [shutil.which("bash") or "/bin/sh", "-i"]


class PtySession:
    """A real terminal: pywinpty (ConPTY) on Windows, the stdlib pty module elsewhere."""

    def __init__(self, argv: List[str], cwd: str, rows: int = 30, cols: int = 120) -> None:
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        self.win = os.name == "nt"
        self._closed = False
        if self.win:
            from winpty import PtyProcess   # pip install pywinpty
            self._p = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        else:
            import fcntl, pty, struct, termios
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

            def _child():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            try:
                self._proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                                              close_fds=True, preexec_fn=_child)
            finally:
                os.close(slave)
            self._master = master
            self._dec = codecs.getincrementaldecoder("utf-8")("replace")

    def read(self) -> Optional[str]:
        """Blocking. Returns the next output chunk, or None when the shell is gone."""
        try:
            if self.win:
                d = self._p.read(4096)
                return d if d else (None if not self._p.isalive() else "")
            d = os.read(self._master, 4096)
            return self._dec.decode(d) if d else None
        except (EOFError, OSError, ValueError):
            return None

    def write(self, s: str) -> None:
        if self._closed:
            return
        try:
            if self.win:
                self._p.write(s)
            else:
                os.write(self._master, s.encode("utf-8"))
        except (EOFError, OSError, ValueError):
            pass

    def resize(self, rows: int, cols: int) -> None:
        if self._closed or rows < 1 or cols < 1:
            return
        try:
            if self.win:
                self._p.setwinsize(rows, cols)
            else:
                import fcntl, struct, termios
                fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except (EOFError, OSError, ValueError):
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.win:
                pid = getattr(self._p, "pid", None)
                if pid:   # kill the whole tree (bash + whatever it started)
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    self._p.terminate(force=True)
                except Exception:
                    pass
                try:
                    self._p.close(force=True)
                except Exception:
                    pass
            else:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    os.close(self._master)
                except OSError:
                    pass
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
        except Exception as e:   # never let cleanup raise into the websocket handler
            console.log(f"[DebugWeb] terminal cleanup: {e}", "WARN")


async def ws_terminal(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
    await ws.prepare(request)

    def _int(name: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(request.query.get(name, default))))
        except ValueError:
            return default
    cols, rows = _int("cols", 120, 10, 500), _int("rows", 30, 3, 200)
    loop = asyncio.get_running_loop()
    try:
        sess = await loop.run_in_executor(None, lambda: PtySession(_shell_argv(), ROOT, rows, cols))
    except Exception as e:
        hint = "  (pip install pywinpty)" if os.name == "nt" and isinstance(e, ImportError) else ""
        await ws.send_str("\r\n\x1b[31mcould not start the shell: %s%s\x1b[0m\r\n" % (e, hint))
        await ws.close()
        return ws
    console.log(f"[DebugWeb] terminal opened from {request.remote}", "INFO")
    q: asyncio.Queue = asyncio.Queue()

    def _reader() -> None:   # daemon thread: blocking PTY reads
        while True:
            d = sess.read()
            loop.call_soon_threadsafe(q.put_nowait, d)
            if d is None:
                return
    threading.Thread(target=_reader, daemon=True, name="debug-pty-reader").start()

    async def _pump() -> None:
        while True:
            d = await q.get()
            if d is None:
                await ws.send_str("\r\n\x1b[33m[shell exited]\x1b[0m\r\n")
                await ws.close()
                return
            if d:
                await ws.send_str(d)
    pump = asyncio.ensure_future(_pump())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            t = data.get("type")
            if t == "input":
                sess.write(str(data.get("data", "")))
            elif t == "resize":
                try:
                    sess.resize(int(data["rows"]), int(data["cols"]))
                except (KeyError, TypeError, ValueError):
                    pass
    finally:
        pump.cancel()
        await loop.run_in_executor(None, sess.close)
    return ws


# ───────────────────────────── TensorBoard: metrics + server + proxy ─────────────────────────────
_tb: Dict[str, Any] = {"last": {}, "lat": None, "writer": None, "task": None, "proc": None, "port": None, "session": None, "log": None}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_HIST_LIMITS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 20, 30, 60, 1e12]   # seconds, response-latency buckets


def _histo(vals: List[float]):
    from tensorboard.compat.proto.summary_pb2 import HistogramProto
    counts = [0] * len(_HIST_LIMITS)
    for v in vals:
        for i, lim in enumerate(_HIST_LIMITS):
            if v <= lim:
                counts[i] += 1
                break
    return HistogramProto(min=min(vals), max=max(vals), num=len(vals), sum=sum(vals),
                          sum_squares=sum(v * v for v in vals), bucket_limit=_HIST_LIMITS, bucket=counts)


def _voice_scalars(st: Dict[str, Any], prev: Dict[str, Any], now: float) -> Dict[str, float]:
    """Tags under voice/ (the Call UI page shows exactly these). Rates need the previous sample, kept in prev."""
    m = _vm.metrics
    b = st.get("bot", {})
    live = bool(b.get("gemini_live")) and bool(b.get("in_voice"))
    if not live:
        m.clear_turn()
    out: Dict[str, float] = {
        "voice/gemini_last_msg_age_s": m.msg_age_s() if live else 0.0,
        "voice/unanswered_s": m.unanswered_s() if live else 0.0,
        "voice/gemini_ws_closes": m.gemini_ws_closes,
        "voice/gemini_ws_last_close_code": m.gemini_ws_last_close_code,
        "voice/disconnects": m.voice_disconnects,
        "voice/reconnects": m.voice_reconnects,
        "voice/connect_fails": m.voice_connect_fails,
        "voice/connect_last_fail_code": m.voice_last_fail_code,
        "voice/dave_dropped_frames": m.dave_drops,
        "voice/crypto_errors": m.crypto_errors,
        "voice/opus_errors": m.opus_errors,
        "voice/packets_lost": m.packets_lost,
    }
    t0 = prev.get("t")

    def rate(key: str, total: float, scale: float = 1.0) -> Optional[float]:
        p, prev[key] = prev.get(key), total
        if p is None or t0 is None or now <= t0:
            return None
        return max(0.0, (total - p) / (now - t0) / scale)

    gw = _bot_ns().get("gemini_ws")
    for tag, key, total, scale in (("voice/discord_rx_kb_s", "rx", m.discord_rx_bytes, 1024.0),
                                   ("voice/gemini_tx_kb_s", "tx", getattr(gw, "audio_tx_total", None), 1024.0),
                                   ("voice/window_frames_sent_per_s", "fs", m.frames_sent, 1.0)):
        if total is None:
            continue
        r = rate(key, total, scale)
        if r is not None:
            out[tag] = r
    n, avg_ms, max_ms, avg_kb = m.drain_render()
    if n and t0 is not None and now > t0:
        out["voice/window_render_ms"] = avg_ms
        out["voice/window_render_max_ms"] = max_ms
        out["voice/window_jpeg_kb"] = avg_kb
        out["voice/window_frames_rendered_per_s"] = n / (now - t0)
    prev["t"] = now
    return out


def _scalars(st: Dict[str, Any], audio_kbs: Optional[float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if "cpu" in st:
        out["cpu/percent"] = st["cpu"]["percent"]
    if "ram" in st:
        out["memory/percent"] = st["ram"]["percent"]
        out["memory/used_gb"] = st["ram"]["used"] / 2 ** 30
    if "net" in st:
        out["network/down_kb_s"] = st["net"]["down"] / 1024
        out["network/up_kb_s"] = st["net"]["up"] / 1024
    for i, g in enumerate(st.get("gpus", [])):
        for k, tag in (("util", "utilization_percent"), ("mem_used", "vram_mb"), ("temp", "temp_c"), ("power", "power_w")):
            if g.get(k) is not None:
                out["gpu%d/%s" % (i, tag)] = g[k]
    if "proc" in st:
        p = st["proc"]
        out["bot/cpu_percent"] = p["cpu"]
        out["bot/rss_mb"] = p["rss"] / 2 ** 20
        out["bot/threads"] = p["threads"]
        out["bot/asyncio_tasks"] = p["tasks"]
    b = st.get("bot", {})
    if b.get("latency_ms") is not None:
        out["bot/gateway_latency_ms"] = b["latency_ms"]
    if "gemini_live" in b:
        out["bot/gemini_live"] = 1.0 if b["gemini_live"] else 0.0
        out["bot/in_voice"] = 1.0 if b.get("in_voice") else 0.0
    if audio_kbs is not None:
        out["bot/gemini_audio_sent_kb_s"] = audio_kbs
    return out


async def _metrics_loop(writer) -> None:
    from tensorboard.compat.proto.event_pb2 import Event
    from tensorboard.compat.proto.summary_pb2 import Summary
    step, prev = 0, None
    vprev: Dict[str, Any] = {}
    lat_n, lat_hist = 0, []
    while True:
        try:
            st = await collect_stats()
            total = getattr(_bot_ns().get("gemini_ws"), "audio_tx_total", None)
            now = time.time()
            audio = None
            if total is not None and prev is not None and now > prev[0]:
                audio = max(0.0, (total - prev[1]) / (now - prev[0]) / 1024)
            if total is not None:
                prev = (now, total)
            vals = _scalars(st, audio)
            vals.update(_voice_scalars(st, vprev, now))
            _tb["last"] = vals
            # one point per answered turn (own step counter), plus the running latency histogram
            for wall, lat in _vm.metrics.pop_latencies():
                lat_n += 1
                lat_hist = (lat_hist + [lat])[-500:]
                _tb["lat"] = {"last": lat, "avg": sum(lat_hist) / len(lat_hist), "n": lat_n}
                writer.add_event(Event(wall_time=wall, step=lat_n, summary=Summary(value=[
                    Summary.Value(tag="voice/response_latency_s", simple_value=float(lat)),
                    Summary.Value(tag="voice/response_latency_hist", histo=_histo(lat_hist))])))
            if vals:
                writer.add_event(Event(wall_time=now, step=step, summary=Summary(
                    value=[Summary.Value(tag=k, simple_value=float(v)) for k, v in vals.items()])))
                step += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            console.log(f"[DebugWeb] metrics sample failed: {e}", "WARN")
        await asyncio.sleep(TB_INTERVAL)


def _prune_runs() -> None:
    try:
        runs = sorted(d for d in os.listdir(TB_LOGDIR) if d.startswith("run-") and os.path.isdir(os.path.join(TB_LOGDIR, d)))
        for d in runs[:-TB_KEEP_RUNS]:
            shutil.rmtree(os.path.join(TB_LOGDIR, d), ignore_errors=True)
    except OSError:
        pass


async def start_services() -> None:
    """Called with debug mode: start the metric writer and the TensorBoard subprocess."""
    if _tb["task"] is not None:
        return
    try:
        from tensorboard.summary.writer.event_file_writer import EventFileWriter
    except Exception as e:
        console.log(f"[DebugWeb] TensorBoard not available (pip install tensorboard): {e}", "WARN")
        return
    os.makedirs(TB_LOGDIR, exist_ok=True)
    _prune_runs()
    run = os.path.join(TB_LOGDIR, "run-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(run, exist_ok=True)
    _tb["run"] = os.path.basename(run)
    _vm.install_log_counter()
    writer = EventFileWriter(run, flush_secs=2)
    _tb["writer"] = writer
    _tb["task"] = asyncio.ensure_future(_metrics_loop(writer))
    port = _free_port()
    argv = [sys.executable, "-m", "tensorboard.main", "--logdir", TB_LOGDIR, "--host", "127.0.0.1", "--port", str(port),
            "--path_prefix", TB_PREFIX, "--reload_interval", "2"]
    kw: Dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        _tb["log"] = open(os.path.join(ROOT, "logs", "tensorboard.log"), "ab")
        _tb["proc"] = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=_tb["log"], stderr=subprocess.STDOUT, **kw)
        _tb["port"] = port
        console.log(f"[DebugWeb] TensorBoard starting on 127.0.0.1:{port} (served at {TB_PREFIX}/)", "INFO")
    except Exception as e:
        console.log(f"[DebugWeb] could not start TensorBoard: {e}", "WARN")


def tb_url(tag_filter: str = "") -> str:
    """TensorBoard URL (behind this panel's login), limited to the current run and, optionally, tags matching a regex."""
    q = []
    if tag_filter:
        q.append("tagFilter=" + quote(tag_filter, safe=""))
    if _tb.get("run"):
        q.append("runFilter=" + quote(_tb["run"], safe=""))
    return TB_PREFIX + "/" + ("?" + "&".join(q) if q else "") + "#timeseries"


async def api_voice(request: web.Request) -> web.Response:
    return web.json_response({"vals": _tb["last"], "lat": _tb["lat"]}, headers={"Cache-Control": "no-store"})


async def stop_services() -> None:
    _vm.remove_log_counter()
    task, _tb["task"] = _tb["task"], None
    if task is not None:
        task.cancel()
    writer, _tb["writer"] = _tb["writer"], None
    if writer is not None:
        try:
            await asyncio.get_running_loop().run_in_executor(None, writer.close)
        except Exception:
            pass
    proc, _tb["proc"], _tb["port"] = _tb["proc"], None, None
    if proc is not None and proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                proc.terminate()
            await asyncio.get_running_loop().run_in_executor(None, lambda: proc.wait(timeout=5))
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    log, _tb["log"] = _tb["log"], None
    if log is not None:
        log.close()
    sess, _tb["session"] = _tb["session"], None
    if sess is not None:
        await sess.close()


_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
        "upgrade", "host", "content-length", "content-encoding", "cookie", "accept-encoding"}


async def tb_proxy(request: web.Request) -> web.Response:
    """Reverse proxy to the local TensorBoard (it has no login of its own; auth_middleware already ran)."""
    return await _proxy_tb(request, request.raw_path)


async def tb_fonts(request: web.Request) -> web.Response:
    """TensorBoard's CSS asks for /font-roboto/... at the site root, outside its path prefix (public static font files)."""
    return await _proxy_tb(request, TB_PREFIX + request.raw_path)


async def _proxy_tb(request: web.Request, target: str) -> web.Response:
    port, proc = _tb["port"], _tb["proc"]
    if not port or proc is None:
        return web.Response(status=503, text="TensorBoard is not running (debug mode off, or 'tensorboard' is not installed).")
    if proc.poll() is not None:
        return web.Response(status=503, text="TensorBoard exited (code %s); see logs/tensorboard.log" % proc.returncode)
    if _tb["session"] is None:
        _tb["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers["Host"] = request.host
    try:
        async with _tb["session"].request(request.method, "http://127.0.0.1:%d%s" % (port, target), headers=headers,
                                          data=await request.read() or None, allow_redirects=False) as r:
            body = await r.read()
            out = {k: v for k, v in r.headers.items() if k.lower() not in _HOP}
            return web.Response(status=r.status, body=body, headers=out)
    except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
        return web.Response(status=503, text="TensorBoard is still starting, reload in a few seconds.")


# ───────────────────────────── wiring ─────────────────────────────
async def _on_startup(app: web.Application) -> None:
    await start_services()


async def _on_cleanup(app: web.Application) -> None:
    await stop_services()


def add_routes(app: web.Application) -> None:
    # TensorBoard + metrics follow the web server's life (runner.setup() / runner.cleanup()), debug_web.py needs no hooks
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    from arona.voice_engine.src import debug_db, debug_ide, debug_lsp
    debug_db.add_routes(app)
    debug_lsp.add_routes(app)
    debug_ide.add_routes(app)
    app.add_routes([
        web.get("/debug/api/voice", api_voice),
        web.get("/debug", home_page), web.get("/debug/", home_page),
        web.get("/debug/login", login_page), web.post("/debug/login", login_post), web.get("/debug/logout", logout),
        web.get("/debug/system", system_page), web.get("/debug/terminal", terminal_page), web.get("/debug/python", python_redirect),
        web.get("/debug/api/stats", api_stats), web.post("/debug/api/exec", api_exec), web.get("/debug/api/vars", api_vars),
        web.get("/debug/ws/terminal", ws_terminal),
        web.route("*", TB_PREFIX, tb_proxy), web.route("*", TB_PREFIX + "/{tail:.*}", tb_proxy),
        web.get("/font-roboto/{tail:.*}", tb_fonts),
    ])
