import asyncio, os, sys
from dotenv import load_dotenv
load_dotenv()
import config
config.LIVE_MODEL = sys.argv[1] if len(sys.argv) > 1 else config.LIVE_MODEL
from arona.voice_engine.src.gemini import GeminiWebSocket


async def main():
    g = GeminiWebSocket()
    msgs = []
    orig = g.handle_message
    async def spy(resp, *a, **k):
        keys = list(resp.keys()) if isinstance(resp, dict) else type(resp)
        if any(k in ("error", "goAway") for k in (keys if isinstance(keys, list) else [])):
            msgs.append(str(resp)[:300])
        return await orig(resp, *a, **k)
    g.handle_message = spy
    await g.connect()
    print("model:", config.LIVE_MODEL, "| open:", bool(g.ws and g.ws.protocol.state.name == "OPEN"))
    await g.send_realtime_text("Say the single word ok.")
    await asyncio.sleep(9)
    print("handle:", bool(g._resume_handle), "| still open:", bool(g.ws and g.ws.protocol.state.name == "OPEN"), "| errors:", msgs[:2])
    await g.close()

asyncio.run(main())
