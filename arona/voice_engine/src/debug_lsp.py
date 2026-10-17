"""Support for the /debug/ide editor: Monaco files + a WebSocket bridge to Pyright.

Monaco is the editor core of VS Code. Pylance itself is closed source and licensed for VS Code only, so the language server is
Pyright (MIT), the engine Pylance is built on: completions, hover, signature help and diagnostics.
Both are npm dependencies of the project root (package.json -> npm install in the project folder). /debug/ws/lsp spawns one `pyright-langserver --stdio`
per open page and relays LSP JSON between the browser and its stdin / stdout; requests the server sends to the client
(configuration, capability registration, progress) are answered here. Same login as the rest of /debug (auth_middleware).
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys

from aiohttp import web, WSMsgType

from console import console

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MONACO = os.path.realpath(os.path.join(ROOT, "node_modules", "monaco-editor", "min"))
SERVER = os.path.join(ROOT, "node_modules", "pyright", "langserver.index.js")
MAX_SESSIONS = 3
_TYPES = {".js": "application/javascript", ".css": "text/css", ".ttf": "font/ttf", ".json": "application/json", ".map": "application/json"}
_sessions: set = set()


def workspace_uri() -> str:
    """The project root is the Pyright workspace (so project imports resolve); scanning it is avoided with python.analysis.exclude (see _cfg)."""
    import pathlib
    return pathlib.Path(ROOT).as_uri()


def available() -> bool:
    return os.path.isfile(SERVER) and os.path.isdir(MONACO) and shutil.which("node") is not None


async def monaco_static(request: web.Request) -> web.StreamResponse:
    p = os.path.realpath(os.path.join(MONACO, request.match_info["tail"]))
    if not p.startswith(MONACO + os.sep) or not os.path.isfile(p):
        raise web.HTTPNotFound()
    h = {"Cache-Control": "public, max-age=86400"}
    ct = _TYPES.get(os.path.splitext(p)[1].lower())
    if ct:
        h["Content-Type"] = ct
    return web.FileResponse(p, headers=h)


def _frame(obj) -> bytes:
    b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(b) + b


async def _messages(stream):
    while True:
        n = None
        while True:
            line = await stream.readline()
            if not line:
                return
            line = line.strip()
            if not line:
                break
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":")[1])
        if n is not None:
            yield json.loads(await stream.readexactly(n))


def _cfg(section):
    if section == "python":
        return {"pythonPath": sys.executable}                      # imports resolve against the interpreter that runs the bot
    if section == "python.analysis":
        return {"typeCheckingMode": "basic", "diagnosticMode": "openFilesOnly", "autoSearchPaths": True, "useLibraryCodeForTypes": True,
                "extraPaths": [ROOT.replace("\\", "/")], "exclude": ["**"]}      # exclude **: nothing is crawled, only the open files are analysed
    return {} if section == "pyright" else None


async def _kill(proc) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            await asyncio.get_running_loop().run_in_executor(None, lambda: subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW))
        else:
            proc.kill()
    except Exception:
        pass


async def ws_lsp(request: web.Request) -> web.StreamResponse:
    if not available() or len(_sessions) >= MAX_SESSIONS:
        return web.Response(status=503, text="Pyright is not installed (run npm install in the project folder) or too many editors are open.")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=16 * 2 ** 20)
    await ws.prepare(request)
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        proc = await asyncio.create_subprocess_exec(shutil.which("node"), SERVER, "--stdio", cwd=ROOT, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **kw)
    except OSError as e:
        console.log(f"[DebugWeb] could not start pyright: {e}", "WARN")
        await ws.close()
        return ws
    _sessions.add(proc)

    async def to_browser() -> None:
        try:
            async for m in _messages(proc.stdout):
                if "method" in m and "id" in m:                     # server -> client request: answer it here
                    res = [_cfg(i.get("section")) for i in m["params"]["items"]] if m["method"] == "workspace/configuration" else None
                    proc.stdin.write(_frame({"jsonrpc": "2.0", "id": m["id"], "result": res}))
                    await proc.stdin.drain()
                elif not ws.closed:
                    await ws.send_str(json.dumps(m, ensure_ascii=False))
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            if not ws.closed:
                await ws.close()

    task = asyncio.ensure_future(to_browser())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                proc.stdin.write(_frame(json.loads(msg.data)))
                await proc.stdin.drain()
    except (ConnectionError, ValueError):
        pass
    finally:
        task.cancel()
        _sessions.discard(proc)
        await _kill(proc)
    return ws


def add_routes(app: web.Application) -> None:
    app.add_routes([web.get("/debug/static/monaco/{tail:.*}", monaco_static), web.get("/debug/ws/lsp", ws_lsp)])
