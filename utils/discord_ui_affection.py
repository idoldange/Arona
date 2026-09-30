import io
import discord
from config import RANKS
from affection import affection, mood as _mood
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

_MOOD_COLOR = {
    "angered": discord.Color.red(),
    "sad": discord.Color.blue(),
    "default": discord.Color.light_grey(),
    "happy": discord.Color.gold(),
    "motivated": discord.Color.orange(),
    "delighted": discord.Color.magenta(),
    "shocked": discord.Color.purple(),
}


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


def build_bond_embed(user: discord.abc.User) -> discord.Embed:
    uid = resolve_id(user.id)
    bond = affection.get_bond(uid)
    rank = affection.get_rank(uid)
    mood_val = affection.get_mood()
    mood_lbl, _desc = affection.get_mood_label()

    # Bond
    bond_lines = [f"`{_bar(bond / 100.0)}` **{bond:.1f}/100**", f"Rank: **{rank}**"]
    nxt = _next_rank(bond)
    if nxt:
        bond_lines.append(f"Next: **{nxt[0]}** in {nxt[1]:.1f} bond")
    else:
        bond_lines.append("Max bond reached.")

    # Mood (global, shared by everyone)
    mood_lines = [
        f"`{_bar((mood_val + 100.0) / 200.0)}` **{mood_val:+.1f}**",
        f"{_MOOD_EMOJI.get(mood_lbl, '🙂')} **{mood_lbl}**",
    ]
    if _mood.is_shocked():
        mood_lines.append(f"Shocked: {_mood.shocked_reason()}")
    if _mood.is_sleeping():
        mood_lines.append("😴 Sleeping")

    embed = discord.Embed(
        title=f"{user.display_name} × Arona",
        color=_MOOD_COLOR.get(mood_lbl, discord.Color.blurple()),
    )
    embed.add_field(name="💙 Bond", value="\n".join(bond_lines), inline=False)
    embed.add_field(name="🌤️ Arona's mood", value="\n".join(mood_lines), inline=False)
    embed.set_footer(text="Mood is shared across everyone. Bond is yours only.")
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

        text = "\n\n".join(f"**{k}**: {v}" for k, v in data.items())
        if len(text) <= 4000:
            embed = discord.Embed(
                title=f"Saved memory ({len(data)})",
                description=text,
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            plain = "\n\n".join(f"{k}: {v}" for k, v in data.items())
            file = discord.File(io.BytesIO(plain.encode("utf-8")), filename="saved_memory.txt")
            await interaction.response.send_message(
                f"Saved memory ({len(data)} entries) is too long to display, sent as a file.",
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
