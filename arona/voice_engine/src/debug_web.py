"""Debug-mode web server: live view of the voice-call UI (rendered up to 30 fps).

Started / stopped together with debug mode (console: debug start / debug stop, or debug_enabled at boot).
  GET /debug/call_ui        page that shows the stream
  GET /debug/call_ui.mjpg   raw MJPEG stream (multipart/x-mixed-replace)
"""
import asyncio
import io
from typing import Any, Optional

from aiohttp import web

from console import console

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
<img src="/debug/call_ui.mjpg" alt="voice call UI">
<p>Live voice-call UI (rendered up to 30 fps; model receives one screenshot per second) - debug mode only</p>
</body></html>
"""


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


async def start(window: Any, host: str, port: int) -> None:
    global _runner
    if _runner is not None:
        return
    app = web.Application()

    async def page(request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    async def stream(request: web.Request) -> web.StreamResponse:
        return await _stream(request, window)

    app.add_routes([web.get("/debug/call_ui", page), web.get("/debug/call_ui.mjpg", stream)])
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
