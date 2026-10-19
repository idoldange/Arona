"""Backend cua Discord Activity (DAW): aiohttp chay chung process voi bot.

Auth cho moi /api/* (tru /api/token): header `Authorization: Bearer <discord access token>` + `X-Channel-Id: <voice channel id>`.
  - token khong hop le                              -> 401
  - kenh khong co session (chua ai bam nut Open DAW) -> 403
  - nguoi goi khong o trong kenh voice do            -> 403   (ACTIVITY_REQUIRE_VOICE=0 de tat, mac dinh bat)
Env: ACTIVITY_CLIENT_ID, ACTIVITY_CLIENT_SECRET (Developer Portal), ACTIVITY_PORT (8787), ACTIVITY_STATIC_DIR, ACTIVITY_REQUIRE_VOICE, ACTIVITY_COI.
"""
from __future__ import annotations

import asyncio
import os
import time

from aiohttp import web, ClientSession

from console import console
from arona.activity import chunking
from arona.activity.sessions import registry

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CACHE_DIR = os.path.join(ROOT, "activity", "cache")
STATIC_DIR = os.environ.get("ACTIVITY_STATIC_DIR") or os.path.join(ROOT, "activity", "web")
PORT = int(os.environ.get("ACTIVITY_PORT", "8787"))
CLIENT_ID = os.environ.get("ACTIVITY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("ACTIVITY_CLIENT_SECRET", "")
REQUIRE_VOICE = os.environ.get("ACTIVITY_REQUIRE_VOICE", "1") != "0"
COI = os.environ.get("ACTIVITY_COI", "0") == "1"      # cross-origin isolation headers (SharedArrayBuffer)
DISCORD_API = "https://discord.com/api/v10"
CACHE_MAX_FILES = 400
MAX_NOTES = 6000
LOCK_WAIT_S = 180          # cho synth_lock (bot co the dang render 1 job dai) toi da bay nhieu giay roi tra 503
SYNTH_TIMEOUT_S = 900

_bot = None
_user_cache: dict[str, tuple[int, float]] = {}     # token -> (user_id, het han)
_inflight: dict[str, asyncio.Future] = {}          # hash -> future wav (khong gen trung khi nhieu client cung xin 1 chunk)


def attach(bot) -> None:
    global _bot
    _bot = bot


# ------------------------------------------------------------------------------------------------ auth
def _deny(status: int, msg: str) -> web.Response:
    return web.json_response({"error": msg}, status=status)


async def _user_id(token: str) -> int | None:
    hit = _user_cache.get(token)
    if hit and hit[1] > time.time():
        return hit[0]
    async with ClientSession() as http:
        async with http.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token}"}) as r:
            if r.status != 200:
                return None
            uid = int((await r.json())["id"])
    _user_cache[token] = (uid, time.time() + 300)
    if len(_user_cache) > 2000:
        for k in [k for k, v in _user_cache.items() if v[1] < time.time()]:
            del _user_cache[k]
    return uid


def _in_voice(uid: int, guild_id: int | None, channel_id: int) -> bool:
    if not REQUIRE_VOICE:
        return True
    if _bot is None or guild_id is None:
        return False
    guild = _bot.get_guild(guild_id)
    member = guild.get_member(uid) if guild else None
    return bool(member and member.voice and member.voice.channel and member.voice.channel.id == channel_id)


@web.middleware
async def auth_mw(request: web.Request, handler):
    path = request.path
    if not path.startswith("/api/") or path in ("/api/token", "/api/config"):
        return await handler(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 12:
        return _deny(401, "missing token")
    try:
        channel_id = int(request.headers.get("X-Channel-Id", ""))
    except ValueError:
        return _deny(403, "no session")           # thieu/sai channel -> coi nhu khong co session
    uid = await _user_id(auth[7:])
    if uid is None:
        return _deny(401, "invalid token")
    sess = registry.get(channel_id)
    if sess is None:
        return _deny(403, "no active session for this channel; open the DAW from the !arona synth menu")
    if not _in_voice(uid, sess.guild_id, channel_id):
        return _deny(403, "you are not in this voice channel")
    sess.touch(uid)
    request["session"], request["user_id"] = sess, uid
    return await handler(request)


# ------------------------------------------------------------------------------------------------ handlers
async def h_config(request):
    return web.json_response({"client_id": CLIENT_ID})


async def h_token(request):
    """Doi `code` (tu Embedded App SDK authorize) lay access_token. Khong can session (nguoi dung chua vao duoc luc nay)."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return _deny(500, "server missing ACTIVITY_CLIENT_ID / ACTIVITY_CLIENT_SECRET")
    try:
        code = str((await request.json()).get("code", ""))
    except Exception:
        return _deny(400, "bad json")
    if not code:
        return _deny(400, "missing code")
    async with ClientSession() as http:
        async with http.post(f"{DISCORD_API}/oauth2/token", data={
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code", "code": code}) as r:
            data = await r.json()
            if r.status != 200:
                return _deny(400, "token exchange failed")
    return web.json_response({"access_token": data["access_token"]})


async def h_session(request):
    s = request["session"]
    return web.json_response({"channel_id": str(s.channel_id), "owner_id": str(s.owner_id), "users": len(s.users)})


def _parse_track(body: dict):
    notes = body.get("notes")
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ValueError("notes must be a list (max %d)" % MAX_NOTES)
    ppq = int(body.get("ppq", 480))
    tempos = body.get("tempos") or [[0, 120]]
    from arona.tts import synth as S
    params = {k: v for k, v in (body.get("params") or {}).items() if k in S.SERVER_PARAMS}
    params.setdefault("temperature", S.DEFAULT_SYNTH_TEMPERATURE)
    chunks, usts = chunking.plan_track(notes, ppq, tempos, params, str(body.get("track", "vocal")))
    return chunks, usts, params


def _cache_path(h: str) -> str:
    return os.path.join(CACHE_DIR, f"{h}.wav")


async def h_plan(request):
    """Tra danh sach chunk + hash + da co cache chua -> client biet chunk nao can gen lai."""
    try:
        chunks, _, _ = _parse_track(await request.json())
    except (ValueError, KeyError, TypeError) as e:
        return _deny(400, str(e))
    out = [dict(c.info(), cached=os.path.exists(_cache_path(c.hash))) for c in chunks]
    return web.json_response({"chunks": out, "dirty": sum(1 for c in out if not c["cached"])})


def _prune_cache() -> None:
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".wav")]
        if len(files) > CACHE_MAX_FILES:
            files.sort(key=os.path.getmtime)
            for f in files[:len(files) - CACHE_MAX_FILES]:
                os.remove(f)
    except OSError:
        pass


async def _render(h: str, ust: bytes, params: dict) -> bytes:
    from arona.tts import synth as S
    fut = _inflight.get(h)
    if fut:
        return await asyncio.shield(fut)
    fut = asyncio.get_running_loop().create_future()
    _inflight[h] = fut
    try:
        try:
            await asyncio.wait_for(S.synth_lock.acquire(), LOCK_WAIT_S)   # dung chung khoa voi !arona synth de khong tranh GPU
        except asyncio.TimeoutError:
            raise web.HTTPServiceUnavailable(text='{"error":"synth busy, retry"}', content_type="application/json")
        try:
            audio, _, err = await S._post_synth(ust, "application/octet-stream", params, SYNTH_TIMEOUT_S)
        finally:
            S.synth_lock.release()
        if not audio:
            raise web.HTTPBadGateway(text='{"error":"%s"}' % str(err).replace('"', "'")[:200], content_type="application/json")
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(h) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(audio)
        os.replace(tmp, _cache_path(h))
        _prune_cache()
        fut.set_result(audio)
        return audio
    except BaseException as e:
        if not fut.done():
            fut.set_exception(e)
            fut.exception()      # danh dau da lay, tranh warning "never retrieved"
        raise
    finally:
        _inflight.pop(h, None)


async def h_chunk(request):
    """Render DUNG 1 chunk (theo index) -> wav. Trung cache thi tra ngay, khong goi GPT-SoVITS."""
    try:
        body = await request.json()
        chunks, usts, params = _parse_track(body)
        idx = int(body["index"])
    except (ValueError, KeyError, TypeError) as e:
        return _deny(400, str(e))
    if not 0 <= idx < len(chunks):
        return _deny(404, "chunk index out of range")
    c = chunks[idx]
    want = body.get("hash")
    if want and want != c.hash:
        return _deny(409, "chunk changed; re-plan")
    hit = os.path.exists(_cache_path(c.hash))
    if hit:
        os.utime(_cache_path(c.hash))
        with open(_cache_path(c.hash), "rb") as f:
            audio = f.read()
    else:
        console.log(f"Activity chunk {idx}/{len(chunks)} ({len(c.notes)} notes) by user {request['user_id']}", "INFO")
        audio = await _render(c.hash, usts[idx], params)
    return web.Response(body=audio, content_type="audio/wav", headers={
        "X-Chunk-Hash": c.hash, "X-Start-Sec": f"{c.start_sec:.4f}", "X-End-Sec": f"{c.end_sec:.4f}",
        "X-Cache": "HIT" if hit else "MISS", "Access-Control-Expose-Headers": "X-Chunk-Hash,X-Start-Sec,X-End-Sec,X-Cache"})


# ------------------------------------------------------------------------------------------------ app
@web.middleware
async def coi_mw(request, handler):
    resp = await handler(request)
    if COI and not request.path.startswith("/api/"):
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return resp


def make_app() -> web.Application:
    app = web.Application(middlewares=[coi_mw, auth_mw], client_max_size=4 * 1024 * 1024)
    app.add_routes([
        web.get("/api/config", h_config),
        web.post("/api/token", h_token),
        web.get("/api/session", h_session),
        web.post("/api/vocal/plan", h_plan),
        web.post("/api/vocal/chunk", h_chunk),
    ])
    if os.path.isdir(STATIC_DIR):
        async def index(_):
            return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))
        app.router.add_get("/", index)
        app.router.add_static("/", STATIC_DIR, follow_symlinks=False)
    return app


_runner = None


async def start(bot=None) -> None:
    """Goi 1 lan khi bot san sang (vd on_ready / setup_hook)."""
    global _runner
    if bot is not None:
        attach(bot)
    if _runner:
        return
    _runner = web.AppRunner(make_app())
    await _runner.setup()
    await web.TCPSite(_runner, "127.0.0.1", PORT).start()      # chi bind localhost; ngrok/cloudflared tro vao day
    console.log(f"Activity server listening on 127.0.0.1:{PORT} (static: {STATIC_DIR if os.path.isdir(STATIC_DIR) else 'none'})", "INFO")
