"""Doi handler cua Entry Point command (nut Activity o launcher) sang APP_HANDLER (1) -> Discord khong tu launch nua, bot tu quyet dinh.
Chay 1 lan:  python activity/set_entry_point_handler.py            (APP_HANDLER, chan mo thu cong)
             python activity/set_entry_point_handler.py --restore  (tra ve DISCORD_LAUNCH_ACTIVITY)
Can DISCORD_TOKEN va ACTIVITY_CLIENT_ID (hoac DISCORD_APP_ID) trong .env / bien moi truong."""
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
    sys.exit("Thieu DISCORD_TOKEN hoac ACTIVITY_CLIENT_ID")
handler = 2 if "--restore" in sys.argv else 1


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bot {token}", "Content-Type": "application/json", "User-Agent": "arona-activity-setup (1.0)"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


entry = [c for c in call("GET", f"/applications/{app_id}/commands") if c.get("type") == 4]
if not entry:
    sys.exit("Khong thay Entry Point command (type 4). Bat Activities trong Developer Portal truoc (se tu tao lenh 'Launch').")
cmd = entry[0]
print(f"Entry Point: {cmd['name']} (id {cmd['id']}), handler hien tai = {cmd.get('handler')}")
res = call("PATCH", f"/applications/{app_id}/commands/{cmd['id']}", {"handler": handler})
print(f"Da dat handler = {res.get('handler')} ({'APP_HANDLER' if res.get('handler') == 1 else 'DISCORD_LAUNCH_ACTIVITY'})")
