"""Hide the Activity "Launch" button by deleting its Entry Point command (type 4).

Activities stay enabled; users can only open the Activity through the button in the
bot's embed (the bot replies with LAUNCH_ACTIVITY, type 12).

Run once:
    python activity/delete_entry_point.py --status    (read-only, changes nothing)
    python activity/delete_entry_point.py             (delete -> hide the Launch button)
    python activity/delete_entry_point.py --restore   (recreate the Launch button)

Requires DISCORD_TOKEN and ACTIVITY_CLIENT_ID (or DISCORD_APP_ID) in .env / environment."""
import json
import os
import sys
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API = "https://discord.com/api/v10"
token = os.environ.get("DISCORD_TOKEN")
app_id = os.environ.get("ACTIVITY_CLIENT_ID") or os.environ.get("DISCORD_APP_ID")
if not token or not app_id:
    sys.exit("Missing DISCORD_TOKEN or ACTIVITY_CLIENT_ID")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        method=method,
        data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "arona-activity-setup (1.0)",
        },
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


commands = call("GET", f"/applications/{app_id}/commands") or []
entry = next((c for c in commands if c.get("type") == 4), None)

if "--status" in sys.argv:
    if entry:
        print(f"Entry Point: {entry['name']} (id {entry['id']}), handler={entry.get('handler')} -> Launch button is VISIBLE.")
    else:
        print("No Entry Point command (type 4) -> Launch button is HIDDEN.")
elif "--restore" in sys.argv:
    if entry:
        print(f"Entry Point already exists: {entry['name']} (id {entry['id']}) - nothing to recreate.")
    else:
        body = {
            "name": "launch",
            "description": "Launch an activity",
            "type": 4,
            "handler": 2,
            "contexts": [0, 1, 2],
            "integration_types": [0, 1],
        }
        res = call("POST", f"/applications/{app_id}/commands", body)
        print(f"Recreated Entry Point: {res['name']} (id {res['id']}, handler={res.get('handler')}). Launch button is visible again.")
else:
    if not entry:
        print("No Entry Point command (type 4) - Launch button is already hidden, nothing to do.")
    else:
        call("DELETE", f"/applications/{app_id}/commands/{entry['id']}")
        print(f"Deleted Entry Point: {entry['name']} (id {entry['id']}). Launch button is now hidden.")
        print("Activities stay enabled; the Activity can only be opened via the button in the embed (LAUNCH_ACTIVITY type 12).")
        print("To show it again: python activity/delete_entry_point.py --restore")
