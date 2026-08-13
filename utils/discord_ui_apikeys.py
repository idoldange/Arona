import discord
from utils import apikeys

TUTORIAL_URL = "https://youtu.be/PdOuGVz0ZIw?si=MbFpJgD0iy9uHvYU&t=10"


class ApiKeyModal(discord.ui.Modal, title="Add Gemini API key(s)"):
    keys_input = discord.ui.TextInput(
        label="Key(s), comma separated",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        keys = apikeys.add_keys(interaction.user.id, str(self.keys_input.value))
        embed = discord.Embed(
            title="Key(s) added",
            description=f"You now have {len(keys)} key(s) total. Requests will use your own key(s), no daily limit applied by Arona.\n\nUse at your own risk — you are responsible for how these keys are used.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
        keys = apikeys.get_keys(interaction.user.id)
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
        description="Enter your key(s) below to skip the daily free-tier limit (don't worry, it's free).\nMultiple keys can be entered, comma separated. New keys are added to your existing ones.\n\nUse at your own risk — you are responsible for how these keys are used.",
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
