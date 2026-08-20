import asyncio
import aiohttp
import discord
from utils import apikeys
from utils.migration_keys import resolve_id

TUTORIAL_URL = "https://youtu.be/PdOuGVz0ZIw?si=MbFpJgD0iy9uHvYU&t=10" #not my vid


class ApiKeyModal(discord.ui.Modal, title="Add Gemini API key(s)"):
    keys_input = discord.ui.TextInput(
        label="Key(s), comma separated",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Validation calls the Gemini API, so defer first to avoid the 3s interaction timeout.
        await interaction.response.defer(ephemeral=True, thinking=True)

        user_id = resolve_id(interaction.user.id)
        candidates = apikeys.split_raw_keys(str(self.keys_input.value))
        if not candidates:
            await interaction.followup.send("No keys found in the input.", ephemeral=True)
            return

        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[apikeys.validate_key(session, k) for k in candidates])

        valid_keys = []
        invalid_entries = []  # (key, reason)
        for k, (ok, reason) in zip(candidates, results):
            if ok:
                valid_keys.append(k)
            else:
                # validate_key now only returns ok=False on 400/401/403 (a genuinely
                # bad/revoked key), never on transient errors (429/5xx/timeout) — so
                # showing the full key here is safe, it's already dead and the user
                # is the only one who can see this ephemeral message anyway.
                invalid_entries.append((k, reason))

        total_keys = None
        if valid_keys:
            total_keys = apikeys.add_keys(user_id, ",".join(valid_keys))
            # Newly added key(s) may fix a previously "exhausted" BYOK state (e.g. their old
            # key(s) died, they just pasted in fresh ones) — clear the "route straight to free
            # pool" flag and the remembered fallback model so their next request actually
            # tries the new key(s) instead of skipping past them until midnight Pacific.
            # Deferred import: main.py imports this module (for build_addkey_embed), so a
            # top-level import here would be circular — importing inside the handler, once
            # both modules are already fully loaded, avoids that.
            try:
                import main
                _uid = str(user_id)
                main._BYOK_OWN_KEYS_EXHAUSTED.pop(_uid, None)
                main._BYOK_LAST_WORKING_MODEL.pop(_uid, None)
            except Exception:
                pass

        lines = []
        if valid_keys:
            lines.append(f"Added {len(valid_keys)} key(s). Total: {len(total_keys)}.")
        if invalid_entries:
            lines.append("Rejected (invalid key):")
            for k, reason in invalid_entries:
                lines.append(f"`{k}` - {reason}")

        embed = discord.Embed(
            title="Add key result",
            description="\n".join(lines),
            color=discord.Color.green() if valid_keys and not invalid_entries else discord.Color.red() if invalid_entries and not valid_keys else discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class ApiKeyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(discord.ui.Button(label="How to get a key", style=discord.ButtonStyle.link, url=TUTORIAL_URL))

    @discord.ui.button(label="Enter key(s)", style=discord.ButtonStyle.primary)
    async def enter_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApiKeyModal())


class ListKeysView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Show my keys", style=discord.ButtonStyle.primary)
    async def show_keys(self, interaction: discord.Interaction, button: discord.ui.Button):
        keys = apikeys.get_keys(resolve_id(interaction.user.id))
        if not keys:
            await interaction.response.send_message("You have no keys saved.", ephemeral=True)
            return
        lines = [f"{i}. {apikeys.mask_key(k)}" for i, k in enumerate(keys, start=1)]
        embed = discord.Embed(title="Your keys", description="\n".join(lines), color=discord.Color.blurple())
        embed.set_footer(text="Use !arona removekey <index> to remove one")
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_addkey_embed() -> tuple[discord.Embed, "ApiKeyView"]:
    embed = discord.Embed(
        title="Add your own Gemini API key",
        description="Enter your key(s) below to skip the daily free-tier limit (don't worry, it's free).\nMultiple keys can be entered, comma separated. New keys are added to your existing ones.",
        color=discord.Color.blurple(),
    )
    return embed, ApiKeyView()


def build_listkeys_embed() -> tuple[discord.Embed, "ListKeysView"]:
    embed = discord.Embed(
        title="Your API keys",
        description="Click below to view your saved keys (only visible to you).",
        color=discord.Color.blurple(),
    )
    return embed, ListKeysView()