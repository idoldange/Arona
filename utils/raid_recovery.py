"""
raid_recovery.py — Emergency anti-raid module for Arona Bot
═══════════════════════════════════════════════════════════
Integration (main.py):
    from raid_recovery import handle_raided_command

    # inside on_message:
    if message.content.lower().startswith("!arona raided"):
        await handle_raided_command(message)
        return

Required permissions:
    User : Administrator
    Bot  : Manage Messages, Manage Channels, View Audit Log
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord

try:
    from console import console
except ImportError:
    import logging
    class _FallbackConsole:
        def log(self, msg, level="INFO"):
            logging.getLogger("raid_recovery").info(f"[{level}] {msg}")
    console = _FallbackConsole()

# constants
PURGE_LIMIT       = 60           # max messages deleted per channel
CHANNEL_SEMAPHORE = asyncio.Semaphore(5)  # max concurrent channel operations
AUDIT_LOG_SCAN    = 200          # audit log entries to scan
MAX_MINUTES       = 1440         # 24 hours


# guide embed
def _guide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Raid Recovery",
        description=(
            "This command will automatically:\n"
            "- Delete up to **60 recent messages** per channel within the specified time window\n"
            "- Restore channel names that were changed during the raid (via Audit Log)\n"
            "- Send an announcement to a designated channel if one is provided\n\n"
            "**Deleted messages cannot be recovered. Confirm your settings before starting.**"
        ),
        color=discord.Color.red(),
    )
    embed.add_field(
        name="Time window (minutes)",
        value=(
            "How many minutes ago the raid started.\n"
            "Example: if the raid began 20 minutes ago, enter `20`.\n"
            "Maximum: `1440` (24 hours)."
        ),
        inline=False,
    )
    embed.add_field(
        name="Raider bot ID  *(optional)*",
        value=(
            "If you know the ID of the bot used to raid, enter it here.\n"
            "The purge will then **only target messages from that bot** instead of all messages.\n\n"
            "**How to get a user ID:** Enable Developer Mode under "
            "Settings > Advanced > Developer Mode, then right-click the bot and select "
            "**Copy User ID**."
        ),
        inline=False,
    )
    embed.add_field(
        name="Announcement channel ID  *(optional)*",
        value=(
            "The bot will post an announcement in this channel so members know "
            "what is happening and do not panic.\n"
            "The message will read: *\"This server was just raided. Please stay calm — "
            "admins are restoring the server. Do not click any suspicious links.\"*\n\n"
            "**How to get a channel ID:** Right-click the channel and select "
            "**Copy Channel ID**."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Click the button below to open the configuration form. "
             "Only the user who ran this command can interact with it."
    )
    return embed


# configuration modal
class RaidConfigModal(discord.ui.Modal, title="Raid Recovery — Configuration"):
    time_window = discord.ui.TextInput(
        label="Time window (minutes)",
        placeholder="e.g. 30  —  deletes messages from the last 30 minutes",
        required=True,
        max_length=4,
    )
    raider_bot_id = discord.ui.TextInput(
        label="Raider bot ID  (leave blank = delete all)",
        placeholder="e.g. 123456789012345678",
        required=False,
        max_length=20,
    )
    announce_channel_id = discord.ui.TextInput(
        label="Announcement channel ID  (leave blank = skip)",
        placeholder="e.g. 987654321098765432",
        required=False,
        max_length=20,
    )

    def __init__(self, invoker: discord.Member):
        super().__init__()
        self.invoker = invoker

    async def on_submit(self, interaction: discord.Interaction):
        # Validate: time_window
        try:
            minutes = int(self.time_window.value.strip())
            if not 1 <= minutes <= MAX_MINUTES:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                f"Invalid time window. Enter a whole number between 1 and {MAX_MINUTES}.",
                ephemeral=True,
            )
            return

        # Validate: raider_bot_id
        raider_id: Optional[int] = None
        if self.raider_bot_id.value.strip():
            try:
                raider_id = int(self.raider_bot_id.value.strip())
            except ValueError:
                await interaction.response.send_message(
                    "Invalid raider bot ID — must be a numeric snowflake.", ephemeral=True
                )
                return

        # Validate: announce_channel_id
        announce_id: Optional[int] = None
        if self.announce_channel_id.value.strip():
            try:
                announce_id = int(self.announce_channel_id.value.strip())
            except ValueError:
                await interaction.response.send_message(
                    "Invalid announcement channel ID — must be a numeric snowflake.",
                    ephemeral=True,
                )
                return
            if interaction.guild.get_channel(announce_id) is None:
                await interaction.response.send_message(
                    f"No channel with ID `{announce_id}` found in this server.",
                    ephemeral=True,
                )
                return

        embed = _confirm_embed(minutes, raider_id, announce_id)
        view  = RaidStartView(
            invoker=self.invoker,
            minutes=minutes,
            raider_id=raider_id,
            announce_id=announce_id,
        )
        await interaction.response.send_message(embed=embed, view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        console.log(f"[RAID] Modal error: {error}", "ERROR")
        await interaction.response.send_message(
            "An error occurred while processing the form.", ephemeral=True
        )


def _confirm_embed(
    minutes: int,
    raider_id: Optional[int],
    announce_id: Optional[int],
) -> discord.Embed:
    embed = discord.Embed(
        title="Confirm — Raid Recovery",
        description=(
            "Review the settings below before starting.\n"
            "**This action cannot be undone.**"
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="Time window",
        value=f"`{minutes}` minutes  (up to {PURGE_LIMIT} messages per channel)",
        inline=True,
    )
    embed.add_field(
        name="Filter by",
        value=f"<@{raider_id}> (`{raider_id}`)" if raider_id else "All authors",
        inline=True,
    )
    embed.add_field(
        name="Announcement channel",
        value=f"<#{announce_id}>" if announce_id else "None",
        inline=True,
    )
    embed.set_footer(text="Start  —  begin recovery  |  Cancel  —  abort")
    return embed


# guide view (opens modal)
class RaidConfigView(discord.ui.View):
    """Attached to the guide embed. Single button that opens the config modal."""

    def __init__(self, invoker: discord.Member):
        super().__init__(timeout=120)
        self.invoker = invoker

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message(
                "Only the user who ran this command can interact with this.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Configure and Start", style=discord.ButtonStyle.danger)
    async def open_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RaidConfigModal(invoker=self.invoker))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# confirm view (start / cancel)
class RaidStartView(discord.ui.View):
    def __init__(
        self,
        invoker: discord.Member,
        minutes: int,
        raider_id: Optional[int],
        announce_id: Optional[int],
    ):
        super().__init__(timeout=60)
        self.invoker     = invoker
        self.minutes     = minutes
        self.raider_id   = raider_id
        self.announce_id = announce_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message(
                "Only the user who ran this command can interact with this.", ephemeral=True
            )
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Start", style=discord.ButtonStyle.danger)
    async def start_recovery(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        await interaction.response.edit_message(
            content="**Running raid recovery — please wait.**",
            embed=None,
            view=self,
        )
        result = await run_raid_recovery(
            guild=interaction.guild,
            invoker=self.invoker,
            minutes=self.minutes,
            raider_id=self.raider_id,
            announce_id=self.announce_id,
        )
        await interaction.edit_original_response(
            content=None,
            embed=_result_embed(result, self.invoker),
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        await interaction.response.edit_message(
            content="Raid recovery cancelled.", embed=None, view=self
        )

    async def on_timeout(self):
        self._disable_all()


def _result_embed(result: dict, invoker: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="Raid Recovery Complete",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Messages deleted",
        value=f"**{result['total_deleted']}** across **{result['channels_scanned']}** channels",
        inline=True,
    )
    if result["renamed_ok"]:
        embed.add_field(
            name="Channel names restored",
            value="\n".join(result["renamed_ok"]),
            inline=False,
        )
    if result["renamed_fail"]:
        embed.add_field(
            name="Could not rename",
            value="\n".join(result["renamed_fail"]),
            inline=False,
        )
    if not result["renamed_ok"] and not result["renamed_fail"]:
        embed.add_field(
            name="Channel names",
            value="No channels were renamed within the specified time window.",
            inline=False,
        )
    if result.get("announce_sent"):
        embed.add_field(name="Announcement", value="Sent to the designated channel.", inline=False)
    if result.get("errors"):
        embed.add_field(
            name="Errors",
            value="\n".join(result["errors"][:5]),
            inline=False,
        )
    embed.set_footer(text=f"Executed by {invoker.display_name} ({invoker.id})")
    return embed


# core logic
async def _purge_channel(
    channel: discord.TextChannel,
    cutoff: datetime,
    raider_id: Optional[int],
) -> int:
    """Delete up to PURGE_LIMIT messages in one channel. Returns the count deleted."""
    async with CHANNEL_SEMAPHORE:
        try:
            def _check(m: discord.Message) -> bool:
                if m.created_at < cutoff:
                    return False
                if raider_id is not None:
                    return m.author.id == raider_id
                return True

            deleted = await channel.purge(limit=PURGE_LIMIT, check=_check, bulk=True)
            return len(deleted)
        except discord.Forbidden:
            return 0
        except discord.HTTPException as e:
            console.log(f"[RAID] purge error in #{channel.name}: {e}", "WARN")
            return 0


async def _recover_channel_names(
    guild: discord.Guild,
    cutoff: datetime,
) -> dict[int, str]:
    """
    Scan audit log for channel renames within the time window.
    Returns {channel_id: original_name}.

    Iterates newest-first and overwrites on each hit, so the final value for
    each channel is the oldest `before` name seen — i.e. the name before the raid.
    """
    renames: dict[int, str] = {}
    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.channel_update,
            limit=AUDIT_LOG_SCAN,
            oldest_first=False,
        ):
            if entry.created_at < cutoff:
                break
            if not (hasattr(entry.before, "name") and hasattr(entry.after, "name")):
                continue
            if entry.before.name == entry.after.name:
                continue
            renames[entry.target.id] = entry.before.name
    except discord.Forbidden:
        console.log("[RAID] Missing View Audit Log permission", "WARN")
    return renames


async def _send_announcement(guild: discord.Guild, channel_id: int) -> bool:
    """Post the raid announcement embed. Returns True on success."""
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False
    embed = discord.Embed(
        title="This server was just raided",
        description=(
            "Please stay calm — the admins are actively restoring the server.\n\n"
            "**Do not click any links** sent during the past few minutes.\n"
            "**Do not follow instructions** from unknown accounts or bots.\n\n"
            "Everything will be back to normal shortly. Thank you for your patience."
        ),
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Automated alert from the server protection system")
    try:
        await channel.send(embed=embed)
        return True
    except discord.HTTPException as e:
        console.log(f"[RAID] Failed to send announcement: {e}", "WARN")
        return False


async def run_raid_recovery(
    guild: discord.Guild,
    invoker: discord.Member,
    minutes: int,
    raider_id: Optional[int],
    announce_id: Optional[int],
) -> dict:
    """Main orchestrator. Returns the result dict consumed by _result_embed."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)

    result: dict = {
        "total_deleted":    0,
        "channels_scanned": 0,
        "renamed_ok":       [],
        "renamed_fail":     [],
        "announce_sent":    False,
        "errors":           [],
    }

    bot_member = guild.me

    # Step 0 — send announcement first so members see it before messages vanish
    if announce_id:
        result["announce_sent"] = await _send_announcement(guild, announce_id)

    # Step 1 — purge all accessible text channels in parallel
    purgeable = [
        ch for ch in guild.channels
        if isinstance(ch, discord.TextChannel)
        and ch.permissions_for(bot_member).manage_messages
    ]
    result["channels_scanned"] = len(purgeable)

    purge_counts = await asyncio.gather(
        *[_purge_channel(ch, cutoff, raider_id) for ch in purgeable],
        return_exceptions=True,
    )
    for count in purge_counts:
        if isinstance(count, Exception):
            result["errors"].append(str(count))
        else:
            result["total_deleted"] += count

    # Step 2 — restore channel names using audit log
    renames = await _recover_channel_names(guild, cutoff)

    for ch_id, original_name in renames.items():
        channel = guild.get_channel(ch_id)
        if channel is None or channel.name == original_name:
            continue
        try:
            await channel.edit(
                name=original_name,
                reason=f"[Raid Recovery] Restored by {invoker} ({invoker.id})",
            )
            result["renamed_ok"].append(f"`{channel.name}` -> `{original_name}`")
        except discord.HTTPException as e:
            result["renamed_fail"].append(f"`{channel.name}`: {e}")

    console.log(
        f"[RAID] done — deleted={result['total_deleted']} "
        f"renamed={len(result['renamed_ok'])} invoker={invoker}",
        "INFO",
    )
    return result


# entry point
async def handle_raided_command(message: discord.Message) -> None:
    """
    Called from on_message in main.py when '!arona raided' is detected.

    main.py integration:
        from raid_recovery import handle_raided_command
        ...
        if message.content.lower().startswith("!arona raided"):
            await handle_raided_command(message)
            return
    """
    member = message.author
    guild  = message.guild

    if guild is None:
        await message.channel.send("This command can only be used inside a server.")
        return

    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        await message.channel.send(
            "This command requires the **Administrator** permission.",
            delete_after=10,
        )
        return

    bot_perms = guild.me.guild_permissions
    missing = []
    if not bot_perms.manage_messages: missing.append("`Manage Messages`")
    if not bot_perms.manage_channels: missing.append("`Manage Channels`")
    if not bot_perms.view_audit_log:  missing.append("`View Audit Log`")
    if missing:
        await message.channel.send(
            f"Bot is missing required permissions: {', '.join(missing)}\n"
            "Grant those permissions and try again."
        )
        return

    view = RaidConfigView(invoker=member)
    await message.channel.send(embed=_guide_embed(), view=view)
