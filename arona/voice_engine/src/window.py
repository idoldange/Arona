"""Virtual Discord window for Gemini Live voice sessions.

The Live API does not accept inline files, so instead of handing the model
attachments we render a fake "Discord window" (Pillow) up to 30 fps and stream
a screenshot once per second through realtime_input.video. The model drives it with
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
VIDEO_STALE = 3.0             # seconds after which a camera frame counts as gone
RENDER_FPS = 30
RENDER_MIN_INTERVAL = 1.0 / RENDER_FPS
FRAME_MIN_INTERVAL = 1.0      # seconds between screenshots sent to the Live API
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
        "name": "window_focus",
        "description": "Focus a person's camera in the call area: it is enlarged and everyone else shrinks into a strip of small tiles at the bottom, like Discord. Only works for someone whose camera is on (see the CAM tag). Call window_unfocus to go back to the normal grid.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "user": {"type": "STRING", "description": "Display name or Discord ID of the person"},
            },
            "required": ["user"],
        },
    },
    {
        "name": "window_unfocus",
        "description": "Leave the focused camera view and show all call tiles in the normal grid again.",
    },
    {
        "name": "window_status",
        "description": "Get a text summary of the window: who is in the voice channel, who is speaking right now, what part of the chat is visible, and media state.",
    },
]
WINDOW_TOOL_NAMES = {t["name"] for t in WINDOW_TOOLS}

WINDOW_PROMPT = """

[VIRTUAL DISCORD WINDOW]
You receive one screenshot per second of the Discord window for this call. It has two SEPARATE parts:
- LEFT = the VOICE CALL (the voice channel you are speaking in). It shows one tile per person in the call with their avatar. A GREEN BORDER around a tile and the "Speaking: ..." label in its top bar show who is talking right now. The audio you hear is a mix of everyone, so use this to work out who said what. You are the tile marked "(you)". This area also shows a zoomed image or a media player while you have one open.
- RIGHT = the TEXT CHAT panel, headed "TEXT CHAT # channel-name" with a message box at the bottom. It is a written text channel, NOT the voice channel: nobody speaks there. Every message has a number "#n" and attachments are numbered [k]. The messages you wrote in text appear there too, as your own name with "(you)". New messages also reach you as plain text.
- Images/videos/audio posted in the text chat are NOT sent to you directly; open them with the window tools.
- A person whose camera is on shows their live camera in their tile (tag CAM). A screen share (tag LIVE) is NOT visible to you. window_focus enlarges one camera and shrinks the others into a strip below; window_unfocus goes back. If a tile shows no picture, the video simply has not arrived yet.
- Tools: window_scroll (scroll the text chat), window_focus / window_unfocus (camera view), window_zoom (open + zoom an image in the call area), window_zoom_avatar, window_close_view, window_media (play/pause/seek a video or audio file; its audio is streamed to you), window_status.
- Every voice tile and every chat message shows that person's Discord ID in small text (e.g. "ID 1234..."). Use it as user_id in saved_information to say whose saved info you are changing.
- After a tool call the next frame already shows the result. Frames lag by up to a second, so do not assume the view is stale or broken.
"""


# ───────────────────────── fonts / text helpers ─────────────────────────
_FONT_DIRS = [
    os.path.join(ROOT, "fonts"),
    os.path.join(ROOT, "assets", "fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "C:/Windows/Fonts",
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype/dejavu",
]
_FONT_CACHE: Dict[tuple, Any] = {}
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\U00020000-\U0003134f]")
_NERD_GLYPH = re.compile(r"[\u2190-\u2bff\u2600-\u27bf\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


def _font(size: int, bold: bool = False, cjk: bool = False, nerd: bool = False):
    key = (size, bold, cjk, nerd)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if cjk:
        names = (["SarasaGothicNerdFont-Bold.ttf", "NotoSansCJK-Bold.ttc", "NotoSansCJKjp-Bold.otf", "YuGothB.ttc", "msyhbd.ttc"]
                 if bold else ["SarasaGothicNerdFont-Regular.ttf", "NotoSansCJK-Regular.ttc", "NotoSansCJKjp-Regular.otf", "YuGothM.ttc", "msyh.ttc", "msgothic.ttc"])
    elif nerd:
        names = (["CaskaydiaCoveNerdFont-Bold.ttf", "JetBrainsMonoNerdFont-Bold.ttf", "FiraCodeNerdFont-Bold.ttf"]
                 if bold else ["CaskaydiaCoveNerdFont-Regular.ttf", "JetBrainsMonoNerdFont-Regular.ttf", "FiraCodeNerdFont-Regular.ttf"])
    else:
        names = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    font = None
    for d in _FONT_DIRS:
        for n in names:
            try:
                font = ImageFont.truetype(os.path.join(d, n), size)
                break
            except Exception:
                continue
        if font:
            break
    font = font or ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _tfont(text: str, size: int, bold: bool = False):
    text = text or ""
    cjk = bool(_CJK.search(text))
    return _font(size, bold, cjk, not cjk and bool(_NERD_GLYPH.search(text)))


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
    realtime audio, video rendered at up to 30 fps in the window's player panel."""

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
            "-vf", "fps=30,scale=640:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
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


def _cover(img, w, h):
    """Scale to fill w x h, centre-crop the overflow."""
    sc = max(w / img.width, h / img.height)
    nw, nh = max(1, int(img.width * sc)), max(1, int(img.height * sc))
    im = img.resize((nw, nh), Image.BILINEAR)
    l, t = (nw - w) // 2, (nh - h) // 2
    return im.crop((l, t, l + w, t + h))


def _contain(img, w, h):
    """Scale to fit inside w x h, keeping the aspect ratio."""
    sc = min(w / img.width, h / img.height)
    return img.resize((max(1, int(img.width * sc)), max(1, int(img.height * sc))), Image.BILINEAR)


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
        self.focus: Optional[Dict[str, Any]] = None      # {'uid': int, 'kind': 'camera'}
        self._vframes: Dict[tuple, tuple] = {}           # (uid, kind) -> (PIL image, monotonic time)
        self.player: Optional[MediaPlayer] = None
        self.dirty = False
        self._force = False
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._last_sent = 0.0
        self._last_rendered = 0.0
        self.last_jpg: Optional[bytes] = None            # newest rendered frame (debug web view)
        self.frame_seq = 0
        self._carry_notice: Optional[str] = None         # shown again after start() resets the window (voice rejoin)
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
        self.focus = None
        self._vframes.clear()
        try:
            msgs = [m async for m in text_channel.history(limit=HISTORY_LOAD)]
            for m in reversed(msgs):
                self.add_message(m)
        except Exception as e:
            console.log(f"[Window] Failed to load history: {e}", "WARN")
        # voice dropped but Gemini stayed up: keep the "connection lost" line across this restart
        carry, self._carry_notice = self._carry_notice, None
        me = voice_channel.guild.me if voice_channel and voice_channel.guild else None
        if carry and me is not None:
            self.add_event("lost", me, text=carry)
            self.add_event("join", me, text="reconnected")
        self._last_sent = self._last_rendered = 0.0
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
        self.focus = None
        self._vframes.clear()
        self.last_jpg, self.frame_seq = None, self.frame_seq + 1

    def latest_frame(self):
        return self.last_jpg, self.frame_seq

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

    def add_event(self, kind: str, member, text: Optional[str] = None) -> None:
        """Adds a system line ("X joined/left the voice channel") to the window. It has no #number."""
        e = _Entry()
        e.num, e.id, e.author_id = 0, 0, member.id
        e.name = getattr(member, "display_name", None) or member.name
        e.color, e.reply, e.items, e.system = None, None, [], kind
        e.time = time.strftime("%H:%M")
        e.text = text or ("joined the voice channel" if kind == "join" else "left the voice channel")
        self._users[member.id] = member
        self._ensure_avatar(member)
        self.entries.append(e)
        if len(self.entries) > MAX_MSGS:
            del self.entries[:len(self.entries) - MAX_MSGS]
        self.dirty = True

    def notice_connection_lost(self) -> None:
        """Voice dropped while the Gemini session is still alive: show a line in the call UI.
        It is carried over the window restart done by the rejoin (start())."""
        guild = self.voice_channel.guild if self.voice_channel else None
        me = guild.me if guild else None
        if me is None:
            return
        self._carry_notice = "connection lost, reconnecting..."
        self.add_event("lost", me, text=self._carry_notice)
        self.force_next()

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
        tf = _tfont(e.text, 18)
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

    def set_video_frame(self, uid, kind, img):
        """Video source hook (any thread): latest decoded camera frame of a member (kind is always 'camera')."""
        self._vframes[(int(uid), kind)] = (img.convert("RGB"), time.monotonic())
        self.dirty = True

    def clear_video(self, uid, kind=None):
        for k in [k for k in self._vframes if k[0] == int(uid) and (kind is None or k[1] == kind)]:
            self._vframes.pop(k, None)
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
        now = time.monotonic()
        frames = {k: v[0] for k, v in list(self._vframes.items()) if now - v[1] < VIDEO_STALE}
        focus = self.focus
        if focus and focus["uid"] not in {mm["id"] for mm in members}:
            focus = self.focus = None
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
            "frames": frames, "focus": focus,
            "vc_name": getattr(vc, "name", "voice"), "ch_name": getattr(self.channel, "name", "chat"),
            "entries": list(self.entries),
        }

    # ── frame loop ──
    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(RENDER_MIN_INTERVAL)
                try:
                    wsock = self.ws.ws
                    if not wsock or wsock.protocol.state.name != "OPEN":
                        continue
                    sp = self._speaking_ids()
                    if sp != self._last_speaking:
                        self._last_speaking, self.dirty = sp, True
                    now = time.monotonic()
                    if (self.dirty or self._force or self.last_jpg is None) and now - self._last_rendered >= RENDER_MIN_INTERVAL:
                        await self._render_latest()
                    if self.last_jpg is not None and time.monotonic() - self._last_sent >= FRAME_MIN_INTERVAL:
                        await self._send_latest()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    console.log(f"[Window] Frame loop error: {e}", "ERROR")
        except asyncio.CancelledError:
            pass

    async def _render_latest(self) -> None:
        async with self._lock:
            self.dirty = self._force = False
            snap = self._snapshot()
            jpg = await asyncio.to_thread(self._render, snap)
            self.last_jpg, self.frame_seq = jpg, self.frame_seq + 1
            self._last_rendered = time.monotonic()

    async def _send_latest(self) -> None:
        async with self._lock:
            if self.last_jpg is None:
                return
            await self.ws.send_video_frame(self.last_jpg)
            self._last_sent = time.monotonic()

    async def _push(self) -> None:
        """Refresh and send immediately after a window tool changes the view."""
        await self._render_latest()
        await self._send_latest()

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
        elif snap["focus"]:
            self._draw_focus(frame, d, snap, body)
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

    def _tiles(self, snap):
        """One camera/avatar tile per member."""
        tiles = []
        for m in snap["members"]:
            tiles.append({"m": m, "kind": "camera"})
        return tiles

    def _draw_tile(self, frame, d, snap, t, tx, ty, tw, th, compact=False):
        m, kind = t["m"], t["kind"]
        speaking = kind == "camera" and m["id"] in snap["speaking_ids"]
        d.rounded_rectangle((tx, ty, tx + tw, ty + th), 10, fill=TILE_COLORS[m["id"] % len(TILE_COLORS)] if kind == "camera" else (24, 25, 28))
        vid = snap["frames"].get((m["id"], kind))
        if vid is not None:
            frame.paste(_cover(vid, tw, th), (tx, ty))
        else:
            size = max(36 if compact else 48, min(int(th * 0.5), 150))
            av = self._avatar(m["id"], size)
            ax, ay = tx + (tw - size) // 2, ty + (th - size) // 2 - (4 if compact else 10)
            if av:
                frame.paste(av, (ax, ay), av)
            else:
                d.ellipse((ax, ay, ax + size, ay + size), fill=BLURPLE)
        if speaking:
            d.rounded_rectangle((tx, ty, tx + tw, ty + th), 10, outline=GREEN, width=5)
        tags = [tg for tg, on in (("MUTED", m["muted"]), ("DEAF", m["deaf"]), ("CAM", m["cam"]), ("LIVE", m["live"])) if on]
        label_full = m["name"]
        nf = _tfont(label_full, 14 if compact else 18, True)
        label = _ellipsize(nf, label_full, tw - 40)
        tgf = _font(11 if compact else 14, True)
        tag_cols = {"MUTED": RED, "DEAF": RED, "CAM": (88, 166, 255), "LIVE": GREEN}
        tags_w = sum(tgf.getlength("  " + tg) for tg in tags)
        pw = int(nf.getlength(label) + tags_w) + 20
        ph = 22 if compact else 28
        py = ty + th - ph - 8
        d.rounded_rectangle((tx + 10, py, tx + 10 + pw, py + ph), 6, fill=(0, 0, 0))
        d.text((tx + 20, py + (2 if compact else 3)), label, font=nf, fill=TEXT)
        if tags:
            txx = tx + 20 + nf.getlength(label)
            for tg in tags:
                d.text((txx, py + (5 if compact else 7)), "  " + tg, font=tgf, fill=tag_cols[tg])
                txx += tgf.getlength("  " + tg)
        idf = _font(11 if compact else 13)
        id_txt = "" f"ID {m['id']}"
        idh = 16 if compact else 20
        d.rounded_rectangle((tx + 10, py - idh - 4, tx + 26 + int(idf.getlength(id_txt)), py - 4), 5, fill=(0, 0, 0))
        d.text((tx + 18, py - idh - 2), id_txt, font=idf, fill=MUTED)

    def _draw_tiles(self, frame, d, snap, box):
        """Voice-call view: one tile per member (camera picture if they have one, else avatar); green border = speaking."""
        x0, y0, x1, y1 = box
        tiles = self._tiles(snap)
        if not tiles:
            d.text((x0 + 40, y0 + 40), "Nobody is in the call", font=_font(22), fill=MUTED)
            return
        n = len(tiles)
        cols = 1 if n == 1 else (2 if n <= 4 else 3)
        rows = (n + cols - 1) // cols
        gap = 14
        tw = (x1 - x0 - gap * (cols + 1)) // cols
        th = min((y1 - y0 - gap * (rows + 1)) // rows, int(tw * 0.6))
        oy = y0 + (y1 - y0 - (rows * th + (rows - 1) * gap)) // 2
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            cnt = min(cols, n - r * cols)
            ox = x0 + gap + int((cols - cnt) * (tw + gap) / 2)
            self._draw_tile(frame, d, snap, t, ox + c * (tw + gap), oy + r * (th + gap), tw, th)

    def _draw_focus(self, frame, d, snap, box):
        """Discord-style focus: one camera big in the middle, everyone else as a strip of small tiles below."""
        x0, y0, x1, y1 = box
        f = snap["focus"]
        tiles = self._tiles(snap)
        ft = next((t for t in tiles if t["m"]["id"] == f["uid"] and t["kind"] == f["kind"]), None)
        if ft is None:
            return self._draw_tiles(frame, d, snap, box)
        others = [t for t in tiles if t is not ft][:8]
        strip_h = 124 if others else 0
        bx0, by0, bx1, by1 = x0 + PAD, y0 + PAD, x1 - PAD, y1 - PAD - ((strip_h + PAD) if others else 0)
        bw, bh = bx1 - bx0, by1 - by0
        d.rounded_rectangle((bx0, by0, bx1, by1), 10, fill=(0, 0, 0))
        m = ft["m"]
        vid = snap["frames"].get((m["id"], ft["kind"]))
        if vid is not None:
            im = _contain(vid, bw, bh)
            frame.paste(im, (bx0 + (bw - im.width) // 2, by0 + (bh - im.height) // 2))
        else:
            size = max(64, min(bh // 2, 220))
            av = self._avatar(m["id"], size)
            if av:
                frame.paste(av, (bx0 + (bw - size) // 2, by0 + (bh - size) // 2 - 14), av)
            msg = "No video received yet"
            mf = _font(20, True)
            d.text((bx0 + (bw - mf.getlength(msg)) // 2, by0 + (bh + size) // 2 - 4), msg, font=mf, fill=MUTED)
        if ft["kind"] == "camera" and m["id"] in snap["speaking_ids"]:
            d.rounded_rectangle((bx0, by0, bx1, by1), 10, outline=GREEN, width=5)
        label_full = m["name"] + " (camera)"
        nf = _tfont(label_full, 20, True)
        pw = int(nf.getlength(label_full)) + 24
        d.rounded_rectangle((bx0 + 12, by1 - 40, bx0 + 12 + pw, by1 - 10), 6, fill=(0, 0, 0))
        d.text((bx0 + 24, by1 - 36), label_full, font=nf, fill=TEXT)
        idf = _font(14)
        id_txt = f"ID {m['id']}"
        d.rounded_rectangle((bx0 + 12, by1 - 66, bx0 + 28 + int(idf.getlength(id_txt)), by1 - 46), 5, fill=(0, 0, 0))
        d.text((bx0 + 20, by1 - 64), id_txt, font=idf, fill=MUTED)
        cap = "FOCUS  |  window_unfocus to go back"
        cf = _font(14)
        cw = int(cf.getlength(cap))
        d.rounded_rectangle((bx1 - 24 - cw, by0 + 10, bx1 - 8, by0 + 34), 6, fill=(0, 0, 0))
        d.text((bx1 - 16 - cw, by0 + 13), cap, font=cf, fill=MUTED)
        if others:
            n = len(others)
            tw = min(200, (x1 - x0 - PAD * (n + 1)) // n)
            total = n * tw + (n - 1) * PAD
            sx, ty = x0 + (x1 - x0 - total) // 2, y1 - PAD - strip_h
            for i, t in enumerate(others):
                self._draw_tile(frame, d, snap, t, sx + i * (tw + PAD), ty, tw, strip_h, compact=True)

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
        meta = f"{e.time}   #{e.num}   ID {e.author_id}"
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
        if self.focus:
            fu = self._users.get(self.focus["uid"])
            parts.append(f"Focused on {getattr(fu, 'display_name', self.focus['uid'])}'s {self.focus['kind']}.")
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
            if name in ("window_zoom", "window_zoom_avatar", "window_close_view") or (name == "window_media" and args.get("action") == "play"):
                self.focus = None
            if name == "window_focus":
                key = str(args.get("user") or "").strip().strip("<@!>")
                kind = "camera"
                pool = list(self.voice_channel.members) if self.voice_channel else []
                u = next((m for m in pool if str(m.id) == key), None)
                if u is None:
                    cand = self._find_user(key)
                    u = cand if cand is not None and any(m.id == cand.id for m in pool) else None
                if u is None:
                    return f"Error: nobody named '{args.get('user')}' is in the call."
                vs = u.voice
                on = bool(vs and vs.self_video)
                now = time.monotonic()
                have = any(k == (u.id, kind) and now - v[1] < VIDEO_STALE for k, v in list(self._vframes.items()))
                if not (on or have):
                    return f"Error: {u.display_name} has no {kind} on."
                self.focus = {"uid": u.id, "kind": kind}
                self.view = None
                return await self._refresh()
            if name == "window_unfocus":
                self.focus = None
                return await self._refresh()
            if name == "window_status":
                snap = self._snapshot()
                def _desc(m):
                    bits = []
                    if m["muted"]:
                        bits.append("muted")
                    if m["cam"]:
                        bits.append("camera on, " + ("picture visible" if (m["id"], "camera") in snap["frames"] else "no picture yet"))
                    if m["live"]:
                        bits.append("screen sharing - you cannot see it")
                    return m["name"] + f" (ID {m['id']})" + (" [" + "; ".join(bits) + "]" if bits else "")
                mem = ", ".join(_desc(m) for m in snap["members"]) or "none"
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
