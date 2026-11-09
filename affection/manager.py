"""
affection/manager.py
~~~~~~~~~~~~~~~~~~~~
Facade. Import this everywhere.

    from affection import affection

Startup:
    await affection.initialize()
    asyncio.create_task(affection.start())

Per message:
    ctx = await affection.on_interaction(user_id, scope={"is_dm": ...})
    block = affection.build_prompt_block(ctx)      # goes into the system prompt

Model output (stripped from the reply, effects applied):
    text = affection.parse_and_apply_mood_tag(text, user_id=..., scope=..., turn_id=message.id)

Tags understood:
    <mood>joy+8 anxiety+3 | reason</mood>   emotion vector (per-user offset + a little global leak)
    <mood>-5</mood>                         legacy number (N/30 x 0.15 -> joy / sadness)
    <shocked>reason</shocked>               surprise spike (global)
    <bond>N</bond>                          bond change for the current speaker (see bond.adjust)
"""

import re
import time

from config import TAG_DEDUPE_WINDOW
from affection import mood as _mood
from affection import bond as _bond
from affection import special as _special

# Anything between <mood> and </mood>: new emotion syntax or a plain number.
_MOOD_ANY_RE = re.compile(r"<mood>(?P<body>.*?)</mood>", re.IGNORECASE | re.DOTALL)

# Malformed legacy variants the model has been seen to produce (kept from the old parser).
_MOOD_LEGACY_RE = re.compile(
    r"<mood>\s*(?P<v1>[+-]?\d+(?:\.\d+)?)\s*</mood>"    # canonical:  <mood>-5</mood>
    r"|</mood>\s*\[(?P<v2>[+-]?\d+(?:\.\d+)?)\]"        # malformed:  </mood>[-5]
    r"|</mood>\s*(?P<v3>[+-]?\d+(?:\.\d+)?)>"           # malformed:  </mood>-5>
    r"|</mood>\s*(?P<v4>[+-]?\d+(?:\.\d+)?)\s*</mood>"  # malformed:  </mood>25</mood>
    r"|<:mood>\s*(?P<v5>[+-]?\d+(?:\.\d+)?)\s*</mood>", # malformed:  <:mood>30</mood>
    re.IGNORECASE,
)
_STRAY_MOOD_RE = re.compile(r"</?:?mood\s*/?>", re.IGNORECASE)

_SHOCKED_TAG_RE = re.compile(
    r"<shocked>\s*(?P<reason>.+?)\s*</shocked>",   # canonical:  <shocked>Sensei confessed out of nowhere</shocked>
    re.IGNORECASE | re.DOTALL,
)

_BOND_TAG_RE = re.compile(
    r"<bond>\s*(?P<v>[+\-–—]?\s*\d+(?:\.\d+)?)\s*</bond>",   # canonical:  <bond>-4</bond>
    re.IGNORECASE,
)

# (turn_id, kind, normalized content) -> time first applied. The same turn can pass through the parser more than
# once (text sent alongside a tool call, then the final reply); an identical tag must only count once.
_seen: dict[tuple, float] = {}


def _first_time(turn_id, kind: str, content: str, now: float) -> bool:
    if turn_id is None:
        return True   # no turn to tie it to (scheduler): apply
    for k in [k for k, t in _seen.items() if now - t > TAG_DEDUPE_WINDOW]:   # tiny dict, cheap
        del _seen[k]
    key = (turn_id, kind, re.sub(r"\s+", " ", content.strip().lower()))
    if key in _seen and now - _seen[key] <= TAG_DEDUPE_WINDOW:
        return False
    _seen[key] = now
    return True


class AffectionManager:

    async def initialize(self):
        await _mood.initialize()
        await _bond.initialize()

    async def start(self):
        """Pass to asyncio.create_task()."""
        import asyncio
        # Start emotion tick loop
        asyncio.create_task(_mood.tick_loop())
        # Start bond health check loop
        asyncio.create_task(_bond.health_check_loop())
        # Start bond idle-decay loop
        asyncio.create_task(_bond.decay_loop())

    async def shutdown(self):
        """Call during bot shutdown to ensure all pending DB writes complete."""
        await _mood.shutdown()
        await _bond.shutdown()

    # Mood (sync - in-memory)

    def get_mood(self) -> float:
        """Global valence, -100..100 (baseline = 0)."""
        return _mood.get()

    def get_mood_label(self, user_id: int | None = None) -> tuple[str, str]:
        return _mood.label(user_id)

    def describe_mood(self, user_id: int | None = None, is_dm: bool = False) -> dict:
        """Label, blend text, valence, privacy-aware 'why' lines and the full emotion vector."""
        return _mood.describe(user_id, is_dm)

    def nudge_mood(self, delta: float):
        _mood.nudge(delta)

    def punish(self, delta: float):
        """Pass a negative value. Non-blocking."""
        _mood.nudge(delta)

    def parse_and_apply_mood_tag(self, text: str, user_id: int | None = None, scope: dict | None = None,
                                 turn_id=None) -> str:
        """
        Scan a model response for <mood>, <shocked> and <bond> tags, apply them, strip them, return the text.

        user_id  resolved id of the current speaker (None for scheduler/system text: mood goes to the global
                 vector, bond tags are ignored but still stripped)
        scope    {"is_dm": bool, "guild_id": ..., "channel_id": ...}; DM reasons are stored as private
        turn_id  e.g. message.id; an identical tag seen twice in the same turn is applied once
        """
        now = time.time()
        uid = int(user_id) if user_id is not None else None
        is_dm = bool(scope.get("is_dm")) if isinstance(scope, dict) else False

        # <shocked> first: when present it owns `surprise`, a surprise delta in <mood> of the same text is dropped.
        shocked_reason = None
        shocked_match = _SHOCKED_TAG_RE.search(text)
        if shocked_match:
            shocked_reason = shocked_match.group("reason")
        text = _SHOCKED_TAG_RE.sub("", text)

        for match in _MOOD_ANY_RE.finditer(text):
            body = match.group("body")
            if not _first_time(turn_id, "mood", body, now):
                continue
            try:
                deltas, reason, _legacy = _mood.parse_tag_body(body)
                if shocked_reason:
                    deltas.pop("surprise", None)
                if deltas:
                    _mood.apply_emotions(deltas, reason, user_id=uid, is_dm=is_dm, now=now)
            except Exception as e:  # a bad tag must never break the reply
                from console import console
                console.log(f"[mood] tag ignored: {e}", "WARN")
        text = _MOOD_ANY_RE.sub("", text)

        # Malformed legacy leftovers (e.g. '</mood>[-5]')
        for match in _MOOD_LEGACY_RE.finditer(text):
            value = next((g for g in match.groups() if g is not None), None)
            if value is None or not _first_time(turn_id, "mood", "legacy:" + value, now):
                continue
            try:
                deltas, _reason, _legacy = _mood.parse_tag_body(value.replace("—", "-").replace("–", "-"))
                if deltas:
                    _mood.apply_emotions(deltas, "", user_id=uid, is_dm=is_dm, now=now)
            except Exception:
                pass
        text = _MOOD_LEGACY_RE.sub("", text)
        text = _STRAY_MOOD_RE.sub("", text)

        if shocked_reason and _first_time(turn_id, "shocked", shocked_reason, now):
            _mood.trigger_shocked(shocked_reason, user_id=uid, is_dm=is_dm)

        # <bond>N</bond> - the current speaker only; always stripped. If the model sent several, the last wins.
        bond_matches = list(_BOND_TAG_RE.finditer(text))
        if bond_matches:
            if uid is not None:
                raw = bond_matches[-1].group("v").replace("—", "-").replace("–", "-").replace(" ", "")
                try:
                    if _first_time(turn_id, "bond", raw, now):
                        _bond.adjust(uid, float(raw))
                except (ValueError, TypeError):
                    pass
            text = _BOND_TAG_RE.sub("", text)

        return text.strip()

    # Bond (async - DB)

    def get_bond(self, user_id: int) -> float:
        return _bond.get(int(user_id))

    def get_rank(self, user_id: int) -> str:
        return _bond.rank_name(_bond.get(int(user_id)))

    # Per-message event

    async def on_interaction(self, user_id: int, mood_delta: float = 0.0, scope: dict | None = None) -> dict:
        """
        Call once per incoming user message.

        Returns a context dict to be injected into the system prompt:
            {
                "was_sleeping": bool,
                "mood_label":   str,    # halo/label from the collapsed dominant emotion
                "mood_desc":    str,
                "mood_blend":   str,    # e.g. "mostly joy (strong), with some anxiety (slight)"
                "mood_why":     list,   # privacy-aware causes, e.g. 'joy: "Sensei thanked me" (~10m ago)'
                "mood_value":   float,  # valence, kept for compatibility
                "bond_line":    str,
            }
        """
        from console import console
        user_id = int(user_id)
        is_dm = bool(scope.get("is_dm")) if isinstance(scope, dict) else False
        console.log(f"[bond] on_interaction() START user={user_id}", "DEBUG")
        _mood.wake()
        was_sleeping = _mood.just_woke()
        _mood.touch_user(user_id)
        _bond.touch(user_id)  # activity clock + any pending idle decay

        b = _bond.get(user_id)
        console.log(f"[bond] on_interaction() after get(): bond={b:.2f}", "DEBUG")
        current_mood = _mood.get_for(user_id)   # how she feels toward THIS user scales the exp
        exp = _bond.exp_for_message(b, mood=current_mood, mood_delta=mood_delta)
        b = _bond.add_and_get(user_id, exp)  # fire and forget - shutdown waits for all tasks

        if mood_delta != 0.0:
            _mood.nudge(mood_delta)

        d = _mood.describe(user_id, is_dm)
        return {
            "was_sleeping": was_sleeping,
            "mood_label":   d["label"],
            "mood_desc":    d["desc"],
            "mood_blend":   d["blend"],
            "mood_why":     d["why"],
            "mood_value":   d["valence"],
            "bond_line":    _bond.prompt_line(b),
            "special":      _special.prompt_line(user_id),
        }

    # Prompt helpers

    def prompt_line(self, user_id: int) -> str:
        """Single relationship line for the system prompt."""
        return _bond.prompt_line(_bond.get(int(user_id)))

    def build_prompt_block(self, ctx: dict) -> str:
        """
        Convert the dict returned by on_interaction() into a hidden system-prompt block (kept tiny on purpose:
        it is sent with every message). Example:
            <affection>
            Relationship with this user: 25-45 (Bond 31.2/100)
            Mood: happy: mostly joy (strong), with some anxiety (slight). Arona is happy - warm, expressive ...
            Why: joy "Sensei thanked me" (~10m ago); anxiety: the machine ... (~3m ago)
            </affection>
        The general rules (express through tone, never recite, reverse a feeling when its cause is resolved)
        live once in the static system prompt, not here.
        """
        mood = f"Mood: {ctx['mood_label']}: {ctx['mood_blend']}."
        if ctx["mood_label"] != "default":
            mood += " " + ctx["mood_desc"]
        lines = [ctx["bond_line"]]
        if ctx.get("special"):
            lines.append(ctx["special"])
        lines.append(mood)
        if ctx.get("mood_why"):
            lines.append("Why: " + "; ".join(ctx["mood_why"]))
        if ctx["was_sleeping"]:
            lines.append("Just woke up: slightly drowsy, mention at most once.")
        return "<affection>\n" + "\n".join(lines) + "\n</affection>"


affection = AffectionManager()
