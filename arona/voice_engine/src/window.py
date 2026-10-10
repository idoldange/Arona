"""Virtual Discord window for Gemini Live voice sessions.

The Live API does not accept inline files, so instead of handing the model
attachments we render a fake "Discord window" (Pillow) and stream it as a
~1 fps JPEG video feed through realtime_input.video. The model drives it with
function calls (scroll / zoom / media control) and can see who is speaking
(green ring + "Speaking:" label) plus every member's avatar.

Chat text still goes through realtime text as usual (see GeminiWebSocket.send_realtime_text).
"""
import asyncio
import io
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional

import discord
from PIL import Image, ImageDraw, ImageFont

from console import console

# ───────────────────────── constants ─────────────────────────
W, H = 1280, 720
CHAT_W = 440
CALL_W = W - CHAT_W
TOPBAR_H = 44
INPUT_H = 52
HINT_H = 24
PAD = 16
AV = 40
FRAME_MIN_INTERVAL = 1.0      # seconds between frames (Live API samples video at ~1 fps)
SPEAK_HOLD = 0.6              # seconds a member stays "speaking" after the last loud audio
MAX_MSGS = 150
HISTORY_LOAD = 40
MAX_LINES = 14
IMG_MAX_W, IMG_MAX_H = 330, 200
MAX_IMG_BYTES = 25 * 1024 * 1024

COL_SIDE = (43, 45, 49)
CALL_BG = (17, 18, 20)
TILE_COLORS = [(228, 184, 166), (199, 196, 226), (166, 200, 188), (214, 170, 200), (170, 190, 226), (222, 210, 160)]
COL_MAIN = (49, 51, 56)
COL_ROW = (64, 66, 73)
COL_LINE = (30, 31, 34)
TEXT = (219, 222, 225)
MUTED = (148, 155, 164)
GREEN = (35, 165, 90)
RED = (237, 66, 69)
BLURPLE = (88, 101, 242)

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VID_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
AUD_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus", ".aac"}

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MEDIA_TMP = os.path.join(ROOT, "temp_audio", "window_media")


def _bin(name: str) -> str:
    local = os.path.join(ROOT, name + ".exe")
    return local if os.path.exists(local) else (shutil.which(name) or name)


# ───────────────────────── tool declarations ─────────────────────────
WINDOW_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "window_scroll",
        "description": "Scroll the chat inside the virtual Discord window (the video feed you see). Use it to read older messages or jump back to the newest ones.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "direction": {"type": "STRING", "enum": ["up", "down", "top", "bottom"],
                              "description": "up = older messages, down = newer, top = oldest loaded, bottom = latest"},
                "pages": {"type": "NUMBER", "description": "Screen-heights to scroll for up/down (default 0.7)"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "window_zoom",
        "description": "Open an image attachment from the chat full-size in the window and zoom into it. Call again with different x/y/level to pan or zoom further.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {"type": "INTEGER", "description": "Message number shown as #n next to the message"},
                "item": {"type": "INTEGER", "description": "Attachment number [k] within that message (default 1)"},
                "level": {"type": "NUMBER", "description": "Zoom factor 1-10, 1 = fit to window (default 2)"},
                "x": {"type": "NUMBER", "description": "Horizontal center to zoom on, 0 (left) to 1 (right). Default: keep / 0.5"},
                "y": {"type": "NUMBER", "description": "Vertical center to zoom on, 0 (top) to 1 (bottom). Default: keep / 0.5"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "window_zoom_avatar",
        "description": "Show a member's avatar large in the window (voice members and chat authors).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "user": {"type": "STRING", "description": "Display name of the member"},
                "level": {"type": "NUMBER", "description": "Zoom factor 1-10 (default 1)"},
                "x": {"type": "NUMBER", "description": "Horizontal center 0-1"},
                "y": {"type": "NUMBER", "description": "Vertical center 0-1"},
            },
            "required": ["user"],
        },
    },
    {
        "name": "window_close_view",
        "description": "Close the zoomed image/avatar view and go back to the chat.",
    },
    {
        "name": "window_media",
        "description": "Control playback of a video or audio attachment. Video frames appear in a player panel in the window and the audio is streamed to you. play needs message+item (or no args to resume the loaded media).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["play", "pause", "resume", "seek", "stop"]},
                "message": {"type": "INTEGER", "description": "Message number #n (for play)"},
                "item": {"type": "INTEGER", "description": "Attachment number [k] (for play, default 1)"},
                "position": {"type": "NUMBER", "description": "Absolute position in seconds (for play start / seek)"},
                "skip": {"type": "NUMBER", "description": "Relative jump in seconds for seek, e.g. -10 or 30"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "window_status",
        "description": "Get a text summary of the window: who is in the voice channel, who is speaking right now, what part of the chat is visible, and media state.",
    },
]
WINDOW_TOOL_NAMES = {t["name"] for t in WINDOW_TOOLS}

WINDOW_PROMPT = """

[VIRTUAL DISCORD WINDOW]
You receive a live video feed (about 1 frame per second) that is a render of the Discord window for this call. It has two SEPARATE parts:
- LEFT = the VOICE CALL (the voice channel you are speaking in). It shows one tile per person in the call with their avatar. A GREEN BORDER around a tile and the "Speaking: ..." label in its top bar show who is talking right now. The audio you hear is a mix of everyone, so use this to work out who said what. You are the tile marked "(you)". This area also shows a zoomed image or a media player while you have one open.
- RIGHT = the TEXT CHAT panel, headed "TEXT CHAT # channel-name" with a message box at the bottom. It is a written text channel, NOT the voice channel: nobody speaks there. Every message has a number "#n" and attachments are numbered [k]. The messages you wrote in text appear there too, as your own name with "(you)". New messages also reach you as plain text.
- Images/videos/audio posted in the text chat are NOT sent to you directly; open them with the window tools.
- Tools: window_scroll (scroll the text chat), window_zoom (open + zoom an image in the call area), window_zoom_avatar, window_close_view, window_media (play/pause/seek a video or audio file; its audio is streamed to you), window_status.
- After a tool call the next frame already shows the result. Frames lag by up to a second, so do not assume the view is stale or broken.
"""


# ───────────────────────── fonts / text helpers ─────────────────────────
_FONT_DIRS = ["C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/noto/"]
_FONT_CACHE: Dict[tuple, Any] = {}
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _font(size: int, bold: bool = False, cjk: bool = False):
    key = (size, bold, cjk)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if cjk:
        names = ["YuGothB.ttc" if bold else "YuGothM.ttc", "msyh.ttc", "msgothic.ttc", "NotoSansCJK-Regular.ttc"]
    else:
        names = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    font = None
    for d in _FONT_DIRS:
        for n in names:
            try:
                font = ImageFont.truetype(d + n, size)
                break
            except Exception:
                continue
        if font:
            break
    font = font or ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _tfont(text: str, size: int, bold: bool = False):
    return _font(size, bold, bool(_CJK.search(text or "")))


def _wrap(font, text: str, maxw: int) -> List[str]:
    lines: List[str] = []
    for para in (text or "").split("\n"):
        cur = ""
        for tok in re.findall(r"\S+\s*|\s+", para) or [""]:
            if font.getlength(cur + tok) <= maxw:
                cur += tok
                continue
            if cur:
                lines.append(cur.rstrip())
                cur = ""
            while font.getlength(tok) > maxw and len(tok) > 1:
                i = len(tok)
                while i > 1 and font.getlength(tok[:i]) > maxw:
                    i -= 1
                lines.append(tok[:i])
                tok = tok[i:]
            cur = tok
        lines.append(cur.rstrip())
    return lines


def _ellipsize(font, text: str, maxw: int) -> str:
    if font.getlength(text) <= maxw:
        return text
    while len(text) > 1 and font.getlength(text + "...") > maxw:
        text = text[:-1]
    return text + "..."


def _circle(im: Image.Image, size: int) -> Image.Image:
    im = im.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    im.putalpha(mask.resize((size, size), Image.LANCZOS))
    return im


def _fmt_t(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class _Entry:
    __slots__ = ("num", "id", "author_id", "name", "color", "time", "text", "reply", "items", "system")


# ───────────────────────── media player ─────────────────────────
class MediaPlayer:
    """Plays a local video/audio file into the Live session: audio as 16 kHz PCM through
    realtime audio, video as 1 fps JPEG frames shown in the window's player panel."""

    def __init__(self, win: "VirtualDiscordWindow", path: str, name: str, kind: str, ref: str):
        self.win, self.path, self.name, self.kind, self.ref = win, path, name, kind, ref
        self.duration = 0.0
        self.position = 0.0
        self.t0 = 0.0
        self.playing = False
        self.ended = False
        self.frame: Optional[bytes] = None
        self._gen = 0
        self._tasks: List[asyncio.Task] = []
        self._procs: List[Any] = []

    @property
    def cur(self) -> float:
        pos = self.position + ((time.monotonic() - self.t0) if self.playing else 0.0)
        return min(pos, self.duration) if self.duration else pos

    async def probe(self) -> None:
        try:
            p = await asyncio.create_subprocess_exec(
                _bin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", self.path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await p.communicate()
            self.duration = float(out.decode().strip() or 0)
        except Exception as e:
            console.log(f"[Window] ffprobe failed: {e}", "WARN")

    async def still(self, pos: float) -> None:
        """Grab one frame at `pos` for the panel (used while paused / after seeking)."""
        if self.kind != "video":
            return
        try:
            p = await asyncio.create_subprocess_exec(
                _bin("ffmpeg"), "-v", "error", "-ss", str(pos), "-i", self.path, "-frames:v", "1",
                "-vf", "scale=640:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await p.communicate()
            if out:
                self.frame = out
        except Exception as e:
            console.log(f"[Window] still frame failed: {e}", "WARN")

    async def start(self, pos: float) -> None:
        await self._kill()
        top = max(0.0, self.duration - 0.2) if self.duration else pos
        self.position = max(0.0, min(pos, top))
        self.ended = False
        self.playing = True
        self.t0 = time.monotonic()
        gen = self._gen
        self._tasks = [asyncio.create_task(self._audio_loop(self.position))]
        if self.kind == "video":
            self._tasks.append(asyncio.create_task(self._video_loop(self.position)))
        self._tasks.append(asyncio.create_task(self._watch(gen, list(self._tasks))))
        self.win.dirty = True

    async def pause(self) -> None:
        if not self.playing:
            return
        pos = self.cur
        await self._kill()
        self.position, self.playing = pos, False
        await self.still(pos)
        self.win.dirty = True

    async def seek(self, pos: float) -> None:
        was_playing = self.playing
        pos = max(0.0, min(pos, max(0.0, self.duration - 0.2) if self.duration else pos))
        if was_playing:
            await self.start(pos)
        else:
            self.position, self.ended = pos, False
            await self.still(pos)
            self.win.dirty = True

    async def close(self) -> None:
        await self._kill()
        self.playing = False
        try:
            os.remove(self.path)
        except OSError:
            pass

    async def _kill(self) -> None:
        self._gen += 1
        for t in self._tasks:
            t.cancel()
        for p in self._procs:
            try:
                p.kill()
            except Exception:
                pass
        for t in self._tasks:
            try:
                await t
            except BaseException:
                pass
        self._tasks, self._procs = [], []

    async def _watch(self, gen: int, tasks: List[asyncio.Task]) -> None:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            return
        if gen == self._gen:
            self.position, self.playing, self.ended = self.duration or self.cur, False, True
            self.win.dirty = True
            console.log(f"[Window] Media finished: {self.name}", "INFO")

    async def _audio_loop(self, start: float) -> None:
        proc = await asyncio.create_subprocess_exec(
            _bin("ffmpeg"), "-v", "error", "-ss", str(start), "-i", self.path, "-vn",
            "-f", "s16le", "-ar", "16000", "-ac", "1", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self._procs.append(proc)
        base, sent = time.monotonic(), 0
        while True:
            data = await proc.stdout.read(3200)  # 100 ms
            if not data:
                break
            delay = base + sent / 32000 - 0.3 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await self.win.ws.send_audio_chunk(data)
            sent += len(data)

    async def _video_loop(self, start: float) -> None:
        proc = await asyncio.create_subprocess_exec(
            _bin("ffmpeg"), "-v", "error", "-ss", str(start), "-i", self.path, "-an",
            "-vf", "fps=1,scale=640:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self._procs.append(proc)
        buf, k, t0 = b"", 0, self.t0
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                s = buf.find(b"\xff\xd8")
                if s < 0:
                    break
                e = buf.find(b"\xff\xd9", s + 2)
                if e < 0:
                    break
                jpg, buf = buf[s:e + 2], buf[e + 2:]
                delay = t0 + k - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self.frame = jpg
                self.win.dirty = True
                k += 1


# ───────────────────────── the window ─────────────────────────
class VirtualDiscordWindow:
    def __init__(self, ws) -> None:
        self.ws = ws                         # GeminiWebSocket
        self.channel: Optional[discord.abc.Messageable] = None
        self.voice_channel = None
        self.bot_id = 0
        self.entries: List[_Entry] = []
        self._counter = 0
        self._by_id: Dict[int, int] = {}
        self._users: Dict[int, Any] = {}
        self.avatars: Dict[int, Image.Image] = {}
        self._av_pending: set = set()
        self._av_cache: Dict[tuple, Image.Image] = {}
        self.last_audio: Dict[int, float] = {}   # written by AudioProcessor (thread) – member id -> monotonic time
        self.at_bottom = True
        self.scroll_top = 0
        self.view: Optional[Dict[str, Any]] = None
        self.player: Optional[MediaPlayer] = None
        self.dirty = False
        self._force = False
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._last_sent = 0.0
        self._last_speaking: frozenset = frozenset()
        self._total_h = self._view_h = 0
        self._vis: tuple = (0, 0, 0, 0)      # first, last, above, below

    # ── lifecycle ──
    async def start(self, text_channel, voice_channel) -> None:
        await self.stop()
        self.channel, self.voice_channel = text_channel, voice_channel
        self.bot_id = voice_channel.guild.me.id if voice_channel and voice_channel.guild else 0
        self.entries, self._counter, self._by_id = [], 0, {}
        self.at_bottom, self.scroll_top, self.view = True, 0, None
        try:
            msgs = [m async for m in text_channel.history(limit=HISTORY_LOAD)]
            for m in reversed(msgs):
                self.add_message(m)
        except Exception as e:
            console.log(f"[Window] Failed to load history: {e}", "WARN")
        self._running, self.dirty = True, True
        self._task = asyncio.create_task(self._loop())
        console.log("[Window] Virtual Discord window started", "INFO")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self.player:
            await self.player.close()
            self.player = None
        self.view = None

    def force_next(self) -> None:
        self._force = True

    # ── messages ──
    def add_message(self, m: discord.Message) -> int:
        """Registers a chat message and returns its #number. Assets load in the background."""
        if m.id in self._by_id:
            return self._by_id[m.id]
        self._counter += 1
        self._by_id[m.id] = self._counter
        e = _Entry()
        e.num, e.id, e.author_id = self._counter, m.id, m.author.id
        e.name = (getattr(m.author, "display_name", None) or m.author.name) + (" (you)" if m.author.id == self.bot_id else "")
        col = getattr(m.author, "color", None)
        e.color = col.to_rgb() if col is not None and col.value else None
        e.time = m.created_at.astimezone().strftime("%H:%M")
        text = m.clean_content or ""
        for emb in m.embeds:
            bits = [emb.title or "", (emb.description or "")[:200]]
            bits = " ".join(b for b in bits if b)
            if bits:
                text += ("\n" if text else "") + "| " + bits
        for st in getattr(m, "stickers", []) or []:
            text += ("\n" if text else "") + f"[sticker: {st.name}]"
        e.text = text
        e.reply = None
        res = m.reference.resolved if m.reference else None
        if isinstance(res, discord.Message):
            e.reply = f"{res.author.display_name}: {(res.clean_content or '[attachment]')[:60]}"
        e.items = []
        e.system = None
        for k, a in enumerate(m.attachments, 1):
            ct, ext = (a.content_type or "").lower(), os.path.splitext(a.filename)[1].lower()
            if ct.startswith("image/") or ext in IMG_EXT:
                kind = "image"
            elif ct.startswith("video/") or ext in VID_EXT:
                kind = "video"
            elif ct.startswith("audio/") or ext in AUD_EXT:
                kind = "audio"
            else:
                kind = "file"
            item = {"idx": k, "name": a.filename, "kind": kind, "att": a, "img": None, "thumb": None, "fail": False}
            e.items.append(item)
            if kind == "image" and a.size <= MAX_IMG_BYTES:
                asyncio.create_task(self._load_image(item))
        self._users[m.author.id] = m.author
        self._ensure_avatar(m.author)
        self.entries.append(e)
        if len(self.entries) > MAX_MSGS:
            del self.entries[:len(self.entries) - MAX_MSGS]
        self.dirty = True
        return e.num

    @staticmethod
    def _message_text(m) -> str:
        text = m.clean_content or ""
        for emb in m.embeds:
            bits = " ".join(b for b in [emb.title or "", (emb.description or "")[:200]] if b)
            if bits:
                text += ("\n" if text else "") + "| " + bits
        for st in getattr(m, "stickers", []) or []:
            text += ("\n" if text else "") + f"[sticker: {st.name}]"
        return text

    def update_message(self, m) -> None:
        """Message was edited (the bot streams replies by editing)."""
        num = self._by_id.get(m.id)
        e = next((x for x in self.entries if x.num == num), None) if num else None
        if e is None:
            return
        text = self._message_text(m)
        if text != e.text:
            e.text, self.dirty = text, True

    def remove_message(self, message_id: int) -> None:
        num = self._by_id.pop(message_id, None)
        if num:
            self.entries = [x for x in self.entries if x.num != num]
            self.dirty = True

    def add_event(self, kind: str, member) -> None:
        """Adds a system line ("X joined/left the voice channel") to the window. It has no #number."""
        e = _Entry()
        e.num, e.id, e.author_id = 0, 0, member.id
        e.name = getattr(member, "display_name", None) or member.name
        e.color, e.reply, e.items, e.system = None, None, [], kind
        e.time = time.strftime("%H:%M")
        e.text = "joined the voice channel" if kind == "join" else "left the voice channel"
        self._users[member.id] = member
        self._ensure_avatar(member)
        self.entries.append(e)
        if len(self.entries) > MAX_MSGS:
            del self.entries[:len(self.entries) - MAX_MSGS]
        self.dirty = True

    def on_voice_state(self, member, before, after) -> None:
        """Call from on_voice_state_update: logs members entering/leaving the voice channel the bot is in."""
        vc = self.voice_channel
        if not self._running or vc is None or member.id == self.bot_id:
            return
        was = before.channel is not None and before.channel.id == vc.id
        now = after.channel is not None and after.channel.id == vc.id
        if now and not was:
            self.add_event("join", member)
        elif was and not now:
            self.add_event("leave", member)

    def _draw_event(self, view, vd, e: _Entry, by: int) -> None:
        col = GREEN if e.system == "join" else RED
        y, x = by + 6, PAD + 6
        pts = [(0, 7), (12, 7), (12, 2), (22, 11), (12, 20), (12, 15), (0, 15)]
        if e.system != "join":
            pts = [(22 - px, py) for px, py in pts]
        vd.polygon([(x + px, y + py) for px, py in pts], fill=col)
        ax = PAD + 38
        av = self._avatar(e.author_id, 24)
        if av:
            view.paste(av, (ax, y), av)
        else:
            vd.ellipse((ax, y, ax + 24, y + 24), fill=BLURPLE)
        nf = _tfont(e.name, 18, True)
        nx = ax + 32
        vd.text((nx, y), e.name, font=nf, fill=TEXT)
        tf = _font(18)
        tx2 = nx + nf.getlength(e.name) + 6
        vd.text((tx2, y), e.text, font=tf, fill=MUTED)
        vd.text((tx2 + tf.getlength(e.text) + 12, y + 3), e.time, font=_font(15), fill=MUTED)

    async def _load_image(self, item: Dict[str, Any]) -> None:
        try:
            raw = await item["att"].read()

            def _decode():
                im = Image.open(io.BytesIO(raw))
                im.seek(0)
                im = im.convert("RGB")
                im.thumbnail((4096, 4096))
                th = im.copy()
                th.thumbnail((IMG_MAX_W, IMG_MAX_H))
                return im, th
            item["img"], item["thumb"] = await asyncio.to_thread(_decode)
        except Exception as e:
            item["fail"] = True
            console.log(f"[Window] Image load failed ({item['name']}): {e}", "WARN")
        self.dirty = True

    def _ensure_avatar(self, user) -> None:
        if user.id in self.avatars or user.id in self._av_pending:
            return
        self._av_pending.add(user.id)
        self._users[user.id] = user

        async def _go():
            try:
                raw = await user.display_avatar.with_size(256).with_format("png").read()
                self.avatars[user.id] = Image.open(io.BytesIO(raw)).convert("RGBA")
            except Exception as e:
                console.log(f"[Window] Avatar load failed ({user}): {e}", "WARN")
            self.dirty = True
        asyncio.create_task(_go())

    def _avatar(self, uid: int, size: int) -> Optional[Image.Image]:
        src = self.avatars.get(uid)
        if src is None:
            return None
        key = (uid, size)
        if key not in self._av_cache:
            self._av_cache[key] = _circle(src, size)
        return self._av_cache[key]

    # ── state helpers (event-loop thread) ──
    def _speaking_ids(self) -> frozenset:
        now = time.monotonic()
        ids = {uid for uid, t in list(self.last_audio.items()) if now - t < SPEAK_HOLD}
        vc = self.ws.voice_client
        if vc is not None and self.bot_id:
            try:
                if vc.is_playing():
                    ids.add(self.bot_id)
            except Exception:
                pass
        return frozenset(ids)

    def _snapshot(self) -> Dict[str, Any]:
        members = []
        vc = self.voice_channel
        for m in (vc.members if vc else []):
            vs = m.voice
            self._ensure_avatar(m)
            members.append({
                "id": m.id, "name": m.display_name + (" (you)" if m.id == self.bot_id else ""),
                "muted": bool(vs and (vs.self_mute or vs.mute)), "deaf": bool(vs and (vs.self_deaf or vs.deaf)),
                "cam": bool(vs and vs.self_video), "live": bool(vs and vs.self_stream),
            })
        names = {mm["id"]: mm["name"] for mm in members}
        sp = self._speaking_ids()
        speaking = [names.get(i) or getattr(self._users.get(i), "display_name", str(i)) for i in sp]
        pl = self.player
        player = None
        if pl:
            player = {"name": pl.name, "kind": pl.kind, "ref": pl.ref, "playing": pl.playing, "ended": pl.ended,
                      "cur": pl.cur, "dur": pl.duration, "frame": pl.frame}
        return {
            "members": members, "speaking_ids": sp, "speaking": speaking, "player": player,
            "vc_name": getattr(vc, "name", "voice"), "ch_name": getattr(self.channel, "name", "chat"),
            "entries": list(self.entries),
        }

    # ── frame loop ──
    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(0.25)
                try:
                    wsock = self.ws.ws
                    if not wsock or wsock.protocol.state.name != "OPEN":
                        continue
                    sp = self._speaking_ids()
                    if sp != self._last_speaking:
                        self._last_speaking, self.dirty = sp, True
                    if (self.dirty or self._force) and time.monotonic() - self._last_sent >= FRAME_MIN_INTERVAL:
                        await self._push()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    console.log(f"[Window] Frame loop error: {e}", "ERROR")
        except asyncio.CancelledError:
            pass

    async def _push(self) -> None:
        async with self._lock:
            self.dirty = self._force = False
            snap = self._snapshot()
            jpg = await asyncio.to_thread(self._render, snap)
            await self.ws.send_video_frame(jpg)
            self._last_sent = time.monotonic()

    # ── rendering (worker thread) ──
    def _render(self, snap: Dict[str, Any]) -> bytes:
        frame = Image.new("RGB", (W, H), CALL_BG)
        d = ImageDraw.Draw(frame)

        # ---------- LEFT: the voice call ----------
        d.rectangle((0, 0, CALL_W, TOPBAR_H), fill=COL_SIDE)
        title = "VOICE CALL  |  " + snap["vc_name"]
        d.text((PAD, 11), title, font=_tfont(title, 19, True), fill=TEXT)
        sp_txt = ("Speaking: " + ", ".join(snap["speaking"])) if snap["speaking"] else "Nobody is speaking"
        sf = _tfont(sp_txt, 18, True)
        d.text((CALL_W - PAD - sf.getlength(sp_txt), 12), sp_txt, font=sf, fill=GREEN if snap["speaking"] else MUTED)
        body = (0, TOPBAR_H, CALL_W, H)
        if self.view:
            self._draw_zoom(frame, d, body)
        elif snap["player"]:
            self._draw_player(frame, d, snap["player"], body)
        else:
            self._draw_tiles(frame, d, snap, body)

        # ---------- RIGHT: the text chat panel (a text channel, not the voice channel) ----------
        cx = CALL_W
        d.rectangle((cx, 0, W, H), fill=COL_MAIN)
        d.text((cx + PAD, 5), "TEXT CHAT", font=_font(12, True), fill=MUTED)
        d.text((cx + PAD, 19), "# " + snap["ch_name"], font=_tfont(snap["ch_name"], 19, True), fill=TEXT)
        d.line((cx, TOPBAR_H, W, TOPBAR_H), fill=COL_LINE, width=2)
        d.line((cx, 0, cx, H), fill=COL_LINE, width=2)

        chat_bottom = H - INPUT_H - HINT_H
        self._draw_chat(frame, d, snap["entries"], (cx + 1, TOPBAR_H + 2, W, chat_bottom))
        first, last, above, below = self._vis
        hint = (f"Showing #{first}-#{last}" if first else "No messages") + \
               (" | latest" if self.at_bottom else f" | {below} newer below") + \
               (f" | {above} older above" if above else "")
        d.rectangle((cx + 1, chat_bottom, W, H), fill=COL_MAIN)
        d.text((cx + PAD, chat_bottom + 4), hint, font=_font(14), fill=MUTED)
        # fake message box, so it reads as a Discord text channel
        d.rounded_rectangle((cx + PAD, H - INPUT_H + 6, W - PAD, H - 10), 8, fill=(56, 58, 64))
        mf = _tfont(snap["ch_name"], 17)
        d.text((cx + PAD + 14, H - INPUT_H + 17), _ellipsize(mf, "Message #" + snap["ch_name"], CHAT_W - 2 * PAD - 28), font=mf, fill=MUTED)

        buf = io.BytesIO()
        frame.save(buf, "JPEG", quality=82)
        return buf.getvalue()

    def _draw_tiles(self, frame, d, snap, box) -> None:
        """Voice-call view: one tile per member, avatar in the middle, green border = speaking."""
        x0, y0, x1, y1 = box
        members = snap["members"]
        if not members:
            d.text((x0 + 40, y0 + 40), "Nobody is in the call", font=_font(22), fill=MUTED)
            return
        n = len(members)
        cols = 1 if n == 1 else (2 if n <= 4 else 3)
        rows = (n + cols - 1) // cols
        gap = 14
        tw = (x1 - x0 - gap * (cols + 1)) // cols
        th = min((y1 - y0 - gap * (rows + 1)) // rows, int(tw * 0.6))
        oy = y0 + (y1 - y0 - (rows * th + (rows - 1) * gap)) // 2
        for i, m in enumerate(members):
            r, c = divmod(i, cols)
            cnt = min(cols, n - r * cols)
            ox = x0 + gap + int((cols - cnt) * (tw + gap) / 2)
            tx, ty = ox + c * (tw + gap), oy + r * (th + gap)
            speaking = m["id"] in snap["speaking_ids"]
            d.rounded_rectangle((tx, ty, tx + tw, ty + th), 10, fill=TILE_COLORS[m["id"] % len(TILE_COLORS)])
            size = max(48, min(int(th * 0.5), 150))
            av = self._avatar(m["id"], size)
            ax, ay = tx + (tw - size) // 2, ty + (th - size) // 2 - 10
            if av:
                frame.paste(av, (ax, ay), av)
            else:
                d.ellipse((ax, ay, ax + size, ay + size), fill=BLURPLE)
            if speaking:
                d.rounded_rectangle((tx, ty, tx + tw, ty + th), 10, outline=GREEN, width=5)
            tags = [t for t, on in (("MUTED", m["muted"]), ("DEAF", m["deaf"]), ("CAM", m["cam"]), ("LIVE", m["live"])) if on]
            nf = _tfont(m["name"], 18, True)
            label = _ellipsize(nf, m["name"], tw - 40)
            tag_txt = "  " + " ".join(tags) if tags else ""
            tgf = _font(14, True)
            pw = int(nf.getlength(label) + (tgf.getlength(tag_txt) if tags else 0)) + 20
            py = ty + th - 36
            d.rounded_rectangle((tx + 10, py, tx + 10 + pw, py + 28), 6, fill=(0, 0, 0))
            d.text((tx + 20, py + 3), label, font=nf, fill=TEXT)
            if tags:
                d.text((tx + 20 + nf.getlength(label), py + 7), tag_txt, font=tgf, fill=RED if (m["muted"] or m["deaf"]) else GREEN)

    def _draw_player(self, frame, d, p, box) -> None:
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=(20, 21, 24))
        info_h = 150
        fx0, fy0, fx1, fy1 = x0 + PAD, y0 + PAD, x1 - PAD, y1 - info_h
        d.rectangle((fx0, fy0, fx1, fy1), fill=(0, 0, 0))
        if p["frame"]:
            try:
                im = Image.open(io.BytesIO(p["frame"])).convert("RGB")
                s = min((fx1 - fx0) / im.width, (fy1 - fy0) / im.height)
                im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
                frame.paste(im, (fx0 + (fx1 - fx0 - im.width) // 2, fy0 + (fy1 - fy0 - im.height) // 2))
            except Exception:
                pass
        elif p["kind"] == "audio":
            d.text(((fx0 + fx1) // 2 - 50, (fy0 + fy1) // 2 - 16), "AUDIO", font=_font(32, True), fill=MUTED)
        state = "ENDED" if p["ended"] else ("PLAYING" if p["playing"] else "PAUSED")
        ty = fy1 + 14
        d.text((fx0, ty), state, font=_font(22, True), fill=GREEN if p["playing"] else MUTED)
        tm = f"{_fmt_t(p['cur'])} / {_fmt_t(p['dur'])}"
        tf = _font(24, True)
        d.text((fx1 - tf.getlength(tm), ty), tm, font=tf, fill=TEXT)
        nf = _tfont(p["name"], 19, True)
        d.text((fx0, ty + 36), _ellipsize(nf, p["name"], fx1 - fx0), font=nf, fill=TEXT)
        d.text((fx0, ty + 64), p["ref"] + f"  ({p['kind']})  |  window_media: pause / resume / seek / stop", font=_font(15), fill=MUTED)
        by = ty + 94
        d.rounded_rectangle((fx0, by, fx1, by + 10), 5, fill=(64, 66, 73))
        if p["dur"]:
            d.rounded_rectangle((fx0, by, fx0 + max(6, int((fx1 - fx0) * min(1.0, p["cur"] / p["dur"]))), by + 10), 5, fill=BLURPLE)

    def _draw_zoom(self, frame, d, box) -> None:
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=(20, 21, 24))
        v = self.view
        im: Image.Image = v["img"]
        iw, ih = im.size
        aw, ah = (x1 - x0) - 40, (y1 - y0) - 50
        s = min(aw / iw, ah / ih) * v["level"]
        cw, ch = min(iw, aw / s), min(ih, ah / s)
        left = min(max(v["cx"] * iw - cw / 2, 0), iw - cw)
        top = min(max(v["cy"] * ih - ch / 2, 0), ih - ch)
        v["cx"], v["cy"] = (left + cw / 2) / iw, (top + ch / 2) / ih
        crop = im.crop((int(left), int(top), max(int(left) + 1, int(left + cw)), max(int(top) + 1, int(top + ch))))
        out = crop.resize((max(1, int(cw * s)), max(1, int(ch * s))), Image.LANCZOS)
        frame.paste(out, (x0 + (x1 - x0 - out.width) // 2, y0 + 8 + (ah - out.height) // 2))
        cap = f"{v['label']}  |  zoom x{v['level']:.1f}  |  center ({v['cx']:.2f}, {v['cy']:.2f})  |  window_close_view to close"
        d.text((x0 + 20, y1 - 34), cap, font=_font(16), fill=TEXT)

    def _block_height(self, e: _Entry, lines: List[str]) -> int:
        if e.system:
            return 34
        h = (22 if e.reply else 0) + 26 + len(lines) * 24
        for it in e.items:
            if it["kind"] == "image":
                h += (it["thumb"].height if it["thumb"] else 60) + 8
            else:
                h += 32
        return max(h, AV + 10) + 14

    def _draw_chat(self, frame, d, entries, box) -> None:
        x0, y0, x1, y1 = box
        mw, vh = x1 - x0, y1 - y0
        tx, tw = PAD + AV + 14, mw - (PAD + AV + 14) - PAD
        blocks, y = [], 8
        for e in entries:
            f = _tfont(e.text, 18)
            if e.system:
                blocks.append((e, y, 34, [], f))
                y += 34
                continue
            lines = _wrap(f, e.text, tw)[:MAX_LINES] if e.text else []
            if e.text and len(_wrap(f, e.text, tw)) > MAX_LINES:
                lines[-1] = _ellipsize(f, lines[-1], tw - 40) + " [truncated]"
            h = self._block_height(e, lines)
            blocks.append((e, y, h, lines, f))
            y += h
        total = y + 8
        self._total_h, self._view_h = total, vh
        maxtop = max(0, total - vh)
        if not self.at_bottom and self.scroll_top >= maxtop:
            self.at_bottom = True
        top = maxtop if self.at_bottom else max(0, self.scroll_top)
        self.scroll_top = top

        view = Image.new("RGB", (mw, vh), COL_MAIN)
        vd = ImageDraw.Draw(view)
        vis, above, below = [], 0, 0
        for e, by, h, lines, f in blocks:
            if by + h <= top:
                above += 1
                continue
            if by >= top + vh:
                below += 1
                continue
            if not e.system:
                vis.append(e.num)
            self._draw_block(view, vd, e, by - top, lines, f, tx, tw)
        self._vis = (min(vis), max(vis), above, below) if vis else (0, 0, above, below)
        frame.paste(view, (x0, y0))

    def _draw_block(self, view, vd, e: _Entry, by: int, lines, f, tx: int, tw: int) -> None:
        if e.system:
            self._draw_event(view, vd, e, by)
            return
        cy = by
        if e.reply:
            rf = _tfont(e.reply, 15)
            vd.text((tx, cy), "re " + _ellipsize(rf, e.reply, tw - 30), font=rf, fill=MUTED)
            cy += 22
        av = self._avatar(e.author_id, AV)
        if av:
            view.paste(av, (PAD, cy + 2), av)
        else:
            vd.ellipse((PAD, cy + 2, PAD + AV, cy + 2 + AV), fill=BLURPLE)
        nf = _tfont(e.name, 19, True)
        vd.text((tx, cy), e.name, font=nf, fill=e.color or TEXT)
        nx = tx + nf.getlength(e.name) + 10
        meta = f"{e.time}   #{e.num}"
        vd.text((nx, cy + 3), meta, font=_font(15), fill=MUTED)
        cy += 26
        for ln in lines:
            vd.text((tx, cy), ln, font=f, fill=TEXT)
            cy += 24
        for it in e.items:
            if it["kind"] == "image":
                th = it["thumb"]
                if th:
                    view.paste(th, (tx, cy))
                    vd.rectangle((tx, cy, tx + 30, cy + 22), fill=(0, 0, 0))
                    vd.text((tx + 8, cy + 1), str(it["idx"]), font=_font(17, True), fill=TEXT)
                    cy += th.height + 8
                else:
                    msg = "image failed to load" if it["fail"] else "loading image..."
                    vd.rectangle((tx, cy, tx + 240, cy + 52), fill=COL_SIDE)
                    vd.text((tx + 10, cy + 14), f"[{it['idx']}] {msg}", font=_font(16), fill=MUTED)
                    cy += 60
            else:
                label = f"[{it['idx']}] {it['kind'].upper()}  {it['name']}" + ("  (playable)" if it["kind"] in ("video", "audio") else "")
                lf = _tfont(label, 16)
                vd.rounded_rectangle((tx, cy, tx + min(tw, 480), cy + 28), 5, fill=COL_SIDE, outline=COL_LINE)
                vd.text((tx + 10, cy + 4), _ellipsize(lf, label, min(tw, 480) - 20), font=lf, fill=TEXT)
                cy += 32

    # ── tool handling ──
    def _find_entry(self, num: int) -> Optional[_Entry]:
        return next((e for e in self.entries if e.num == num and num > 0), None)

    def _summary(self) -> str:
        parts = []
        if self.view:
            v = self.view
            parts.append(f"Zoom view open: {v['label']}, x{v['level']:.1f}, center ({v['cx']:.2f},{v['cy']:.2f}).")
        else:
            first, last, above, below = self._vis
            parts.append(f"Chat shows messages #{first}-#{last}; " + ("at the latest message." if self.at_bottom else f"{below} newer message(s) below.") + (f" {above} older above." if above else ""))
        pl = self.player
        if pl:
            st = "ended" if pl.ended else ("playing" if pl.playing else "paused")
            parts.append(f"Media '{pl.name}' {st} at {_fmt_t(pl.cur)}/{_fmt_t(pl.duration)}.")
        sp = self._snapshot()["speaking"]
        parts.append("Speaking now: " + (", ".join(sp) if sp else "nobody") + ".")
        return " ".join(parts)

    async def _refresh(self) -> str:
        """Render + send a frame right away so it reaches the model before the tool response."""
        await self._push()
        return self._summary()

    def _find_user(self, name: str):
        name = (name or "").strip().lower().lstrip("@")
        pool = list(self._users.values())
        if self.voice_channel:
            pool = list(self.voice_channel.members) + pool
        for u in pool:
            if (getattr(u, "display_name", "") or "").lower() == name or u.name.lower() == name:
                return u
        for u in pool:
            if name and name in ((getattr(u, "display_name", "") or "").lower() + " " + u.name.lower()):
                return u
        return None

    async def handle_tool(self, name: str, args: Dict[str, Any]) -> str:
        try:
            args = args or {}
            if name == "window_status":
                snap = self._snapshot()
                mem = ", ".join(m["name"] + (" [muted]" if m["muted"] else "") for m in snap["members"]) or "none"
                return f"Voice members: {mem}. " + self._summary()

            if name == "window_scroll":
                page = self._view_h or 400
                amt = _num(args.get("pages"), 0.7)
                cur = max(0, self._total_h - self._view_h) if self.at_bottom else self.scroll_top
                d = args.get("direction")
                if d == "up":
                    self.at_bottom, self.scroll_top = False, max(0, int(cur - page * amt))
                elif d == "down":
                    new = int(cur + page * amt)
                    self.at_bottom, self.scroll_top = False, new
                elif d == "top":
                    self.at_bottom, self.scroll_top = False, 0
                elif d == "bottom":
                    self.at_bottom = True
                else:
                    return "Error: direction must be up, down, top or bottom."
                self.view = None
                return await self._refresh()

            if name in ("window_zoom", "window_zoom_avatar"):
                level = min(10.0, max(1.0, _num(args.get("level"), 2.0 if name == "window_zoom" else 1.0)))
                if name == "window_zoom":
                    num, k = int(_num(args.get("message"), 0)), int(_num(args.get("item"), 1))
                    e = self._find_entry(num)
                    if not e:
                        return f"Error: no message #{num} is loaded (scroll to find it)."
                    it = next((i for i in e.items if i["idx"] == k), None)
                    if not it:
                        return f"Error: message #{num} has no attachment [{k}]."
                    if it["kind"] != "image":
                        return f"Error: [{k}] of message #{num} is a {it['kind']}, not an image" + ("; use window_media to play it." if it["kind"] in ("video", "audio") else ".")
                    if not it["img"]:
                        return "Error: image failed to load." if it["fail"] else "Image is still loading, try again in a moment."
                    key, img, label = ("img", num, k), it["img"], f"message #{num} [{k}] {it['name']}"
                else:
                    u = self._find_user(args.get("user"))
                    if not u:
                        return f"Error: no member named '{args.get('user')}'."
                    key, label = ("av", u.id), f"avatar of {getattr(u, 'display_name', u.name)}"
                    if self.view and self.view.get("key") == key:
                        img = self.view["img"]
                    else:
                        raw = await u.display_avatar.with_size(1024).with_format("png").read()
                        img = Image.open(io.BytesIO(raw)).convert("RGB")
                same = bool(self.view and self.view.get("key") == key)
                cx = _num(args.get("x"), self.view["cx"] if same else 0.5)
                cy = _num(args.get("y"), self.view["cy"] if same else 0.5)
                self.view = {"key": key, "img": img, "label": label, "level": level,
                             "cx": min(1.0, max(0.0, cx)), "cy": min(1.0, max(0.0, cy))}
                return await self._refresh()

            if name == "window_close_view":
                self.view = None
                return await self._refresh()

            if name == "window_media":
                return await self._media(args)

            return f"Error: unknown window tool {name}."
        except Exception as e:
            console.log(f"[Window] Tool {name} failed: {e}", "ERROR")
            return f"Error: {e}"

    async def _media(self, args: Dict[str, Any]) -> str:
        action = args.get("action")
        pl = self.player
        if action == "play":
            if args.get("message") is not None:
                num, k = int(_num(args.get("message"), 0)), int(_num(args.get("item"), 1))
                e = self._find_entry(num)
                it = next((i for i in e.items if i["idx"] == k), None) if e else None
                if not it:
                    return f"Error: message #{num} / attachment [{k}] not found."
                if it["kind"] not in ("video", "audio"):
                    return f"Error: [{k}] of message #{num} is a {it['kind']}, not playable media."
                if pl:
                    await pl.close()
                os.makedirs(MEDIA_TMP, exist_ok=True)
                path = os.path.join(MEDIA_TMP, f"{e.id}_{k}{os.path.splitext(it['name'])[1] or '.bin'}")
                await it["att"].save(path)
                pl = self.player = MediaPlayer(self, path, it["name"], it["kind"], f"message #{num} [{k}]")
                await pl.probe()
                self.view = None
                await pl.start(_num(args.get("position"), 0.0))
            elif pl:
                pos = _num(args.get("position"), pl.cur if not pl.ended else 0.0)
                await pl.start(pos)
            else:
                return "Error: nothing loaded; give message and item to play."
        elif not pl:
            return "Error: no media is loaded."
        elif action == "pause":
            await pl.pause()
        elif action == "resume":
            await pl.start(0.0 if pl.ended else pl.cur)
        elif action == "seek":
            pos = _num(args.get("position"))
            if pos is None:
                pos = pl.cur + (_num(args.get("skip"), 0.0) or 0.0)
            await pl.seek(pos)
        elif action == "stop":
            await pl.close()
            self.player = None
        else:
            return "Error: action must be play, pause, resume, seek or stop."
        return await self._refresh()
