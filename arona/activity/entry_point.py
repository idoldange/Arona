"""Keep the Activity "Launch" button hidden on every bot start.

Discord re-creates the default Entry Point command (type 4) whenever Activities are
toggled in the Developer Portal. Deleting it on each start keeps the button gone, so
users can only open the Activity through the button in the bot's embed
(LAUNCH_ACTIVITY, type 12).

To restore the button manually:
    python activity/delete_entry_point.py --restore
"""
from console import console


async def hide_launch_button(bot) -> None:
    """Delete any global Entry Point command (type 4) so the Launch button stays hidden."""
    app_id = bot.application_id
    if not app_id:
        console.log("Activity entry point: application id not ready, skipped.", "WARN")
        return
    try:
        commands = await bot.http.get_global_commands(app_id)
    except Exception as e:
        console.log(f"Activity entry point: could not list commands: {e}", "WARN")
        return
    for cmd in commands:
        if cmd.get("type") != 4:
            continue
        try:
            await bot.http.delete_global_command(app_id, cmd["id"])
            console.log(
                f"Activity entry point: removed {cmd.get('name')!r} (id {cmd['id']}) -> Launch button hidden.",
                "INFO",
            )
        except Exception as e:
            console.log(f"Activity entry point: could not delete {cmd.get('name')!r}: {e}", "WARN")
