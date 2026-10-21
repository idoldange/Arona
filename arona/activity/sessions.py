"""Session cua Activity, quan ly THEO KENH MO ACTIVITY (voice hoac text): chi co session khi ai do bam nut
"Open Editor" trong menu `!arona synth`. Khong co session cho kenh do -> API tra 403 (xem server.py)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

SESSION_TTL_S = 2 * 60 * 60     # het han sau 2 gio khong hoat dong


@dataclass
class Session:
    channel_id: int
    guild_id: int | None
    owner_id: int                       # nguoi bam nut mo Activity
    kind: str = "daw"                   # "daw" (Open Editor) loac "chess" (Interactive board)
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    users: set = field(default_factory=set)   # user id da goi API thanh cong
    text_channel_id: int | None = None        # kenh text noi bam Open Editor -> noi bot dang file export
    imports: list = field(default_factory=list)   # project da parse tu file ustx/mid trong panel (xem imports.py)
    fonts: list = field(default_factory=list)     # [(stem, path)] soundfont nguoi dung upload trong panel (Activity tai ve)
    last_export: float = 0.0

    def alive(self, now: float | None = None) -> bool:
        return ((now or time.time()) - self.last_seen) < SESSION_TTL_S

    def touch(self, user_id: int | None = None) -> None:
        self.last_seen = time.time()
        if user_id is not None:
            self.users.add(user_id)


class SessionRegistry:
    def __init__(self):
        self._by_channel: dict[int, Session] = {}

    def create(self, channel_id: int, guild_id: int | None, owner_id: int,
               text_channel_id: int | None = None, imports: list | None = None,
               fonts: list | None = None, kind: str = "daw") -> Session:
        """Moi kenh 1 session; bam nut lan nua thi gia han, giu session cu va cap nhat kenh text / file nhap moi."""
        self.purge()
        s = self._by_channel.get(channel_id)
        if s and s.alive():
            s.touch(owner_id)
        else:
            s = Session(channel_id, guild_id, owner_id, kind=kind)
            s.users.add(owner_id)
            self._by_channel[channel_id] = s
        s.kind = kind
        if text_channel_id is not None:
            # Activity co the duoc mo tu text channel (sdk.channelId = text id) trong khi
            # session duoc tao tu voice channel -> don session cu cua text id neu co.
            old = self.get(text_channel_id)
            if old is not None and old is not s:
                self.end(old.channel_id)
            s.text_channel_id = text_channel_id
        if imports is not None:
            s.imports = imports
        if fonts is not None:
            s.fonts = fonts
        return s

    def get(self, channel_id: int) -> Session | None:
        s = self._by_channel.get(channel_id)
        if s is None:
            # Session tao tu voice channel nhung Activity mo o text channel: tim theo
            # text_channel_id de khong phu thuoc vao viec Discord chon kenh nao.
            s = next((x for x in self._by_channel.values() if x.text_channel_id == channel_id), None)
        if s and not s.alive():
            self.end(s.channel_id)
            return None
        return s

    def end(self, channel_id: int) -> bool:
        s = self._by_channel.get(channel_id)
        if s is None:
            s = next((x for x in self._by_channel.values() if x.text_channel_id == channel_id), None)
        if s is None:
            return False
        for key in [k for k, v in self._by_channel.items() if v is s]:
            del self._by_channel[key]
        return True

    def purge(self) -> None:
        now = time.time()
        for cid in [c for c, s in self._by_channel.items() if not s.alive(now)]:
            del self._by_channel[cid]


registry = SessionRegistry()
