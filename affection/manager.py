"""
affection/manager.py
~~~~~~~~~~~~~~~~~~~~
Facade. Import this everywhere.

    from affection import affection

Startup:
    await affection.initialize()
    asyncio.create_task(affection.start())

Per message:
    ctx = await affection.on_interaction(message.author.id)
    # ctx is a dict injected into the system prompt — pass it to get_arona_prompt()

Mood penalty:
    affection.punish(-15.0)

System prompt line:
    line = affection.prompt_line(message.author.id)
"""

import re
import time
from affection import mood as _mood
from affection import bond as _bond

_MOOD_TAG_RE = re.compile(
    r"<mood>\s*(?P<v1>[+-]?\d+(?:\.\d+)?)\s*</mood>"   # canonical:  <mood>-5</mood>
    r"|</mood>\s*\[(?P<v2>[+-]?\d+(?:\.\d+)?)\]"        # malformed:  </mood>[-5]
    r"|</mood>\s*(?P<v3>[+-]?\d+(?:\.\d+)?)>"           # malformed:  </mood>-5>
    r"|</mood>\s*(?P<v4>[+-]?\d+(?:\.\d+)?)\s*</mood>", # malformed:  </mood>25</mood>
    re.IGNORECASE,
)

_SHOCKED_TAG_RE = re.compile(
    r"<shocked>\s*(?P<reason>.+?)\s*</shocked>",   # canonical:  <shocked>Sensei confessed out of nowhere</shocked>
    re.IGNORECASE | re.DOTALL,
)


class AffectionManager:

    async def initialize(self):
        await _mood.initialize()
        await _bond.initialize()

    async def start(self):
        """Pass to asyncio.create_task()."""
        import asyncio
        # Start mood tick loop
        asyncio.create_task(_mood.tick_loop())
        # Start bond health check loop
        asyncio.create_task(_bond.health_check_loop())

    async def shutdown(self):
        """Call during bot shutdown to ensure all pending DB writes complete."""
        await _bond.shutdown()

    # Mood (sync — in-memory)

    def get_mood(self) -> float:
        return _mood.get()

    def get_mood_label(self) -> tuple[str, str]:
        return _mood.label()

    def nudge_mood(self, delta: float):
        _mood.nudge(delta)

    def punish(self, delta: float):
        """Pass a negative value. Non-blocking."""
        _mood.nudge(delta)

    def parse_and_apply_mood_tag(self, text: str) -> str:
        """
        Scan model response for <mood>+12</mood> or <mood>-8</mood> tags, and for
        the separate <shocked>reason</shocked> overlay tag. Applies both, then
        strips all tags from the text. Returns the cleaned text to send to the user.

        The model should be instructed (in system prompt) to use these tags
        whenever the conversation warrants it, e.g.:
            <mood>-15</mood>              when the user is rude
            <mood>+8</mood>               when something nice happens
            <shocked>reason here</shocked>  when something genuinely shocks her
        Tags are invisible to the user after stripping.
        """
        for match in _MOOD_TAG_RE.finditer(text):
            try:
                delta = float((match.group("v1") or match.group("v2") or match.group("v3") or match.group("v4")).replace("\u2014", "-").replace("\u2013", "-"))
                # Clamp per-response delta to a reasonable range
                delta = max(-30.0, min(30.0, delta))
                _mood.nudge(delta)
            except ValueError:
                pass
        text = _MOOD_TAG_RE.sub("", text)

        shocked_match = _SHOCKED_TAG_RE.search(text)
        if shocked_match:
            _mood.trigger_shocked(shocked_match.group("reason"))
        text = _SHOCKED_TAG_RE.sub("", text)

        return text.strip()

    # Bond (async — DB)

    def get_bond(self, user_id: int) -> float:
        return _bond.get(int(user_id))

    def get_rank(self, user_id: int) -> str:
        return _bond.rank_name(_bond.get(int(user_id)))

    # Per-message event

    async def on_interaction(self, user_id: int, mood_delta: float = 0.0) -> dict:
        """
        Call once per incoming user message.

        Returns a context dict to be injected into the system prompt:
            {
                "was_sleeping": bool,
                "mood_label":   str,
                "mood_value":   float,
                "bond_line":    str,
            }
        """
        from console import console
        user_id = int(user_id)
        console.log(f"[bond] on_interaction() START user={user_id}", "DEBUG")
        _mood.wake()
        was_sleeping = _mood.just_woke()

        b = _bond.get(user_id)
        console.log(f"[bond] on_interaction() after get(): bond={b:.2f}", "DEBUG")
        current_mood = _mood.get()
        exp = _bond.exp_for_message(b, mood=current_mood, mood_delta=mood_delta)
        b = _bond.add_and_get(user_id, exp)  # fire and forget — shutdown waits for all tasks

        if mood_delta != 0.0:
            _mood.nudge(mood_delta)

        lbl, desc = _mood.label()
        return {
            "was_sleeping": was_sleeping,
            "mood_label":   lbl,
            "mood_desc":  desc,
            "mood_value":   _mood.get(),
            "bond_line":    _bond.prompt_line(b),
        }

    # Prompt helpers

    def prompt_line(self, user_id: int) -> str:
        """Single relationship line for the system prompt."""
        return _bond.prompt_line(_bond.get(int(user_id)))

    def build_prompt_block(self, ctx: dict) -> str:
        """
        Convert the dict returned by on_interaction() into a hidden
        system-prompt block. Wrap this in your system instruction.

        Example output injected into prompt:
            <affection>
            Relationship with this user: Friend (Bond 31.2/100)
            Arona's current mood: cheerful (22.4)
            Arona just woke up from sleep. React naturally as if just woken up.
            </affection>
        """
        lines = [
            ctx["bond_line"],
            f"Arona's current mood: {ctx['mood_label']} ({ctx['mood_value']:.1f}) — {ctx['mood_desc']}",
        ]
        if ctx["was_sleeping"]:
            lines.append("Arona recently woke up — she may seem slightly drowsy. Mention it at most once subtly, do not dwell on it.")
        return "<affection>\n" + "\n".join(lines) + "\n</affection>"


affection = AffectionManager()