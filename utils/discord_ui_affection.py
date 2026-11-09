import io
import discord
from config import RANKS, EMO_BASELINE
from affection import affection, mood as _mood, special as _special
from utils.memory import saved_information
from utils.migration_keys import resolve_id

_MOOD_EMOJI = {
    "angered": "😡",
    "sad": "😢",
    "default": "😐",
    "happy": "😊",
    "motivated": "🔥",
    "delighted": "🥰",
    "shocked": "😱",
}

ARONA_COLOR = discord.Color(0x89C4F4)   # Arona's light blue, always (mood no longer tints the embed)

_MOOD_COLOR = {
    "angered": discord.Color.red(),
    "sad": discord.Color.blue(),
    "default": discord.Color.light_grey(),
    "happy": discord.Color.gold(),
    "motivated": discord.Color.orange(),
    "delighted": discord.Color.magenta(),
    "shocked": discord.Color.purple(),
}


_EMO_EMOJI = {
    "joy": "😊", "warmth": "🤗", "excitement": "🔥", "sadness": "😢", "anger": "😠",
    "anxiety": "😰", "surprise": "😱", "fatigue": "😴", "curiosity": "🧐",
}

_IMPRESSION_KEY = "__impression__"


def _pretty_key(key: str) -> str:
    """user_favorite_food -> User Favorite Food"""
    words = key.replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or key


def _build_text(data: dict, impression: str | None, markdown: bool) -> str:
    parts = []
    if impression:
        parts.append(("### Personalization note\n" if markdown else "[Personalization note]\n") + impression)
    if data:
        entries = [
            (f"**{_pretty_key(k)}**: {v}" if markdown else f"{_pretty_key(k)}: {v}")
            for k, v in data.items()
        ]
        parts.append(("### Saved information\n" if markdown else "[Saved information]\n") + "\n\n".join(entries))
    return "\n\n".join(parts)


def _bar(frac: float, length: int = 10) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * length)
    return "▰" * filled + "▱" * (length - filled)


def _next_rank(bond: float):
    """Returns (next_rank_name, bond_needed) or None if already at the cap."""
    for i, (lo, hi, _name, _mult) in enumerate(RANKS):
        if lo <= bond < hi:
            if i + 1 < len(RANKS):
                return RANKS[i + 1][2], hi - bond
            return None
    return None


def build_bond_embed(user: discord.abc.User, is_dm: bool = False) -> discord.Embed:
    uid = resolve_id(user.id)
    bond = affection.get_bond(uid)
    rank = affection.get_rank(uid)
    d = affection.describe_mood(uid, is_dm)
    mood_val, mood_lbl, vec = d["valence"], d["label"], d["vec"]

    # Bond
    bond_lines = [f"`{_bar(bond / 100.0)}` **{bond:.1f}/100**", f"Rank: **{rank}**"]
    nxt = _next_rank(bond)
    if nxt:
        bond_lines.append(f"Next: **{nxt[0]}** in {nxt[1]:.1f} bond")
    else:
        bond_lines.append("Max bond reached.")

    # Mood: her general state + how she feels toward this user (valence bar is 50% at rest)
    def _safe(text: str) -> str:
        return discord.utils.escape_mentions(discord.utils.escape_markdown(text))

    mood_lines = [
        f"`{_bar((mood_val + 100.0) / 200.0)}` **{mood_val:+.1f}**",
        f"{_MOOD_EMOJI.get(mood_lbl, '🙂')} **{mood_lbl}**: {d['blend']}",
    ]
    top = sorted(vec, key=lambda e: vec[e] - EMO_BASELINE[e], reverse=True)[:4]
    for e in top:
        mood_lines.append(f"{_EMO_EMOJI[e]} {e}: `{_bar(vec[e], 8)}` {vec[e] * 100:.0f}%")
    for line in d["why"]:   # already privacy-filtered: reasons from DMs are masked outside that DM
        mood_lines.append("> " + _safe(line))
    if _mood.is_shocked():
        why = _mood.shocked_reason(uid, is_dm)
        mood_lines.append("Shocked" + (f": {_safe(why)}" if why else ""))
    if _mood.is_sleeping():
        mood_lines.append("😴 Sleeping")

    embed = discord.Embed(
        title=f"{user.display_name}",
        color=ARONA_COLOR,
    )
    embed.add_field(name="💙 Bond", value="\n".join(bond_lines), inline=False)
    _rel = _special.embed_line(uid)
    if _rel:
        embed.add_field(name="💞 Special relationship", value=_safe(_rel)[:1000], inline=False)
    embed.add_field(name="🌤️ Arona's mood", value="\n".join(mood_lines)[:1020], inline=False)
    embed.set_footer(text="Mood = Arona's general state plus how she feels toward you. Bond is yours only.")
    return embed


class AffectionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.message: discord.Message | None = None

    @discord.ui.button(label="Saved memory", emoji="🧠", style=discord.ButtonStyle.secondary)
    async def show_memory(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Whoever clicks sees THEIR OWN saved info, and only they can see the reply (ephemeral).
        uid = resolve_id(interaction.user.id)
        data = saved_information.get(uid)
        if not data:
            await interaction.response.send_message("Arona hasn't saved anything about you yet.", ephemeral=True)
            return

        data = dict(data)
        total = len(data)
        impression = data.pop(_IMPRESSION_KEY, None)
        text = _build_text(data, impression, markdown=True)
        if len(text) <= 4000:
            embed = discord.Embed(
                title=f"Saved memory ({total})",
                description=text,
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            plain = _build_text(data, impression, markdown=False)
            file = discord.File(io.BytesIO(plain.encode("utf-8")), filename="saved_memory.txt")
            await interaction.response.send_message(
                f"Saved memory ({total} entries) is too long to display, sent as a file.",
                file=file,
                ephemeral=True,
            )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
