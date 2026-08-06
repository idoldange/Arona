"""
──────────────────────────────────────────────────────────────────────────────
Per-channel state for dynamically loaded tool groups.

Arona only gets a small "core" tool set by default. Less-common tool groups
(chess, scheduler, dev/sandbox, github, blue_archive, media, todo, migration)
are loaded on demand via the `load_tools` meta-tool, and stay available for
the next 5 *incoming Discord messages* in that channel before auto-expiring.
Each successful `load_tools` call on an already-loaded group refreshes the
counter back to 5. `unload_tools` removes a group immediately.

Pure in-memory state — no persistence needed, this is just a context-window
optimization, not user data.
──────────────────────────────────────────────────────────────────────────────
"""

TTL_MESSAGES = 5  # how many incoming messages a loaded group survives without being reloaded

# channel_id -> {group_name: messages_remaining}
_loaded: dict[int, dict[str, int]] = {}


def tick_channel(channel_id: int) -> list[str]:
    """
    Call once per incoming Discord message (before building the tool list).
    Decrements TTL for every loaded group in this channel; drops any that hit 0.
    Returns the list of group names that just expired (for logging).
    """
    groups = _loaded.get(channel_id)
    if not groups:
        return []

    expired = []
    for name in list(groups.keys()):
        groups[name] -= 1
        if groups[name] <= 0:
            del groups[name]
            expired.append(name)

    if not groups:
        _loaded.pop(channel_id, None)

    return expired


def load_group(channel_id: int, group_name: str) -> bool:
    """Mark a group as loaded for this channel, (re)setting its TTL to 5 messages."""
    from utils.tool_schemas import TOOL_GROUPS  # local import avoids circular import
    if group_name not in TOOL_GROUPS:
        return False
    _loaded.setdefault(channel_id, {})[group_name] = TTL_MESSAGES
    return True


def unload_group(channel_id: int, group_name: str) -> bool:
    """Remove a group immediately, regardless of remaining TTL."""
    groups = _loaded.get(channel_id)
    if not groups or group_name not in groups:
        return False
    del groups[group_name]
    if not groups:
        _loaded.pop(channel_id, None)
    return True


def unload_all(channel_id: int) -> None:
    _loaded.pop(channel_id, None)


def get_loaded_groups(channel_id: int) -> list[str]:
    return list(_loaded.get(channel_id, {}).keys())


def get_status(channel_id: int) -> dict[str, int]:
    """group_name -> messages remaining, for status/debug display."""
    return dict(_loaded.get(channel_id, {}))