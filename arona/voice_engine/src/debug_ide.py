"""/debug/ide: a VS Code-like web IDE for the bot's own source (Monaco + Pyright, explorer, tabs, search, console, terminal).

Backend = a small file API confined to the project root + hot reload (debug_hot): Ctrl+S writes the file atomically (first write per
run is backed up under backup/ide/<run>/) and patches the running bot, so no restart is needed. Same login as the rest of /debug.
"""
import asyncio
import json
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional

from aiohttp import web

from console import console
from arona.voice_engine.src import debug_hot

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "ide")
BACKUP = os.path.join(ROOT, "backup", "ide", time.strftime("%Y%m%d-%H%M%S"))
TREE_HIDE = {"__pycache__", ".git"}
SKIP = {"node_modules", ".git", "__pycache__", "backup", "logs", "temp", "temp_audio", "crashreports", "server-logs", ".mcp_undo", "site-packages", ".cache", ".venv", "venv"}
BINEXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mkv", ".zip", ".7z", ".rar", ".gz",
          ".exe", ".dll", ".pyd", ".so", ".db", ".sqlite", ".sqlite3", ".ttf", ".otf", ".woff", ".woff2", ".pdf", ".class", ".whl", ".pyc",
          ".bin", ".npy", ".pt", ".onnx", ".sf2", ".ogg", ".lock", ".chroma"}
MAX_OPEN = 3_000_000
MAX_SEARCH_FILE = 1_000_000
_backed: set = set()
_files_cache: Dict[str, Any] = {"t": 0.0, "v": []}


def _norm(p: str) -> str:
    return os.path.normcase(p)


def _resolve(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    p = os.path.realpath(os.path.join(ROOT, rel))
    if _norm(p) != _norm(ROOT) and not _norm(p).startswith(_norm(ROOT) + os.sep):
        raise web.HTTPForbidden(text="outside the project")
    return p


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def _ms(p: str) -> int:
    return int(os.path.getmtime(p) * 1000)


def _is_venv(d: str) -> bool:      # virtualenvs and embedded python runtimes (thousands of files nobody edits here)
    return (os.path.isfile(os.path.join(d, "pyvenv.cfg")) or os.path.isfile(os.path.join(d, "python.exe"))
            or os.path.isdir(os.path.join(d, "Lib", "site-packages")) or os.path.isdir(os.path.join(d, "lib", "site-packages")))


_RESERVED = {"nul", "con", "prn", "aux"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}      # Windows device names (a stray file called "nul" exists)


def _walk():
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP and d.lower() not in _RESERVED and not _is_venv(os.path.join(dp, d))]
        for f in fns:
            if os.path.splitext(f)[1].lower() not in BINEXT and os.path.splitext(f)[0].lower() not in _RESERVED:
                yield os.path.join(dp, f)


async def _json(request: web.Request) -> Dict[str, Any]:
    if request.content_type != "application/json":
        raise web.HTTPBadRequest(text="json only")
    try:
        d = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="bad json")
    return d if isinstance(d, dict) else {}


# ───────────────────────────── file api ─────────────────────────────
async def api_tree(request: web.Request) -> web.Response:
    p = _resolve(request.query.get("path", ""))
    if not os.path.isdir(p):
        raise web.HTTPNotFound()
    out = []
    try:
        for e in os.scandir(p):
            if e.name in TREE_HIDE or e.name.lower().split(".")[0] in _RESERVED:
                continue
            try:
                d = e.is_dir()
                out.append({"n": e.name, "d": d, "x": d and (e.name in SKIP or _is_venv(e.path))})
            except OSError:
                pass
    except OSError as ex:
        raise web.HTTPForbidden(text=str(ex))
    out.sort(key=lambda r: (not r["d"], r["n"].lower()))
    return web.json_response(out)


async def api_files(request: web.Request) -> web.Response:
    if time.time() - _files_cache["t"] > 20 or request.query.get("fresh"):
        v = await asyncio.get_running_loop().run_in_executor(None, lambda: [_rel(p) for p in _walk()][:40000])
        _files_cache.update(t=time.time(), v=v)
    return web.json_response(_files_cache["v"])


async def api_read(request: web.Request) -> web.Response:
    p = _resolve(request.query.get("path", ""))
    if not os.path.isfile(p):
        raise web.HTTPNotFound()
    size = os.path.getsize(p)
    if size > MAX_OPEN:
        return web.json_response({"error": "file is larger than %d MB" % (MAX_OPEN // 1_000_000)})
    raw = await asyncio.get_running_loop().run_in_executor(None, lambda: open(p, "rb").read())
    if b"\0" in raw[:8192]:
        return web.json_response({"error": "binary file"})
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return web.json_response({"error": "not a UTF-8 text file"})
    return web.json_response({"content": text, "mtime": _ms(p), "bom": bom, "size": size,
                              "loaded": p.lower().endswith(".py") and bool(debug_hot.modules_for(p))},
                             headers={"Cache-Control": "no-store"})


async def api_stat(request: web.Request) -> web.Response:
    d = await _json(request)
    out = {}
    for rel in list(d.get("paths") or [])[:200]:
        try:
            p = _resolve(str(rel))
            out[rel] = _ms(p) if os.path.isfile(p) else None
        except web.HTTPException:
            out[rel] = None
    return web.json_response(out)


def _write_atomic(p: str, data: bytes) -> None:
    tmp = p + ".ide_tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        with open(p, "wb") as f:           # destination locked for replace (Windows): write in place
            f.write(data)


def _backup_once(p: str) -> None:
    if p in _backed or not os.path.isfile(p):
        return
    _backed.add(p)
    dst = os.path.join(BACKUP, _rel(p))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(p, dst)


async def api_save(request: web.Request) -> web.Response:
    d = await _json(request)
    p = _resolve(str(d.get("path", "")))
    content = d.get("content")
    if not isinstance(content, str):
        raise web.HTTPBadRequest(text="content missing")
    write, apply = bool(d.get("write", True)), bool(d.get("apply", False))
    is_py = p.lower().endswith(".py")
    loop = asyncio.get_running_loop()
    res: Dict[str, Any] = {"ok": True}
    exists = os.path.isfile(p)
    disk_before: Optional[str] = None
    if exists and is_py:
        try:
            raw = await loop.run_in_executor(None, lambda: open(p, "rb").read())
            if os.path.getmtime(p) <= debug_hot.PROC_START:
                disk_before = raw.decode("utf-8-sig", errors="replace")
        except OSError:
            pass
    err = debug_hot.check_syntax(content, p) if is_py and (write or apply) else None
    if err:
        res["syntax"] = err
    if write:
        if exists and not d.get("force") and d.get("mtime") is not None and abs(_ms(p) - int(d["mtime"])) > 2:
            return web.json_response({"ok": False, "conflict": True, "mtime": _ms(p)}, status=409)
        data = (b"\xef\xbb\xbf" if d.get("bom") else b"") + content.encode("utf-8")
        await loop.run_in_executor(None, lambda: (_backup_once(p), os.makedirs(os.path.dirname(p), exist_ok=True), _write_atomic(p, data)))
        res["mtime"] = _ms(p)
        _files_cache["t"] = 0
        console.log(f"[IDE] saved {_rel(p)} ({len(data)} bytes)", "INFO")
    if apply and is_py and not err:
        try:
            res["hot"] = await debug_hot.apply(p, content, disk_before)
        except Exception as e:
            res["hot"] = {"error": "%s: %s" % (type(e).__name__, e)}
        h = res["hot"]
        console.log(f"[IDE] hot reload {_rel(p)}: patched={h.get('patched')} added={h.get('added')} skipped={h.get('skipped')}", "INFO")
    return web.json_response(res)


async def api_fs(request: web.Request) -> web.Response:
    d = await _json(request)
    op = d.get("op")
    p = _resolve(str(d.get("path", "")))
    if _norm(p) == _norm(ROOT):
        raise web.HTTPForbidden(text="no")
    loop = asyncio.get_running_loop()
    if op == "new_file":
        if os.path.exists(p):
            return web.json_response({"error": "already exists"}, status=409)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").close()
    elif op == "new_dir":
        if os.path.exists(p):
            return web.json_response({"error": "already exists"}, status=409)
        os.makedirs(p)
    elif op == "rename":
        q = _resolve(str(d.get("to", "")))
        if os.path.exists(q):
            return web.json_response({"error": "target exists"}, status=409)
        os.makedirs(os.path.dirname(q), exist_ok=True)
        os.rename(p, q)
    elif op == "delete":                      # never really deleted: moved to backup/ide/<run>/_deleted
        if not os.path.exists(p):
            raise web.HTTPNotFound()
        dst = os.path.join(BACKUP, "_deleted", _rel(p))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        await loop.run_in_executor(None, lambda: shutil.move(p, dst))
    else:
        raise web.HTTPBadRequest(text="bad op")
    _files_cache["t"] = 0
    console.log(f"[IDE] {op} {_rel(p)}", "INFO")
    return web.json_response({"ok": True})


def _search(q: str, regex: bool, case: bool, limit: int) -> List[Dict[str, Any]]:
    pat = re.compile(q if regex else re.escape(q), 0 if case else re.I)
    out, total = [], 0
    for p in _walk():
        try:
            if os.path.getsize(p) > MAX_SEARCH_FILE:
                continue
            raw = open(p, "rb").read()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue
        hits = []
        for i, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            m = pat.search(line)
            if m:
                s = max(0, m.start() - 40)
                hits.append([i, m.start() + 1, m.end() - m.start(), line[s:s + 220], m.start() - s])
                total += 1
                if total >= limit or len(hits) >= 100:
                    break
        if hits:
            out.append({"p": _rel(p), "m": hits})
        if total >= limit:
            break
    return out


async def api_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "")
    if not q:
        return web.json_response([])
    try:
        r = await asyncio.get_running_loop().run_in_executor(
            None, _search, q, request.query.get("re") == "1", request.query.get("case") == "1", 600)
    except re.error as e:
        return web.json_response({"error": "bad regex: %s" % e})
    return web.json_response(r)


# ───────────────────────────── page ─────────────────────────────
async def static(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if name not in ("ide.js", "ide.css"):
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-cache"})


async def ide_page(request: web.Request) -> web.Response:
    from arona.voice_engine.src import debug_lsp, debug_panel
    if not debug_lsp.available():
        return debug_panel.page("IDE", "/debug/ide", '<div class="card">The editor files are missing. Run <code>npm install</code> in the project folder '
                                "(Monaco + Pyright are in package.json), then reload.</div>")
    cfg = {"root": ROOT.replace("\\", "/"), "lspRoot": debug_lsp.workspace_uri(), "name": os.path.basename(ROOT)}
    html = ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>IDE - Arona debug</title><style>" + debug_panel.NAV_CSS + "</style>"
            "<link rel=\"stylesheet\" href=\"/debug/static/ide/ide.css\"></head><body>" + debug_panel.nav_block("/debug/ide", False)
            + "<div id=\"app\"></div><script>window.IDE=" + json.dumps(cfg) + ";</script>"
            "<script src=\"/debug/static/monaco/vs/loader.js\"></script><script src=\"/debug/static/ide/ide.js\"></script></body></html>")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


def add_routes(app: web.Application) -> None:
    app.add_routes([
        web.get("/debug/ide", ide_page), web.get("/debug/static/ide/{name}", static),
        web.get("/debug/api/ide/tree", api_tree), web.get("/debug/api/ide/files", api_files), web.get("/debug/api/ide/read", api_read),
        web.get("/debug/api/ide/search", api_search), web.post("/debug/api/ide/stat", api_stat),
        web.post("/debug/api/ide/save", api_save), web.post("/debug/api/ide/fs", api_fs),
    ])
