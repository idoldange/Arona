"""Debug-mode web server: live view of the voice-call UI (rendered up to 30 fps).

Started / stopped together with debug mode (console: debug start / debug stop, or debug_enabled at boot).
  GET /debug/call_ui        page that shows the stream
  GET /debug/call_ui.mjpg   raw MJPEG stream (multipart/x-mixed-replace)
  WS  /debug/call_audio     what the bot hears: raw 16 kHz mono PCM16 chunks (played by the page)
  /debug, /debug/system, /debug/terminal, /debug/python   see debug_panel.py
Every /debug/* route needs a login (same user / password as server.js: pass.txt -> pass.hash); see debug_panel.py.
"""
import asyncio
import io
from typing import Any, Optional

from aiohttp import web

from console import console
from arona.voice_engine.src import debug_panel

_runner: Optional[web.AppRunner] = None
_placeholder: Optional[bytes] = None

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Call UI debug</title>
<style>
  html, body { margin: 0; background: #111214; color: #949ba4; font: 14px system-ui, sans-serif; }
  body { display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
  img { max-width: 100vw; max-height: calc(100vh - 36px); object-fit: contain; background: #000; }
  p { margin: 8px; }
</style></head>
<body>
__NAV__
<img src="/debug/call_ui.mjpg" alt="voice call UI">
<p>Live voice-call UI (rendered up to 30 fps; model receives one screenshot per second) - debug mode only</p>
<p><button id="a">Listen to what the bot hears</button> <span id="s"></span></p>
<script>
const btn = document.getElementById('a'), st = document.getElementById('s');
let ctx = null, ws = null, t = 0;
btn.onclick = () => {
  if (ws) { ws.close(); return; }
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/debug/call_audio');
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { btn.textContent = 'Stop listening'; st.textContent = 'connected'; t = 0; };
  ws.onmessage = (e) => {
    const i16 = new Int16Array(e.data, 0, e.data.byteLength >> 1);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const buf = ctx.createBuffer(1, f32.length, 16000);
    buf.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(ctx.destination);
    const now = ctx.currentTime;
    if (t < now + 0.05) t = now + 0.1;
    src.start(t); t += buf.duration;
  };
  ws.onclose = () => {
    ws = null; btn.textContent = 'Listen to what the bot hears'; st.textContent = '';
    if (ctx) { ctx.close(); ctx = null; }
  };
};
</script>
</body></html>
"""


PAGE = PAGE.replace("__NAV__", debug_panel.nav_block("/debug/call_ui"))


def _no_call_jpeg() -> bytes:
    global _placeholder
    if _placeholder is None:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (1280, 720), (17, 18, 20))
        ImageDraw.Draw(im).text((40, 40), "No active voice call", fill=(148, 155, 164))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        _placeholder = buf.getvalue()
    return _placeholder


async def _stream(request: web.Request, window: Any) -> web.StreamResponse:
    resp = web.StreamResponse(headers={
        "Content-Type": "multipart/x-mixed-replace; boundary=frame",
        "Cache-Control": "no-store",
    })
    await resp.prepare(request)
    last_seq, idle = None, 0.0
    try:
        while True:
            if request.transport is None or request.transport.is_closing():
                break
            jpg, seq = window.latest_frame()
            # resend every ~5 s even if unchanged: that is how a closed tab gets noticed
            if seq != last_seq or idle >= 5.0:
                data = jpg or _no_call_jpeg()
                await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                last_seq, idle = seq, 0.0
            await asyncio.sleep(1 / 30)
            idle += 1 / 30
    except (ConnectionResetError, asyncio.CancelledError, ConnectionError):
        pass
    return resp


async def _audio_ws(request: web.Request, window: Any) -> web.WebSocketResponse:
    """Streams the audio the bot is hearing (the same chunks sent to Gemini Live)."""
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    gem = getattr(window, "ws", None)
    if gem is None or not hasattr(gem, "subscribe_audio"):
        await ws.close()
        return ws
    q = gem.subscribe_audio()

    async def _watch_client():
        async for _ in ws:   # returns when the tab closes
            pass

    watcher = asyncio.ensure_future(_watch_client())
    try:
        while not ws.closed and not watcher.done():
            try:
                data = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await ws.send_bytes(data)
    except (ConnectionResetError, ConnectionError):
        pass
    finally:
        gem.unsubscribe_audio(q)
        watcher.cancel()
    return ws


async def start(window: Any, host: str, port: int) -> None:
    global _runner
    if _runner is not None:
        return
    app = web.Application(middlewares=[debug_panel.auth_middleware])
    debug_panel.add_routes(app)

    async def page(request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    async def stream(request: web.Request) -> web.StreamResponse:
        return await _stream(request, window)

    async def audio(request: web.Request) -> web.WebSocketResponse:
        return await _audio_ws(request, window)

    app.add_routes([web.get("/debug/call_ui", page), web.get("/debug/call_ui.mjpg", stream),
                    web.get("/debug/call_audio", audio)])
    runner = web.AppRunner(app, handle_signals=False)
    try:
        await runner.setup()
        await web.TCPSite(runner, host, port, shutdown_timeout=1.0).start()
    except Exception as e:
        console.log(f"[DebugWeb] could not listen on {host}:{port}: {e}", "WARN")
        try:
            await runner.cleanup()
        except Exception:
            pass
        return
    _runner = runner
    console.log(f"[DebugWeb] voice UI at http://{host}:{port}/debug/call_ui", "INFO")


async def stop() -> None:
    global _runner
    runner, _runner = _runner, None
    if runner is not None:
        await runner.cleanup()
        console.log("[DebugWeb] stopped", "INFO")


async def sync(enabled: bool, window: Any, host: str, port: int) -> None:
    await (start(window, host, port) if enabled else stop())
