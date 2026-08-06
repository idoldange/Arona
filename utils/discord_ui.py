"""
arona/discord_ui.py
──────────────────────────────────────────────────────────────────────────────
Discord UI components used by Arona's interactive tool calls.

Classes
AskUserModal        — Text-input modal opened when the user clicks "Answer"
MalformedRetryView  — Retry/Cancel buttons shown on malformed AI responses
AskUserView         — Choice-button + optional text-input view for ask_user tool
"""

import asyncio
import discord


# text-input modal

class AskUserModal(discord.ui.Modal):
    """Simple text-input modal for free-form answers."""

    def __init__(self, question: str, future: asyncio.Future):
        super().__init__(title="Answer the question")
        self.future = future
        self.answer_input = discord.ui.TextInput(
            label=question[:45],
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer here...",
            required=True,
            max_length=1000,
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not self.future.done():
            self.future.set_result(("text", self.answer_input.value.strip()))


# malformed response recovery

class MalformedRetryView(discord.ui.View):
    """Retry / Cancel buttons shown when the AI returns a malformed response."""

    def __init__(self, future: asyncio.Future, author_id: int = None):
        super().__init__(timeout=120)
        self.future = future
        self.author_id = author_id
        self._sent_message: discord.Message | None = None

        retry_btn = discord.ui.Button(label="Retry", style=discord.ButtonStyle.primary)
        retry_btn.callback = self._retry_callback
        self.add_item(retry_btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._cancel_callback
        self.add_item(cancel_btn)

    async def _retry_callback(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("This interaction isn't for you.", ephemeral=True)
            return
        if not self.future.done():
            self.future.set_result(True)
        await interaction.response.defer()
        if self._sent_message:
            try:
                await self._sent_message.delete()
            except Exception:
                pass

    async def _cancel_callback(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("This interaction isn't for you.", ephemeral=True)
            return
        if not self.future.done():
            self.future.set_result(False)
        await interaction.response.defer()
        if self._sent_message:
            try:
                await self._sent_message.delete()
            except Exception:
                pass

    async def on_timeout(self):
        if not self.future.done():
            self.future.set_result(False)
        if self._sent_message:
            try:
                for item in self.children:
                    item.disabled = True
                await self._sent_message.edit(view=self)
            except Exception:
                pass


# ask_user interactive view

class AskUserView(discord.ui.View):
    """
    Interactive view for the ask_user tool.

    Renders choice buttons and/or an "Answer" button that opens AskUserModal.
    The resolved value is a 2-tuple: ("choice", label) or ("text", user_text).
    """

    def __init__(
        self,
        question: str,
        future: asyncio.Future,
        choices: list = None,
        allow_text: bool = True,
        author_id: int = None,
        other_label: str = None,
    ):
        super().__init__(timeout=None)
        self.question = question
        self.future = future
        self.author_id = author_id
        self._sent_message: discord.Message | None = None
        has_choices = bool(choices)

        if has_choices:
            for label in choices:
                btn = discord.ui.Button(label=label.strip()[:80], style=discord.ButtonStyle.primary)
                btn.callback = self._make_choice_callback(label.strip())
                self.add_item(btn)

        if not has_choices or allow_text:
            fallback_label = (other_label or ("Other" if has_choices else "Answer"))[:80]
            other_btn = discord.ui.Button(
                label=fallback_label,
                style=discord.ButtonStyle.secondary,
            )
            other_btn.callback = self._open_modal_callback
            self.add_item(other_btn)

    def _make_choice_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            if self.author_id and interaction.user.id != self.author_id:
                await interaction.response.send_message("This interaction isn't for you.", ephemeral=True)
                return
            await self._resolve(interaction, "choice", choice)
        return callback

    async def _open_modal_callback(self, interaction: discord.Interaction):
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("This interaction isn't for you.", ephemeral=True)
            return
        original_message = interaction.message
        modal = AskUserModal(self.question, self.future)
        await interaction.response.send_modal(modal)

        async def _wait_and_disable():
            try:
                await self.future
            except Exception:
                pass
            await self._disable_view(original_message)

        asyncio.create_task(_wait_and_disable())

    async def _resolve(self, interaction: discord.Interaction, kind: str, value: str):
        if not self.future.done():
            self.future.set_result((kind, value))
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

    async def _disable_view(self, message: discord.Message):
        for item in self.children:
            item.disabled = True
        try:
            await message.edit(view=self)
        except Exception:
            pass
