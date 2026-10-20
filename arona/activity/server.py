"""Backend cua Discord Activity (DAW): aiohttp chay chung process voi bot.

Auth cho moi /api/* (tru /api/token): header `Authorization: Bearer <discord access token>` + `X-Channel-Id: <voice channel id>`.
  - token khong hop le                              -> 401
  - kenh khong co session (chua ai bam nut Open DAW) -> 403
  - nguoi goi khong o trong kenh voice do            -> 403   (ACTIVITY_REQUIRE_VOICE=0 de tat, mac dinh bat)
Env: ACTIVITY_CLIENT_ID, ACTIVITY_CLIENT_SECRET (Developer Portal), ACTIVITY_PORT (8787), ACTIVITY_STATIC_DIR, ACTIVITY_REQUIRE_VOICE, ACTIVITY_COI.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import time

import discord
from aiohttp import web, ClientSession

from console import console
from arona.activity import chunking
from arona.activity.sessions import registry

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CACHE_DIR = os.path.join(ROOT, "activity", "cache")
STATIC_DIR = os.environ.get("ACTIVITY_STATIC_DIR") or os.path.join(ROOT, "activity", "web")
PORT = int(os.environ.get("ACTIVITY_PORT", "8787"))
CERT = os.environ.get("ACTIVITY_CERT", "")             # duong dan Cloudflare Origin CA cert (.pem) -> bat HTTPS o origin
KEY = os.environ.get("ACTIVITY_KEY", "")
HOST = os.environ.get("ACTIVITY_HOST", "127.0.0.1")   # 0.0.0.0 neu Cloudflare (DNS proxy) ket noi thang toi may nay, khong qua tunnel
CLIENT_ID = os.environ.get("ACTIVITY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("ACTIVITY_CLIENT_SECRET", "")
REQUIRE_VOICE = os.environ.get("ACTIVITY_REQUIRE_VOICE", "1") != "0"
START_PAGE = os.environ.get("ACTIVITY_START_PAGE", "index.html")   # trang phuc vu cho "/"; dat diag.html de chan doan Activity trong Discord
COI = os.environ.get("ACTIVITY_COI", "0") == "1"      # cross-origin isolation headers (SharedArrayBuffer)
DISCORD_API = "https://discord.com/api/v10"
CACHE_MAX_FILES = 400
EXPORT_MODES = {"audio", "audio+ustx", "ustx"}
EXPORT_MAX_AUDIO_BYTES = 100 * 1024 * 1024
EXPORT_MAX_USTX_BYTES = 5 * 1024 * 1024
EXPORT_COOLDOWN_S = 3
FALLBACK_UPLOAD_LIMIT = 10 * 1024 * 1024      # gioi han upload khi khong doc duoc tu guild
JSON_MAX_BYTES = 4 * 1024 * 1024              # moi endpoint JSON (tru /api/export)
CACHE_TTL_S = 3600         # cache chunk phia server giu 1 gio ke tu lan dung gan nhat
WAIT_S = 25                # moi request cho job toi da bay nhieu giay roi tra 202 (Cloudflare cat o 100s)
RETRY_AFTER_S = 10         # client doi bay nhieu giay truoc khi hoi lai
MAX_NOTES = 6000
SYNTH_TIMEOUT_S = 900

_bot = None
_user_cache: dict[str, tuple[int, float]] = {}     # token -> (user_id, het han)
_jobs: dict[str, asyncio.Task] = {}                # hash -> job gen dang chay (dung chung giua cac request/retry)
_job_errors: dict[str, tuple[str, float]] = {}     # hash -> (loi, luc xay ra) cho job that bai khi khong ai dang doi
_watched: set[str] = set()                         # hash ma 1 request dang cho truc tiep


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
    if path != "/api/export" and (request.content_length or 0) > JSON_MAX_BYTES:
        return _deny(413, "request too large")
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


def _fresh(h: str) -> bool:
    """Cache hit con han: CACHE_TTL_S tinh tu lan dung gan nhat (moi lan trung cache se gia han them)."""
    try:
        return (time.time() - os.path.getmtime(_cache_path(h))) < CACHE_TTL_S
    except OSError:
        return False


async def h_plan(request):
    """Tra danh sach chunk + hash + da co cache chua -> client biet chunk nao can gen lai."""
    try:
        chunks, _, _ = _parse_track(await request.json())
    except (ValueError, KeyError, TypeError) as e:
        return _deny(400, str(e))
    out = [dict(c.info(), cached=_fresh(c.hash)) for c in chunks]
    return web.json_response({"chunks": out, "dirty": sum(1 for c in out if not c["cached"])})


def _prune_cache() -> None:
    try:
        now = time.time()
        files = []
        for name in os.listdir(CACHE_DIR):
            if not name.endswith(".wav"):
                continue
            path = os.path.join(CACHE_DIR, name)
            if now - os.path.getmtime(path) >= CACHE_TTL_S:
                os.remove(path)
            else:
                files.append(path)
        if len(files) > CACHE_MAX_FILES:
            files.sort(key=os.path.getmtime)
            for f in files[:len(files) - CACHE_MAX_FILES]:
                os.remove(f)
    except OSError:
        pass


async def _job(h: str, ust: bytes, params: dict) -> None:
    """Gen 1 chunk o nen (khong gan voi request nao): request co the bi Cloudflare cat o 100s nhung job van chay tiep va ghi cache."""
    from arona.tts import synth as S
    async with S.synth_lock:      # dung chung khoa voi !arona synth de khong tranh GPU
        audio, _, err = await S._post_synth(ust, "application/octet-stream", params, SYNTH_TIMEOUT_S)
    if not audio:
        raise RuntimeError(str(err) or "synth failed")
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_path(h) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(audio)
    os.replace(tmp, _cache_path(h))
    _prune_cache()


def _start_job(h: str, ust: bytes, params: dict) -> tuple[asyncio.Task, bool]:
    """Mot hash chi co 1 job dang chay, nhieu client/lan retry cung xin 1 chunk thi dung chung."""
    running = _jobs.get(h)
    if running and not running.done():
        return running, False
    task = asyncio.get_running_loop().create_task(_job(h, ust, params))
    _jobs[h] = task

    def _done(t: asyncio.Task) -> None:
        if _jobs.get(h) is t:
            del _jobs[h]
        if t.cancelled():
            return
        exc = t.exception()      # luon doc de khong bi warning "never retrieved"
        if exc is not None and h not in _watched:      # khong ai dang cho -> luu loi de lan retry sau bao lai
            _job_errors[h] = (str(exc)[:200], time.time())
    task.add_done_callback(_done)
    return task, True


def _wav_response(c, hit: bool) -> web.Response:
    try:
        with open(_cache_path(c.hash), "rb") as f:
            audio = f.read()
    except OSError:
        return _deny(502, "chunk expired before it could be sent; retry")
    return web.Response(body=audio, content_type="audio/wav", headers={
        "X-Chunk-Hash": c.hash, "X-Start-Sec": f"{c.start_sec:.4f}", "X-End-Sec": f"{c.end_sec:.4f}",
        "X-Cache": "HIT" if hit else "MISS", "Access-Control-Expose-Headers": "X-Chunk-Hash,X-Start-Sec,X-End-Sec,X-Cache"})


async def h_chunk(request):
    """Render DUNG 1 chunk (theo index) -> wav.
    - Trung cache: tra ngay.
    - Chua co: gen o nen, doi toi da WAIT_S giay. Xong kip thi tra wav, khong thi tra 202 (client doi Retry-After giay roi hoi lai;
      lan sau job da xong thi trung cache). Cloudflare cat request o 100s nen KHONG de request treo lau."""
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
    if _fresh(c.hash):
        os.utime(_cache_path(c.hash))
        return _wav_response(c, True)
    err = _job_errors.pop(c.hash, None)
    if err and time.time() - err[1] < 120 and c.hash not in _jobs:
        return _deny(502, err[0])
    task, started = _start_job(c.hash, usts[idx], params)
    if started:
        console.log(f"Activity chunk {idx}/{len(chunks)} ({len(c.notes)} notes) by user {request['user_id']}", "INFO")
    _watched.add(c.hash)
    try:
        await asyncio.wait({task}, timeout=WAIT_S)
    finally:
        _watched.discard(c.hash)
    if not task.done():
        return web.json_response({"status": "pending", "retry_after": RETRY_AFTER_S}, status=202,
                                 headers={"Retry-After": str(RETRY_AFTER_S)})
    if task.cancelled():
        return _deny(502, "synth job cancelled")
    exc = task.exception()
    if exc is not None:
        _job_errors.pop(c.hash, None)
        return _deny(502, str(exc)[:200])
    return _wav_response(c, False)


# ------------------------------------------------------------------------------------------------ import / export
async def h_imports(request):
    """File ustx/mid ma nguoi dung da up qua modal cua panel `!arona synth` (da parse san)."""
    return web.json_response({"projects": request["session"].imports})


def _safe_name(raw) -> str:
    name = re.sub(r"[^\w\- ]+", "", str(raw or "")).strip().replace(" ", "_")[:60]
    return name or "arona_export"


def _upload_limit(channel) -> int:
    guild = getattr(channel, "guild", None)
    return int(getattr(guild, "filesize_limit", 0) or FALLBACK_UPLOAD_LIMIT)


async def _wav_to_mp3(wav: bytes) -> bytes:
    def work() -> bytes:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(io.BytesIO(wav), format="wav")
        buf = io.BytesIO()
        seg.export(buf, format="mp3", bitrate="192k")
        return buf.getvalue()
    return await asyncio.to_thread(work)


async def _field_bytes(form, key: str, limit: int) -> bytes | None:
    field = form.get(key)
    if field is None:
        return None
    data = field.file.read(limit + 1) if hasattr(field, "file") else str(field).encode("utf-8")
    return data if data else None


async def h_export(request):
    """Nhan file tu Activity (multipart: mode, name, audio=wav, ustx=text) -> bot dang 1 message kem file vao kenh text cua session.
    mode: audio | audio+ustx | ustx. Wav qua lon so voi gioi han upload cua server Discord thi doi sang mp3."""
    s, uid = request["session"], request["user_id"]
    if time.time() - s.last_export < EXPORT_COOLDOWN_S:
        return _deny(429, "slow down; wait a few seconds between exports")
    channel = _bot.get_channel(s.text_channel_id) if (_bot is not None and s.text_channel_id) else None
    if channel is None:
        return _deny(409, "no text channel for this session; press Open DAW again from the !arona synth panel")
    try:
        form = await request.post()
    except Exception:
        return _deny(400, "bad multipart body")
    mode = str(form.get("mode", ""))
    if mode not in EXPORT_MODES:
        return _deny(400, "mode must be one of: " + ", ".join(sorted(EXPORT_MODES)))
    name = _safe_name(form.get("name"))

    audio = ustx = None
    if "audio" in mode:
        audio = await _field_bytes(form, "audio", EXPORT_MAX_AUDIO_BYTES)
        if audio is None or len(audio) > EXPORT_MAX_AUDIO_BYTES or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            return _deny(400, "audio must be a WAV file (max %d MB)" % (EXPORT_MAX_AUDIO_BYTES >> 20))
    if "ustx" in mode:
        ustx = await _field_bytes(form, "ustx", EXPORT_MAX_USTX_BYTES)
        from arona.tts import synth as S
        if ustx is None or len(ustx) > EXPORT_MAX_USTX_BYTES or S.detect_format(ustx) != "ustx":
            return _deny(400, "ustx must be a valid USTX project (max %d MB)" % (EXPORT_MAX_USTX_BYTES >> 20))
        try:
            ustx.decode("utf-8-sig")
        except UnicodeDecodeError:
            return _deny(400, "ustx must be UTF-8")

    limit = _upload_limit(channel)
    audio_name, converted = f"{name}.wav", False
    if audio is not None and len(audio) + len(ustx or b"") > limit:
        audio, audio_name, converted = await _wav_to_mp3(audio), f"{name}.mp3", True
    total = len(audio or b"") + len(ustx or b"")
    if total > limit:
        return _deny(413, f"export is {total >> 20} MB, over this server's {limit >> 20} MB upload limit even as mp3")

    s.last_export = time.time()
    files = []
    if audio is not None:
        files.append(discord.File(io.BytesIO(audio), filename=audio_name))
    if ustx is not None:
        files.append(discord.File(io.BytesIO(ustx), filename=f"{name}.ustx"))
    try:
        msg = await channel.send(content=f"🎹 **{name}** exported from the DAW by <@{uid}>", files=files,
                                 allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        return _deny(502, "the bot cannot post files in that channel")
    except discord.HTTPException as e:
        return _deny(502, f"Discord rejected the message: {str(e)[:150]}")
    console.log(f"Activity export '{name}' ({mode}) by user {uid} -> #{s.text_channel_id}", "INFO")
    return web.json_response({"ok": True, "message_url": msg.jump_url, "files": [f.filename for f in files],
                              "converted_to_mp3": converted})


# ------------------------------------------------------------------------------------------------ app
@web.middleware
async def coi_mw(request, handler):
    resp = await handler(request)
    if COI and not request.path.startswith("/api/"):
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return resp


async def h_static(request):
    rel = request.match_info.get("path", "") or START_PAGE
    root = os.path.realpath(STATIC_DIR)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
        raise web.HTTPNotFound()          # ke ca path traversal (../) va symlink thoat ra ngoai
    return web.FileResponse(full)


def make_app() -> web.Application:
    app = web.Application(middlewares=[coi_mw, auth_mw], client_max_size=EXPORT_MAX_AUDIO_BYTES + EXPORT_MAX_USTX_BYTES + (1 << 20))
    app.add_routes([
        web.get("/api/config", h_config),
        web.post("/api/token", h_token),
        web.get("/api/session", h_session),
        web.get("/api/session/imports", h_imports),
        web.post("/api/export", h_export),
        web.post("/api/vocal/plan", h_plan),
        web.post("/api/vocal/chunk", h_chunk),
    ])
    app.router.add_get("/{path:.*}", h_static)      # dang ky sau cac route /api; doc thu muc web theo tung request (build lai khong can restart)
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
    ssl_ctx = None
    if CERT and KEY:
        import ssl
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(CERT, KEY)
    await web.TCPSite(_runner, HOST, PORT, ssl_context=ssl_ctx).start()   # mac dinh chi bind localhost; tunnel hoac Cloudflare proxy tro vao day
    console.log(f"Activity server listening on {'https' if ssl_ctx else 'http'}://{HOST}:{PORT} (static: {STATIC_DIR if os.path.isdir(STATIC_DIR) else 'MISSING - build the web app into this folder'})", "INFO")
