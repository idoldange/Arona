"""Gui thong bao update len Discord qua webhook.

URL webhook lay tu bien moi truong DISCORD_WEBHOOK_URL, hoac tu file .env (canh script).
Noi dung (mac dinh .discord_msg.txt, do generate_msg.py tao ra tu the <message>) duoc cat thanh cac tin <= 2000 ky tu
(Discord gioi han 2000/tin) va gui lan luot. Khong bao gio ping @everyone/role/user.

Dung:  python notify_discord.py [file]        (file mac dinh: .discord_msg.txt)
Exit code: 0 = xong (hoac khong co gi de gui / chua cau hinh URL), 1 = gui loi.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 2000                      # gioi han ky tu cua 1 tin nhan Discord
USERNAME = "Arona Update"
DEFAULT_FILE = ".discord_msg.txt"
ENV_NAME = "DISCORD_WEBHOOK_URL"
ENV_PATHS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")]
GAP_SECONDS = 0.6                 # nghi giua cac tin de khong cham rate limit


def read_env_var(name):
    """os.environ truoc, sau do cac file .env (KEY=VALUE, bo qua comment, bo dau nhay)."""
    v = os.environ.get(name)
    if v and v.strip():
        return v.strip()
    for path in ENV_PATHS:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].lstrip()
                    key, sep, val = line.partition("=")
                    if sep and key.strip() == name:
                        val = val.strip().strip('"').strip("'").strip()
                        if val:
                            return val
        except OSError:
            continue
    return None


def commit_link():
    """Link commit HEAD (GitHub) dang markdown [id](url). None neu khong xac dinh duoc."""
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        raw = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None
    m = re.match(r"https?://(?:www\.)?github\.com/([^/]+/[^/.]+)", raw) or re.match(r"git@github\.com:([^/]+/[^/.]+?)(?:\.git)?$", raw)
    url = f"https://github.com/{m.group(1)}/commit/{sha}" if m and sha else None
    return f"[{sha[:7]}]({url})" if url else None


def valid_webhook_url(url):
    """Chi chap nhan webhook that cua Discord (tranh gui noi dung toi URL la)."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and (host == "discord.com" or host.endswith(".discord.com")
                                     or host == "discordapp.com" or host.endswith(".discordapp.com"))
            and u.path.startswith("/api/webhooks/"))


def split_message(text, limit=LIMIT):
    """Cat text thanh cac doan <= limit ky tu, uu tien cat o dong trong, roi xuong dong, roi dau cach."""
    text = text.strip()
    chunks = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 4:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return [c for c in chunks if c]


def post_message(url, content, retries=3):
    """POST 1 tin. Tu cho khi bi rate limit (429). Tra ve True neu gui duoc."""
    body = json.dumps({"content": content, "username": USERNAME, "flags": 4, "allowed_mentions": {"parse": []}}).encode("utf-8")
    for _ in range(retries):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "AronaUpdate (sync.bat)",   # Discord/Cloudflare tu choi User-Agent mac dinh cua urllib
        })
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                try:
                    wait = float(json.loads(raw).get("retry_after", 1))
                except Exception:
                    wait = 1.0
                time.sleep(min(wait, 30) + 0.2)
                continue
            print(f"[discord] HTTP {e.code}: {raw[:200]}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[discord] request error: {e}", file=sys.stderr)
            time.sleep(1)
    return False


def send(url, text, post=post_message, gap=GAP_SECONDS):
    """Cat va gui lan luot. Tra ve (so tin gui duoc, tong so tin)."""
    chunks = split_message(text)
    ok = 0
    for i, chunk in enumerate(chunks):
        if i and gap:
            time.sleep(gap)
        if post(url, chunk):
            ok += 1
        else:
            break
    return ok, len(chunks)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else DEFAULT_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as e:
        print(f"[discord] cannot read {path}: {e}", file=sys.stderr)
        return 0
    if not text:
        print("[discord] nothing to send", file=sys.stderr)
        return 0
    url = read_env_var(ENV_NAME)
    if not url:
        print(f"[discord] {ENV_NAME} is not set (environment or .env); skipping announcement", file=sys.stderr)
        return 0
    if not valid_webhook_url(url):
        print(f"[discord] {ENV_NAME} is not a Discord webhook URL; skipping announcement", file=sys.stderr)
        return 1
    link = commit_link()
    if link:
        text += "\n\n" + link
    ok, total = send(url, text)
    print(f"[discord] sent {ok}/{total} message(s)")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
