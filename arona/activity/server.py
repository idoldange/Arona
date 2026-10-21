"""Backend cua Discord Activity (DAW): aiohttp chay chung process voi bot.

Auth cho moi /api/* (tru /api/token, /api/config, /api/session/check): header `Authorization: Bearer <discord access token>` + `X-Channel-Id: <channel id>`.
  - token khong hop le                              -> 401
  - kenh khong co session (chua ai bam nut Open DAW) -> 403
  - kenh voice: nguoi goi khong o trong kenh do     -> 403   (ACTIVITY_REQUIRE_VOICE=0 de tat)
  - kenh text:  nguoi goi phai la member cua guild   -> 403
Env: ACTIVITY_CLIENT_ID, ACTIVITY_CLIENT_SECRET (Developer Portal), ACTIVITY_PORT (8787), ACTIVITY_STATIC_DIR, ACTIVITY_REQUIRE_VOICE, ACTIVITY_COI.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import time
from urllib.parse import quote, unquote

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
SAVE_DIR = os.path.join(ROOT, "activity", "saves")
SAVE_KEEP = 5                         # so ban luu giu lai moi kenh voice
SAVE_TTL_S = 7 * 24 * 3600            # ban luu qua 7 ngay bi xoa
SAVE_MAX_BYTES = 4 * 1024 * 1024
IMPORT_MAX_BYTES = 32 * 1024 * 1024
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


def _member_ok(uid: int, guild_id: int | None) -> bool:
    """Text channel khong co khai niem 'dang o trong kenh' -> chi can la member cua guild."""
    if guild_id is None:
        return True                                  # DM
    if _bot is None:
        return False
    guild = _bot.get_guild(guild_id)
    return bool(guild and guild.get_member(uid))


async def h_session_check(request):
    """Kiem tra kenh da co session chua (Activity mo truc tiep thi chua co).

    KHONG tao session: chi bam nut Open DAW moi tao. Khong co -> {"exists": false} de
    client hien trang Blue Archive wiki thay vi man hinh trong.
    """
    try:
        channel_id = int(request.headers.get("X-Channel-Id", ""))
    except ValueError:
        return _deny(400, "bad channel id")
    # Khong goi Discord API o day: boot goi endpoint nay truoc khi dispatch auth, mot
    # request /users/@me cham se lam Activity dung o "Initializing..." rat lau.
    sess = registry.get(channel_id)
    if sess is None:
        return web.json_response({"exists": False})
    return web.json_response({"exists": True, "channel_id": str(sess.channel_id)})


@web.middleware
async def auth_mw(request: web.Request, handler):
    path = request.path
    if not path.startswith("/api/") or path in ("/api/token", "/api/config", "/api/session/check"):
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
        launched_from_text = sess.text_channel_id is not None and channel_id == sess.text_channel_id
        if not (launched_from_text and _member_ok(uid, sess.guild_id)):
            return _deny(403, "you are not in this voice/text channel")
    sess.touch(uid)
    if path not in ("/api/export", "/api/import") and (request.content_length or 0) > JSON_MAX_BYTES:
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
    # Lock one transpose for the whole track. Letting /synth auto-transpose each chunk
    # independently makes the octave jump between phrases (different median per chunk).
    if "transpose" not in params and params.get("auto_octave", True):
        params["transpose"] = chunking.auto_transpose(notes, float(params.get("voice_center", 66.0)))
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


def _session_resolver(sess):
    """Track -> (sf stem, bank, program) dung soundfont cua panel (ke ca font nguoi dung upload)."""
    from arona.tts import synth as S
    try:
        default = S.default_soundfont()
    except Exception:
        default = None

    def resolve(t):
        if t.role == "vocal":
            return "", 0, 52
        bank = 128 if t.drums else 0
        program = max(0, min(127, int(t.program)))
        stem = (t.sf_hint or "").strip()
        if not stem:
            return "", bank, program
        path = next((p for name, p in sess.fonts if name == stem), None)
        if path is None:
            found = S._match_soundfonts(stem)
            path = found[0] if found else None
        if path is None:
            return "", bank, program
        if (t.preset or "").strip():
            want = t.preset.strip().lower()
            for (b, p), name in (S._presets(path) or {}).items():
                if str(name).strip().lower() == want:
                    bank, program = int(b), int(p)
                    break
        same = default and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(default))
        return ("" if same else stem), bank, program

    return resolve


async def h_import_file(request):
    """Mo truc tiep file .mid/.ust/.ustx trong Activity (khong qua panel), tra ve project JSON."""
    if request.content_length and request.content_length > IMPORT_MAX_BYTES:
        return _deny(413, "file too large")
    body = await request.read()
    if not body:
        return _deny(400, "empty file")
    name = unquote(request.headers.get("X-File-Name", "import"))
    name = os.path.basename(name.replace("\\", "/")) or "import"
    from arona.activity.imports import build_imports
    try:
        projects = await asyncio.to_thread(build_imports, [(name, body)], None, _session_resolver(request["session"]))
    except Exception as e:
        return _deny(400, str(e))
    if not projects:
        return _deny(400, "no track with notes found")
    return web.json_response({"projects": projects})


async def h_font(request):
    """Soundfont cho Activity: font nguoi dung upload trong panel, hoac font co san trong ./soundfonts."""
    from arona.tts import synth as S
    stem = request.match_info.get("stem", "")
    path = next((p for name, p in request["session"].fonts if name == stem), None)
    if path is None:
        found = S._match_soundfonts(stem)
        path = found[0] if found else None
    if not path or not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


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
    """Nhan file tu Activity (multipart: mode, name, audio=wav, project=ustx|mid) -> bot dang 1 message kem file vao kenh text cua session.
    mode: audio | audio+ustx | ustx. Project co loi hat gui USTX, nhac cu gui MIDI.
    Wav qua lon so voi gioi han upload cua server Discord thi doi sang mp3."""
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

    audio = project = None
    project_ext = "ustx"
    if "audio" in mode:
        audio = await _field_bytes(form, "audio", EXPORT_MAX_AUDIO_BYTES)
        if audio is None or len(audio) > EXPORT_MAX_AUDIO_BYTES or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            return _deny(400, "audio must be a WAV file (max %d MB)" % (EXPORT_MAX_AUDIO_BYTES >> 20))
    if "ustx" in mode:
        from arona.tts import synth as S
        project = await _field_bytes(form, "project", EXPORT_MAX_USTX_BYTES)
        if project is None:                       # client cu gui field "ustx"
            project = await _field_bytes(form, "ustx", EXPORT_MAX_USTX_BYTES)
        fmt = S.detect_format(project or b"")
        if project is None or len(project) > EXPORT_MAX_USTX_BYTES or fmt not in ("ustx", "midi"):
            return _deny(400, "project must be a valid USTX or MIDI file (max %d MB)" % (EXPORT_MAX_USTX_BYTES >> 20))
        project_ext = "ustx" if fmt == "ustx" else "mid"
        if fmt == "ustx":
            try:
                project.decode("utf-8-sig")
            except UnicodeDecodeError:
                return _deny(400, "ustx must be UTF-8")

    limit = _upload_limit(channel)
    audio_name, converted = f"{name}.wav", False
    if audio is not None and len(audio) + len(project or b"") > limit:
        audio, audio_name, converted = await _wav_to_mp3(audio), f"{name}.mp3", True
    total = len(audio or b"") + len(project or b"")
    if total > limit:
        return _deny(413, f"export is {total >> 20} MB, over this server's {limit >> 20} MB upload limit even as mp3")

    s.last_export = time.time()
    files = []
    if audio is not None:
        files.append(discord.File(io.BytesIO(audio), filename=audio_name))
    if project is not None:
        files.append(discord.File(io.BytesIO(project), filename=f"{name}.{project_ext}"))
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


# ------------------------------------------------------------------------------------------------ saves
def _save_files(channel_id: int) -> list[str]:
    d = os.path.join(SAVE_DIR, str(int(channel_id)))
    try:
        names = [n for n in os.listdir(d) if n.endswith(".mid")]
    except OSError:
        return []
    return sorted((os.path.join(d, n) for n in names), reverse=True)     # ten file la timestamp ms -> moi nhat truoc


def _prune_saves(channel_id: int) -> None:
    now = time.time()
    for i, path in enumerate(_save_files(channel_id)):
        if i >= SAVE_KEEP or now - os.path.getmtime(path) > SAVE_TTL_S:
            for p in (path, path[:-4] + ".json"):
                try:
                    os.remove(p)
                except OSError:
                    pass


async def h_save(request):
    """Activity tu luu: body = file MIDI cua Signal (loi hat nam trong su kien Lyric), header X-Save-Name = ten bai (url-encoded)."""
    s, uid = request["session"], request["user_id"]
    body = await request.read()
    if not body or len(body) > SAVE_MAX_BYTES or body[:4] != b"MThd":
        return _deny(400, "body must be a MIDI file (max %d MB)" % (SAVE_MAX_BYTES >> 20))
    name = _safe_name(unquote(request.headers.get("X-Save-Name", "")))
    d = os.path.join(SAVE_DIR, str(s.channel_id))
    os.makedirs(d, exist_ok=True)
    stamp = int(time.time() * 1000)
    base = os.path.join(d, f"{stamp}")
    with open(base + ".mid.tmp", "wb") as f:
        f.write(body)
    os.replace(base + ".mid.tmp", base + ".mid")
    with open(base + ".json", "w", encoding="utf-8") as f:
        f.write('{"name": %s, "user": %d}' % (__import__("json").dumps(name), uid))
    _prune_saves(s.channel_id)
    return web.json_response({"ok": True, "saved_at": stamp})


async def h_save_latest(request):
    """Ban luu gan nhat cua kenh voice nay (de Activity moi tu mo lai)."""
    s = request["session"]
    _prune_saves(s.channel_id)
    files = _save_files(s.channel_id)
    if not files:
        return _deny(404, "no save for this channel")
    path = files[0]
    name, user = "", 0
    try:
        meta = __import__("json").load(open(path[:-4] + ".json", encoding="utf-8"))
        name, user = str(meta.get("name", "")), int(meta.get("user", 0))
    except (OSError, ValueError):
        pass
    with open(path, "rb") as f:
        data = f.read()
    return web.Response(body=data, content_type="audio/midi", headers={
        "X-Save-Name": quote(name), "X-Saved-At": os.path.basename(path)[:-4], "X-Saved-By": str(user),
        "Access-Control-Expose-Headers": "X-Save-Name,X-Saved-At,X-Saved-By"})


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
        web.get("/api/session/check", h_session_check),
        web.get("/api/session/imports", h_imports),
        web.get("/api/session/font/{stem}", h_font),
        web.post("/api/import", h_import_file),
        web.post("/api/export", h_export),
        web.post("/api/save", h_save),
        web.get("/api/save/latest", h_save_latest),
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
