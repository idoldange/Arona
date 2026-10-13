"""Debug panel under /debug (served by debug_web.py): login, system stats, bash terminal, Python console inside the bot.

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
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

from aiohttp import web, WSMsgType

from console import console

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

NAV = [("/debug", "Home"), ("/debug/call_ui", "Call UI"), ("/debug/system", "System"),
       ("/debug/terminal", "Terminal"), ("/debug/python", "Python")]


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
    <p><a href="/debug/system">System</a> <span class="muted">- CPU / RAM / GPU / network</span></p>
    <p><a href="/debug/terminal">Terminal</a> <span class="muted">- run shell commands on this PC</span></p>
    <p><a href="/debug/python">Python</a> <span class="muted">- console running inside the bot process</span></p>
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
<div class="grid" id="top">
  <div class="card"><h3>CPU</h3><div class="big" id="cpuv">-</div><canvas id="cpuc"></canvas><div id="cores"></div></div>
  <div class="card"><h3>Memory</h3><div class="big" id="ramv">-</div><div class="muted" id="ramt"></div><canvas id="ramc"></canvas><div class="muted" id="swapt"></div></div>
  <div class="card"><h3>Network</h3><div class="row"><span>Down <b id="nd">-</b></span><span>Up <b id="nu">-</b></span></div><canvas id="netc"></canvas>
    <h3 style="margin-top:10px">Disk</h3><div class="bar"><i id="diskb" style="width:0"></i></div><div class="muted" id="diskt"></div></div>
  <div class="card"><h3>Bot process</h3><div id="proc" class="muted">-</div></div>
</div>
<div class="grid" id="gpus" style="margin-top:12px"></div>
<p class="muted" id="note"></p>"""
    script = r"""
const N = 120, $ = id => document.getElementById(id), H = {cpu: [], ram: [], net: []};
const B = n => n == null ? '-' : n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + ' GB' : n >= 2 ** 20 ? (n / 2 ** 20).toFixed(0) + ' MB' : (n / 1024).toFixed(0) + ' KB';
const push = (a, v) => { a.push(v == null ? 0 : v); if (a.length > N) a.shift(); };
function spark(cv, arr, color, max) {
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  g.strokeStyle = '#2b2d31'; g.lineWidth = 1; g.beginPath(); g.moveTo(0, h - .5); g.lineTo(w, h - .5); g.stroke();
  if (arr.length < 2) return;
  const m = max || Math.max(1, ...arr), step = w / (N - 1);
  g.beginPath();
  arr.forEach((v, i) => { const x = w - (arr.length - 1 - i) * step, y = h - 2 - (h - 4) * Math.min(v, m) / m; i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.strokeStyle = color; g.lineWidth = 1.5; g.stroke();
}
function bar(parent, i) {
  const b = document.createElement('div'); b.className = 'bar'; const f = document.createElement('i'); b.appendChild(f); parent.appendChild(b); return f;
}
const coreBars = [], gpuCards = [];
function gpuCard(i, name) {
  if (gpuCards[i]) return gpuCards[i];
  const card = document.createElement('div'); card.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'GPU ' + i + ' - ' + name; card.appendChild(h);
  const big = document.createElement('div'); big.className = 'big'; card.appendChild(big);
  const cv = document.createElement('canvas'); card.appendChild(cv);
  const vb = bar(card, 0); const vt = document.createElement('div'); vt.className = 'muted'; card.appendChild(vt);
  $('gpus').appendChild(card);
  return gpuCards[i] = {big, cv, vb, vt, hist: []};
}
async function tick() {
  let s;
  try { const r = await fetch('/debug/api/stats'); if (r.status === 401) { location = '/debug/login?next=/debug/system'; return; } s = await r.json(); } catch (e) { return; }
  if (s.error) $('note').textContent = s.error;
  if (s.cpu) {
    $('cpuv').textContent = s.cpu.percent.toFixed(0) + '%  (' + s.cpu.count + ' threads)';
    push(H.cpu, s.cpu.percent); spark($('cpuc'), H.cpu, '#5865f2', 100);
    s.cpu.cores.forEach((v, i) => { if (!coreBars[i]) coreBars[i] = bar($('cores'), i); coreBars[i].style.width = v + '%'; });
  }
  if (s.ram) {
    $('ramv').textContent = s.ram.percent.toFixed(0) + '%'; $('ramt').textContent = B(s.ram.used) + ' / ' + B(s.ram.total);
    push(H.ram, s.ram.percent); spark($('ramc'), H.ram, '#23a55a', 100);
    $('swapt').textContent = 'swap ' + B(s.swap.used) + ' / ' + B(s.swap.total);
  }
  if (s.net) { $('nd').textContent = B(s.net.down) + '/s'; $('nu').textContent = B(s.net.up) + '/s'; push(H.net, s.net.down + s.net.up); spark($('netc'), H.net, '#f0b232'); }
  if (s.disk) { $('diskb').style.width = s.disk.percent + '%'; $('diskt').textContent = s.disk.path + '  ' + B(s.disk.used) + ' / ' + B(s.disk.total); }
  if (s.proc) { const p = s.proc; $('proc').textContent = 'CPU ' + p.cpu.toFixed(0) + '%   RAM ' + B(p.rss) + '   threads ' + p.threads + '   asyncio tasks ' + p.tasks; }
  (s.gpus || []).forEach((g, i) => {
    const c = gpuCard(i, g.name);
    c.big.textContent = (g.util == null ? '-' : g.util.toFixed(0)) + '%' + (g.temp == null ? '' : '   ' + g.temp.toFixed(0) + ' C') + (g.power == null ? '' : '   ' + g.power.toFixed(0) + ' W');
    push(c.hist, g.util); spark(c.cv, c.hist, '#f23f43', 100);
    const vp = g.mem_total ? 100 * g.mem_used / g.mem_total : 0; c.vb.style.width = vp + '%';
    c.vt.textContent = 'VRAM ' + (g.mem_used == null ? '-' : g.mem_used.toFixed(0)) + ' / ' + (g.mem_total == null ? '-' : g.mem_total.toFixed(0)) + ' MiB';
  });
  if (!(s.gpus || []).length && !s.error) $('note').textContent = 'No NVIDIA GPU / nvidia-smi found.';
}
setInterval(tick, 1000); tick();
"""
    return page("System", "/debug/system", body, script)


async def terminal_page(request: web.Request) -> web.Response:
    body = r"""
<div class="muted" id="info">connecting...</div>
<div class="term"><pre id="out"></pre></div>
<div class="line"><span id="pr" class="muted"></span><input id="cmd" autocomplete="off" autofocus placeholder="command  (Up/Down = history, Ctrl+C = kill)">
<button class="alt" id="kill">Kill</button><button class="alt" id="clr">Clear</button></div>"""
    script = r"""
const out = document.getElementById('out'), inp = document.getElementById('cmd'), pr = document.getElementById('pr'), info = document.getElementById('info');
const wrap = out.parentElement; let ws, hist = [], hi = 0, busy = false;
function add(s) {
  const bottom = wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 30;
  out.appendChild(document.createTextNode(s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '')));
  while (out.childNodes.length > 3000) out.removeChild(out.firstChild);
  if (bottom) wrap.scrollTop = wrap.scrollHeight;
}
function connect() {
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/debug/ws/shell');
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === 'hello') { info.textContent = 'shell: ' + m.shell; pr.textContent = m.cwd + ' $'; }
    else if (m.type === 'out') add(m.data);
    else if (m.type === 'exit') { busy = false; pr.textContent = m.cwd + ' $'; if (m.code) add('[exit ' + m.code + ']\n'); }
  };
  ws.onclose = () => { add('\n[disconnected - reload the page]\n'); info.textContent = 'disconnected'; };
}
const kill = () => ws && ws.readyState === 1 && ws.send(JSON.stringify({type: 'kill'}));
document.getElementById('kill').onclick = kill;
document.getElementById('clr').onclick = () => { out.textContent = ''; };
inp.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const c = inp.value; if (!c.trim() || !ws || ws.readyState !== 1) return;
    hist.push(c); hi = hist.length; add(pr.textContent + ' ' + c + '\n');
    ws.send(JSON.stringify({type: 'run', cmd: c})); inp.value = ''; busy = true;
  } else if (e.key === 'ArrowUp') { if (hi > 0) inp.value = hist[--hi]; e.preventDefault(); }
  else if (e.key === 'ArrowDown') { hi = Math.min(hist.length, hi + 1); inp.value = hist[hi] || ''; e.preventDefault(); }
  else if (e.key === 'c' && e.ctrlKey && busy) { kill(); e.preventDefault(); }
});
connect();
"""
    return page("Terminal", "/debug/terminal", body, script)


async def python_page(request: web.Request) -> web.Response:
    body = r"""
<div class="muted">Runs inside the bot process, in the globals of main.py (client, gemini_ws, os, ...). Top-level <code>await</code> works.
Blocking code blocks the bot - use <code>await asyncio.to_thread(fn)</code> for slow things. Ctrl+Enter = run.</div>
<div style="display:flex;gap:12px;margin-top:8px;align-items:flex-start;flex-wrap:wrap">
  <div style="flex:1;min-width:320px">
    <div class="term" style="height:calc(100vh - 330px)"><pre id="out"></pre></div>
    <textarea id="code" rows="5" style="width:100%;margin-top:8px" placeholder="code..."></textarea>
    <div class="line"><button id="run">Run</button><button class="alt" id="clr">Clear</button><span class="muted" id="st"></span></div>
  </div>
  <div class="card" style="width:300px;max-height:calc(100vh - 150px);overflow:auto"><h3>Variables</h3>
    <div class="line" style="margin:0 0 6px"><input id="q" placeholder="filter"><button class="alt" id="rv">Refresh</button></div>
    <div id="vars" class="muted"></div></div>
</div>"""
    script = r"""
const out = document.getElementById('out'), code = document.getElementById('code'), run = document.getElementById('run'), st = document.getElementById('st');
const wrap = out.parentElement; let hist = [], hi = 0;
function add(s) { out.appendChild(document.createTextNode(s)); wrap.scrollTop = wrap.scrollHeight; }
async function exec() {
  const src = code.value; if (!src.trim()) return;
  hist.push(src); hi = hist.length; run.disabled = true; st.textContent = 'running...';
  add('>>> ' + src.split('\n').join('\n... ') + '\n');
  try {
    const r = await fetch('/debug/api/exec', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: src})});
    if (r.status === 401) { location = '/debug/login?next=/debug/python'; return; }
    const j = await r.json();
    if (j.out) add(j.out.endsWith('\n') ? j.out : j.out + '\n');
    if (j.result != null) add(j.result + '\n');
    if (j.error) add(j.error + '\n');
    st.textContent = j.ms + ' ms';
  } catch (e) { add('request failed: ' + e + '\n'); st.textContent = ''; }
  run.disabled = false; code.value = ''; code.focus();
}
run.onclick = exec;
document.getElementById('clr').onclick = () => { out.textContent = ''; };
code.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); exec(); }
  else if (e.key === 'ArrowUp' && e.altKey) { if (hi > 0) code.value = hist[--hi]; e.preventDefault(); }
  else if (e.key === 'ArrowDown' && e.altKey) { hi = Math.min(hist.length, hi + 1); code.value = hist[hi] || ''; e.preventDefault(); }
});
async function vars() {
  const box = document.getElementById('vars'); box.textContent = 'loading...';
  try {
    const rows = await (await fetch('/debug/api/vars?q=' + encodeURIComponent(document.getElementById('q').value))).json();
    box.textContent = '';
    rows.forEach(v => {
      const d = document.createElement('div'); d.style.cssText = 'padding:2px 0;border-bottom:1px solid #2b2d31;cursor:pointer';
      const n = document.createElement('b'); n.style.color = '#dbdee1'; n.textContent = v.name; const t = document.createElement('span'); t.textContent = '  ' + v.type;
      const r = document.createElement('div'); r.textContent = v.repr; r.style.fontSize = '12px'; d.append(n, t, r);
      d.onclick = () => { code.value += v.name; code.focus(); }; box.appendChild(d);
    });
  } catch (e) { box.textContent = 'failed'; }
}
document.getElementById('rv').onclick = vars; document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') vars(); });
vars();
"""
    return page("Python", "/debug/python", body, script)


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


def _fmt_exc(e: BaseException) -> str:
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


# ───────────────────────────── bash terminal ─────────────────────────────
def _shell_argv(cmd: str) -> List[str]:
    if os.name == "nt":
        for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(p):
                return [p, "-c", cmd]
        w = shutil.which("bash")
        if w:
            return [w, "-c", cmd]
        ps = shutil.which("powershell")
        if ps:
            return [ps, "-NoProfile", "-Command", cmd]
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", cmd]
    return [shutil.which("bash") or "/bin/sh", "-c", cmd]


def _shell_label() -> str:
    return _shell_argv("")[0]


async def _kill(state: Dict[str, Any]) -> None:
    proc = state.get("proc")
    if proc is None or proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10))
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def _run_command(ws: web.WebSocketResponse, state: Dict[str, Any], cmd: str) -> None:
    async def send(obj):
        try:
            await ws.send_json(obj)
        except (ConnectionResetError, RuntimeError):
            pass
    s = cmd.strip()
    code = 0
    try:
        if s == "cd" or (s.startswith("cd ") and not any(c in s for c in "&;|<>\n")):
            target = s[2:].strip().strip("\"'") or ROOT
            path = os.path.abspath(os.path.join(state["cwd"], os.path.expanduser(target)))
            if os.path.isdir(path):
                state["cwd"] = path
            else:
                code = 1
                await send({"type": "out", "data": "cd: no such directory: %s\n" % target})
        elif s:
            kw: Dict[str, Any] = {} if os.name == "nt" else {"start_new_session": True}
            try:
                proc = await asyncio.create_subprocess_exec(
                    *_shell_argv(cmd), cwd=state["cwd"], env=dict(os.environ, PYTHONUNBUFFERED="1"),
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kw)
            except Exception as e:
                code = 1
                await send({"type": "out", "data": "failed to start: %s\n" % e})
            else:
                state["proc"] = proc
                dec = codecs.getincrementaldecoder("utf-8")("replace")
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    await send({"type": "out", "data": dec.decode(chunk)})
                code = await proc.wait()
    finally:
        state["proc"] = None
    await send({"type": "exit", "code": code, "cwd": state["cwd"]})   # always: the page re-enables its prompt on this


async def ws_shell(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
    await ws.prepare(request)
    state: Dict[str, Any] = {"cwd": ROOT, "proc": None, "task": None}
    await ws.send_json({"type": "hello", "shell": _shell_label(), "cwd": state["cwd"]})
    console.log(f"[DebugWeb] terminal opened from {request.remote}", "INFO")
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            t = data.get("type")
            if t == "run":
                task = state["task"]
                if task is not None and not task.done():
                    await ws.send_json({"type": "out", "data": "[busy] a command is still running (Ctrl+C / Kill)\n"})
                    continue
                state["task"] = asyncio.ensure_future(_run_command(ws, state, str(data.get("cmd", ""))[:20000]))
            elif t == "kill":
                await _kill(state)
    finally:
        await _kill(state)
        task = state.get("task")
        if task is not None and not task.done():
            task.cancel()
    return ws


# ───────────────────────────── wiring ─────────────────────────────
def add_routes(app: web.Application) -> None:
    app.add_routes([
        web.get("/debug", home_page), web.get("/debug/", home_page),
        web.get("/debug/login", login_page), web.post("/debug/login", login_post), web.get("/debug/logout", logout),
        web.get("/debug/system", system_page), web.get("/debug/terminal", terminal_page), web.get("/debug/python", python_page),
        web.get("/debug/api/stats", api_stats), web.post("/debug/api/exec", api_exec), web.get("/debug/api/vars", api_vars),
        web.get("/debug/ws/shell", ws_shell),
    ])
