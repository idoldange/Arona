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

Note on deleted channels:
    If the raid bot deleted a channel outright (instead of just renaming it),
    Discord does not expose the old channel or its message history via the API —
    there is nothing to "restore", only to recreate. This module scans the audit
    log for `channel_delete` entries within the time window and recreates each
    channel with the same name, type, category, position, and permission
    overwrites. The new channel starts empty; message history is NOT recovered
    (that's a Discord platform limitation, not something this bot can work around).
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
            "- **Delete junk channels** the raid created (via Audit Log — see *Raider bot ID* below)\n"
            "- Restore channel names that were changed during the raid (via Audit Log)\n"
            "- Recreate channels that were **deleted** during the raid, same name/category/"
            "permissions (message history in those channels cannot be recovered — Discord "
            "does not expose it)\n"
            "- Send an announcement to a designated channel if one is provided\n\n"
            "**Deleted messages and deleted channels cannot be recovered. "
            "Confirm your settings before starting.**"
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
            "The purge will then **only target messages from that bot**, and junk-channel "
            "deletion will **only target channels that bot created**, instead of everything "
            "in the time window.\n"
            "**Leave blank** and the recovery deletes messages/channels from **any** author "
            "in the time window — only use that on a server where you're sure nobody else "
            "was legitimately posting or creating channels during the raid.\n\n"
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
        console.log(
            f"[RAID] Config submitted by {self.invoker} ({self.invoker.id}): "
            f"minutes={minutes} raider_id={raider_id} announce_id={announce_id}",
            "INFO",
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
        console.log(
            f"[RAID] Recovery started by {self.invoker} ({self.invoker.id}) in guild "
            f"'{interaction.guild.name}' ({interaction.guild.id}) — "
            f"minutes={self.minutes} raider_id={self.raider_id} announce_id={self.announce_id}",
            "INFO",
        )
        result = await run_raid_recovery(
            guild=interaction.guild,
            invoker=self.invoker,
            minutes=self.minutes,
            raider_id=self.raider_id,
            announce_id=self.announce_id,
            origin_channel_id=interaction.channel_id,
        )
        embed = _result_embed(result, self.invoker)

        # The interaction token can expire (~15 min) on a large server with many
        # channels, since purge/recreate/rename all run sequentially-ish before
        # we get here. If editing the original response fails, fall back to
        # sending a fresh message in the same channel so the result isn't lost.
        try:
            await interaction.edit_original_response(content=None, embed=embed, view=None)
            console.log(
                f"[RAID] Result delivered via interaction edit to {self.invoker} "
                f"({self.invoker.id})",
                "INFO",
            )
        except discord.HTTPException as e:
            console.log(
                f"[RAID] edit_original_response failed ({e}) — falling back to channel.send",
                "WARN",
            )
            try:
                await interaction.channel.send(
                    content=f"{self.invoker.mention} Raid recovery finished:",
                    embed=embed,
                )
                console.log("[RAID] Result delivered via fallback channel.send", "INFO")
            except discord.HTTPException as e2:
                console.log(f"[RAID] Fallback channel.send also failed: {e2}", "ERROR")

        console.log(
            f"[RAID] Recovery finished for {self.invoker} ({self.invoker.id}) — "
            f"deleted={result['total_deleted']} junk_deleted={len(result['junk_deleted_ok'])} "
            f"renamed={len(result['renamed_ok'])} recreated={len(result['recreated_ok'])} "
            f"errors={len(result['errors'])}",
            "INFO",
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        await interaction.response.edit_message(
            content="Raid recovery cancelled.", embed=None, view=self
        )

    async def on_timeout(self):
        self._disable_all()


def _join_capped(items: list[str], limit: int = 1024, suffix_note: str = "") -> str:
    """
    Join list items with newlines, staying under Discord's 1024-char embed field
    limit. If everything fits (including an optional suffix_note appended after,
    e.g. a caveat line), returns the plain join. Otherwise keeps as many whole
    lines as fit and appends a "+N more" marker instead of truncating mid-line
    or letting embed.add_field() raise on an oversized value.
    """
    if not items:
        return ""
    extra = f"\n{suffix_note}" if suffix_note else ""
    joined = "\n".join(items) + extra
    if len(joined) <= limit:
        return joined

    out: list[str] = []
    total = 0
    for i, item in enumerate(items):
        add = len(item) + (1 if out else 0)  # +1 for the joining newline
        remaining_after = len(items) - i
        reserve = len(f"\n*(+{remaining_after} more)*")
        if total + add + reserve > limit:
            out.append(f"*(+{remaining_after} more)*")
            return "\n".join(out)
        out.append(item)
        total += add
    return "\n".join(out) + extra


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
    if result["junk_deleted_ok"]:
        embed.add_field(
            name="Junk channels deleted",
            value=_join_capped(result["junk_deleted_ok"]),
            inline=False,
        )
    if result["junk_deleted_fail"]:
        embed.add_field(
            name="Could not delete junk channel",
            value=_join_capped(result["junk_deleted_fail"]),
            inline=False,
        )
    if result["renamed_ok"]:
        embed.add_field(
            name="Channel names restored",
            value=_join_capped(result["renamed_ok"]),
            inline=False,
        )
    if result["renamed_fail"]:
        embed.add_field(
            name="Could not rename",
            value=_join_capped(result["renamed_fail"]),
            inline=False,
        )
    if not result["renamed_ok"] and not result["renamed_fail"]:
        embed.add_field(
            name="Channel names",
            value="No channels were renamed within the specified time window.",
            inline=False,
        )
    if result["recreated_ok"]:
        embed.add_field(
            name="Deleted channels recreated",
            value=_join_capped(
                result["recreated_ok"],
                suffix_note="*(message history could not be restored)*",
            ),
            inline=False,
        )
    if result["recreated_fail"]:
        embed.add_field(
            name="Could not recreate",
            value=_join_capped(result["recreated_fail"]),
            inline=False,
        )
    if result.get("announce_sent"):
        embed.add_field(name="Announcement", value="Sent to the designated channel.", inline=False)
    if result.get("errors"):
        embed.add_field(
            name="Errors",
            value=_join_capped(result["errors"]),
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
            if deleted:
                console.log(f"[RAID] purged {len(deleted)} messages in #{channel.name}", "INFO")
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
    console.log(f"[RAID] found {len(renames)} channel rename(s) to restore", "INFO")
    return renames


async def _delete_junk_channels(
    guild: discord.Guild,
    cutoff: datetime,
    raider_id: Optional[int],
    invoker: discord.Member,
    protect_ids: set[int],
) -> tuple[list[str], list[str]]:
    """
    Scan audit log for channels CREATED within the time window — spam/junk
    channels a raid bot dropped — and delete them.

    - If raider_id is given, only deletes channels created by that user/bot.
      If left blank, deletes ANY channel created in the window (aggressive —
      the guide embed warns about this).
    - Channels this module itself created in Step 3 (recreating deleted
      channels) are always skipped — they carry our own "[Raid Recovery]"
      audit reason, so we don't immediately delete what we just rebuilt.
    - protect_ids (announcement channel, the channel the command was run in)
      are never deleted so the recovery flow doesn't cut itself off.

    Returns (deleted_ok, deleted_fail) as display strings for the result embed.
    """
    ok: list[str] = []
    fail: list[str] = []
    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.channel_create,
            limit=AUDIT_LOG_SCAN,
            oldest_first=False,
        ):
            if entry.created_at < cutoff:
                break

            if entry.reason and entry.reason.startswith("[Raid Recovery]"):
                continue  # a channel we ourselves recreated in Step 3

            if raider_id is not None and (entry.user is None or entry.user.id != raider_id):
                continue

            channel_id = entry.target.id
            if channel_id in protect_ids:
                continue

            channel = guild.get_channel(channel_id)
            if channel is None:
                continue  # already gone somehow

            name = channel.name
            try:
                await channel.delete(
                    reason=f"[Raid Recovery] Junk channel removed by {invoker} ({invoker.id})"
                )
                ok.append(f"`{name}`")
                console.log(f"[RAID] deleted junk channel '{name}' ({channel_id})", "INFO")
            except discord.HTTPException as e:
                fail.append(f"`{name}`: {e}")
                console.log(f"[RAID] failed to delete junk channel '{name}': {e}", "WARN")
    except discord.Forbidden:
        console.log("[RAID] Missing View Audit Log permission (channel_create scan)", "WARN")

    console.log(f"[RAID] junk channel cleanup: {len(ok)} deleted, {len(fail)} failed", "INFO")
    return ok, fail


async def _recover_deleted_channels(
    guild: discord.Guild,
    cutoff: datetime,
    invoker: discord.Member,
) -> tuple[list[str], list[str]]:
    """
    Scan audit log for channels DELETED (not just renamed) within the time window
    and recreate each one with the same name, type, category, position, and
    permission overwrites.

    Discord does not expose deleted channels or their message history via the
    API, so this is a recreation, not a restore — the new channel starts empty.
    Returns (recreated_ok, recreated_fail) as display strings for the result embed.
    """
    ok: list[str] = []
    fail: list[str] = []
    try:
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.channel_delete,
            limit=AUDIT_LOG_SCAN,
            oldest_first=True,  # recreate in original creation-ish order
        ):
            if entry.created_at < cutoff:
                continue

            before = entry.before
            name = getattr(before, "name", None)
            if not name:
                continue

            category = getattr(before, "category", None)
            # Skip if something with this name already exists in the same category —
            # avoids double-recreating on a second run, or clashing with a channel
            # the raider didn't touch.
            if discord.utils.get(guild.channels, name=name, category=category) is not None:
                continue

            ch_type    = getattr(before, "type", discord.ChannelType.text)
            # AuditLogDiff.overwrites is a list of (target, PermissionOverwrite)
            # tuples, NOT a dict — guild.create_*_channel() needs a real dict.
            # This copies the exact allow/deny bits per role/member, same as before
            # the raid — EXCEPT for a role/member that no longer exists (deleted
            # role, member left). Audit log falls back to a bare discord.Object for
            # those, and _create_channel can't reliably tell if it was a role or a
            # member, so those specific overwrites are dropped rather than guessed.
            raw_overwrites = getattr(before, "overwrites", None) or []
            overwrites = {
                target: ow for target, ow in raw_overwrites
                if isinstance(target, (discord.Role, discord.Member))
            }
            dropped = len(raw_overwrites) - len(overwrites)
            if dropped:
                console.log(
                    f"[RAID] channel '{name}': dropped {dropped} overwrite(s) for "
                    "role(s)/member(s) that no longer exist",
                    "WARN",
                )
            position   = getattr(before, "position", None)
            reason = (
                f"[Raid Recovery] Recreated by {invoker} ({invoker.id}) — "
                "channel was deleted during raid, message history not recoverable"
            )

            try:
                if ch_type == discord.ChannelType.voice:
                    new_ch = await guild.create_voice_channel(
                        name=name, category=category, overwrites=overwrites, reason=reason,
                    )
                elif ch_type == discord.ChannelType.category:
                    new_ch = await guild.create_category(
                        name=name, overwrites=overwrites, reason=reason,
                    )
                elif ch_type == discord.ChannelType.forum:
                    new_ch = await guild.create_forum(
                        name=name, category=category, overwrites=overwrites, reason=reason,
                    )
                else:
                    new_ch = await guild.create_text_channel(
                        name=name,
                        category=category,
                        overwrites=overwrites,
                        topic=getattr(before, "topic", None),
                        nsfw=getattr(before, "nsfw", False),
                        slowmode_delay=getattr(before, "slowmode_delay", 0) or 0,
                        reason=reason,
                    )

                if position is not None:
                    try:
                        await new_ch.edit(position=position, reason=reason)
                    except discord.HTTPException:
                        pass  # cosmetic — don't fail the recreation over this

                ok.append(f"`{name}` (recreated, empty — history not recoverable)")
                console.log(
                    f"[RAID] recreated deleted channel '{name}' (type={ch_type}, "
                    f"new_id={new_ch.id}, overwrites_applied={len(overwrites)})",
                    "INFO",
                )
            except discord.HTTPException as e:
                fail.append(f"`{name}`: {e}")
                console.log(f"[RAID] failed to recreate channel '{name}': {e}", "WARN")
    except discord.Forbidden:
        console.log("[RAID] Missing View Audit Log permission (channel_delete scan)", "WARN")

    console.log(
        f"[RAID] deleted-channel recovery: {len(ok)} recreated, {len(fail)} failed", "INFO"
    )
    return ok, fail


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
        console.log(f"[RAID] announcement sent to #{channel.name}", "INFO")
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
    origin_channel_id: Optional[int] = None,
) -> dict:
    """
    Main orchestrator. Returns the result dict consumed by _result_embed.

    origin_channel_id: the channel the "!arona raided" flow is running in —
    protected from junk-channel deletion so the recovery UI doesn't vanish
    out from under itself mid-run.
    """
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)

    console.log(
        f"[RAID] run_raid_recovery: guild='{guild.name}' ({guild.id}) "
        f"cutoff={cutoff.isoformat()} raider_id={raider_id} announce_id={announce_id}",
        "INFO",
    )

    result: dict = {
        "total_deleted":     0,
        "channels_scanned":  0,
        "junk_deleted_ok":   [],
        "junk_deleted_fail": [],
        "renamed_ok":        [],
        "renamed_fail":      [],
        "recreated_ok":      [],
        "recreated_fail":    [],
        "announce_sent":     False,
        "errors":            [],
    }

    bot_member = guild.me

    # Step 0 — send announcement first so members see it before messages vanish
    if announce_id:
        console.log("[RAID] Step 0 — sending announcement", "INFO")
        result["announce_sent"] = await _send_announcement(guild, announce_id)

    # Step 1 — delete junk channels the raid created (before purging/renaming
    # anything, so we're not wasting work on channels about to be deleted anyway)
    console.log("[RAID] Step 1 — scanning for junk channels to delete", "INFO")
    protect_ids = {cid for cid in (announce_id, origin_channel_id) if cid is not None}
    result["junk_deleted_ok"], result["junk_deleted_fail"] = await _delete_junk_channels(
        guild, cutoff, raider_id, invoker, protect_ids
    )

    # Step 2 — purge all accessible text channels in parallel
    purgeable = [
        ch for ch in guild.channels
        if isinstance(ch, discord.TextChannel)
        and ch.permissions_for(bot_member).manage_messages
    ]
    result["channels_scanned"] = len(purgeable)
    console.log(f"[RAID] Step 2 — purging up to {PURGE_LIMIT} msgs across "
                f"{len(purgeable)} channel(s)", "INFO")

    purge_counts = await asyncio.gather(
        *[_purge_channel(ch, cutoff, raider_id) for ch in purgeable],
        return_exceptions=True,
    )
    for count in purge_counts:
        if isinstance(count, Exception):
            result["errors"].append(str(count))
            console.log(f"[RAID] purge task raised: {count}", "WARN")
        else:
            result["total_deleted"] += count

    # Step 3 — recreate channels that were deleted outright (message history is
    # unrecoverable via Discord's API, so we only recreate the empty channel)
    console.log("[RAID] Step 3 — scanning for deleted channels to recreate", "INFO")
    result["recreated_ok"], result["recreated_fail"] = await _recover_deleted_channels(
        guild, cutoff, invoker
    )

    # Step 4 — restore channel names using audit log
    console.log("[RAID] Step 4 — scanning for renamed channels to restore", "INFO")
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
            console.log(f"[RAID] renamed '{channel.name}' -> '{original_name}'", "INFO")
        except discord.HTTPException as e:
            result["renamed_fail"].append(f"`{channel.name}`: {e}")
            console.log(f"[RAID] failed to rename channel {ch_id}: {e}", "WARN")

    console.log(
        f"[RAID] done — deleted={result['total_deleted']} "
        f"junk_deleted={len(result['junk_deleted_ok'])} "
        f"renamed={len(result['renamed_ok'])} "
        f"recreated={len(result['recreated_ok'])} invoker={invoker}",
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

    console.log(
        f"[RAID] '!arona raided' invoked by {member} ({member.id}) in guild "
        f"'{guild.name}' ({guild.id})",
        "INFO",
    )

    view = RaidConfigView(invoker=member)
    await message.channel.send(embed=_guide_embed(), view=view)