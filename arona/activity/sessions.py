"""Session cua Activity, quan ly THEO KENH VOICE: chi co session khi ai do bam nut "Open DAW" trong menu `!arona synth`.
Khong co session cho kenh do -> API tra 403 (xem server.py)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

SESSION_TTL_S = 2 * 60 * 60     # het han sau 2 gio khong hoat dong


@dataclass
class Session:
    channel_id: int
    guild_id: int | None
    owner_id: int                       # nguoi bam nut mo Activity
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    users: set = field(default_factory=set)   # user id da goi API thanh cong

    def alive(self, now: float | None = None) -> bool:
        return ((now or time.time()) - self.last_seen) < SESSION_TTL_S

    def touch(self, user_id: int | None = None) -> None:
        self.last_seen = time.time()
        if user_id is not None:
            self.users.add(user_id)


class SessionRegistry:
    def __init__(self):
        self._by_channel: dict[int, Session] = {}

    def create(self, channel_id: int, guild_id: int | None, owner_id: int) -> Session:
        """Moi kenh 1 session; bam nut lan nua thi gia han va giu nguyen session cu."""
        self.purge()
        s = self._by_channel.get(channel_id)
        if s and s.alive():
            s.touch(owner_id)
            return s
        s = Session(channel_id, guild_id, owner_id)
        s.users.add(owner_id)
        self._by_channel[channel_id] = s
        return s

    def get(self, channel_id: int) -> Session | None:
        s = self._by_channel.get(channel_id)
        if s and not s.alive():
            del self._by_channel[channel_id]
            return None
        return s

    def end(self, channel_id: int) -> bool:
        return self._by_channel.pop(channel_id, None) is not None

    def purge(self) -> None:
        now = time.time()
        for cid in [c for c, s in self._by_channel.items() if not s.alive(now)]:
            del self._by_channel[cid]


registry = SessionRegistry()
