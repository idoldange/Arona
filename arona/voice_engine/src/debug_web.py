"""Debug-mode web server: live view of the voice-call UI (rendered up to 30 fps).

Started / stopped together with debug mode (console: debug start / debug stop, or debug_enabled at boot).
  GET /debug/call_ui        page that shows the stream
  GET /debug/call_ui.mjpg   raw MJPEG stream (multipart/x-mixed-replace)
  WS  /debug/call_audio     what the bot hears: raw 16 kHz mono PCM16 chunks (played by the page)
  /debug, /debug/system, /debug/terminal, /debug/ide      see debug_panel.py
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
  body { min-height: 100vh; }
  .wrap { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; padding: 12px; }
  @media (min-width: 1250px) { .wrap { grid-template-columns: minmax(0, 7fr) minmax(0, 6fr); } iframe { position: sticky; top: 12px; height: calc(100vh - 80px) !important; } }
  img { width: 100%; height: auto; display: block; background: #000; border-radius: 8px; }
  p { margin: 6px 0; }
  iframe { display: block; width: 100%; height: 78vh; min-height: 480px; border: 1px solid #2b2d31; border-radius: 8px; background: #202124; }
  #chips { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin-bottom: 12px; }
  .chip { background: #1a1b1e; border: 1px solid #2b2d31; border-left: 4px solid #23a55a; border-radius: 8px; padding: 6px 10px; }
  .chip small { display: block; color: #949ba4; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  .chip b { font-size: 18px; color: #dbdee1; font-weight: 600; }
  .chip.l1 { border-left-color: #f0b232; } .chip.l2 { border-left-color: #f23f43; } .chip.l0.off { border-left-color: #4e5058; }
</style></head>
<body>
__NAV__
<div class="wrap">
  <div>
    <div id="chips"></div>
    <img src="/debug/call_ui.mjpg" alt="voice call UI">
    <p>Live voice-call UI (rendered up to 30 fps; the model receives one screenshot per second)</p>
    <p><button id="a">Listen to what the bot hears</button> <span id="s"></span></p>
  </div>
  <iframe id="tb" title="TensorBoard: voice metrics"></iframe>
</div>
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
<script>
// Voice metrics (voice/*): TensorBoard of the current run, same origin because it is proxied under /debug.
// Default to dark theme + auto reload (5 s) unless the user already chose something.
try {
  const k = '_tb_global_settings', s = JSON.parse(localStorage.getItem(k) || '{}');
  let ch = false;
  if (!s.theme) { s.theme = 'dark'; ch = true; }
  if (s.autoReload === undefined) { s.autoReload = true; s.autoReloadPeriodInMs = 5000; ch = true; }
  if (ch) localStorage.setItem(k, JSON.stringify(s));
} catch (e) {}
document.getElementById('tb').src = '__TB_SRC__';
// status chips: green / yellow / red from the same numbers TensorBoard graphs (polled every 2 s)
const f = d => x => x.toFixed(d), lvl = (a, b) => v => v > b ? 2 : v > a ? 1 : 0;
const CH = [['Model silent for', 'voice/gemini_last_msg_age_s', v => v.toFixed(1) + ' s', lvl(8, 20)],
  ['Waiting for answer', 'voice/unanswered_s', v => v.toFixed(1) + ' s', lvl(8, 20)],
  ['Discord to bot', 'voice/discord_rx_kb_s', v => v.toFixed(1) + ' KB/s'], ['Bot to Gemini', 'voice/gemini_tx_kb_s', v => v.toFixed(1) + ' KB/s'],
  ['Window render', 'voice/window_render_ms', v => v.toFixed(0) + ' ms', lvl(60, 150)],
  ['DAVE dropped', 'voice/dave_dropped_frames', f(0)], ['Crypto errors', 'voice/crypto_errors', f(0)],
  ['Reconnects', 'voice/reconnects', f(0)], ['Gemini WS closes', 'voice/gemini_ws_closes', f(0)], ['Reply latency (last / avg)', 'lat']];
const box = document.getElementById('chips'), els = CH.map(c => { const d = document.createElement('div'), s = document.createElement('small'), b = document.createElement('b');
  d.className = 'chip l0 off'; s.textContent = c[0]; b.textContent = '-'; d.append(s, b); box.appendChild(d); return [d, b]; });
async function tick() { try {
  const j = await (await fetch('/debug/api/voice')).json(), v = j.vals || {};
  CH.forEach((c, i) => { const x = c[1] === 'lat' ? (j.lat ? 1 : undefined) : v[c[1]]; const [d, b] = els[i];
    if (x === undefined) { d.className = 'chip l0 off'; b.textContent = '-'; return; }
    b.textContent = c[1] === 'lat' ? j.lat.last.toFixed(1) + ' / ' + j.lat.avg.toFixed(1) + ' s' : c[2](x);
    d.className = 'chip l' + (c[3] ? c[3](x) : 0); });
} catch (e) {} }
setInterval(tick, 2000); tick();
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
    app = web.Application(middlewares=[debug_panel.auth_middleware], client_max_size=16 * 2 ** 20)
    debug_panel.add_routes(app)

    async def page(request: web.Request) -> web.Response:
        return web.Response(text=PAGE.replace("__TB_SRC__", debug_panel.tb_url("voice/")), content_type="text/html",
                            headers={"Cache-Control": "no-store"})

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
