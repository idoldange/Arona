import os
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from config import FREE_TIER_DAILY_LIMIT, GLOBAL_DAILY_SOFT_LIMIT
from config import BYOK_DB_PATH as DB_PATH
import dotenv
dotenv.load_dotenv()

_secret = os.getenv("APIKEY_ENCRYPT_SECRET")
if not _secret:
    _secret = Fernet.generate_key().decode()
    print(f"[apikeys] APIKEY_ENCRYPT_SECRET missing, generated: {_secret}")
_fernet = Fernet(_secret.encode() if isinstance(_secret, str) else _secret)

_KEY_RESET_DATE = None


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS user_credentials (
        user_id INTEGER PRIMARY KEY,
        keys_encrypted BLOB,
        key_index INTEGER DEFAULT 0,
        added_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_quota (
        user_id INTEGER,
        date TEXT,
        message_count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, date)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS global_quota (
        date TEXT PRIMARY KEY,
        message_count INTEGER DEFAULT 0
    )""")
    return c


def _enc(keys: list[str]) -> bytes:
    return _fernet.encrypt(json.dumps(keys).encode())


def _clean(k: str) -> str:
    return k.strip().strip('"\'').strip()


def _dec(blob: bytes) -> list[str]:
    keys = json.loads(_fernet.decrypt(blob).decode())
    return [_clean(k) for k in keys if _clean(k)]


def _today() -> str:
    pacific = datetime.now(timezone.utc) - timedelta(hours=8)
    return pacific.date().isoformat()


def add_keys(user_id: int, raw: str) -> list[str]:
    new_keys = [_clean(k) for k in raw.split(",") if _clean(k)]
    existing = get_keys(user_id) or []
    keys = existing + new_keys
    with _conn() as c:
        c.execute(
            "INSERT INTO user_credentials (user_id, keys_encrypted, key_index, added_at) VALUES (?,?,0,?) "
            "ON CONFLICT(user_id) DO UPDATE SET keys_encrypted=excluded.keys_encrypted",
            (user_id, _enc(keys), time.strftime("%Y-%m-%d %H:%M:%S"))
        )
    return keys


def remove_key(user_id: int, index: int) -> "str | None":
    keys = get_keys(user_id) or []
    if index < 1 or index > len(keys):
        return None
    removed = keys.pop(index - 1)
    with _conn() as c:
        if keys:
            c.execute("UPDATE user_credentials SET keys_encrypted=?, key_index=0 WHERE user_id=?", (_enc(keys), user_id))
        else:
            c.execute("UPDATE user_credentials SET keys_encrypted=NULL, key_index=0 WHERE user_id=?", (user_id,))
    return removed


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def remove_keys(user_id: int):
    with _conn() as c:
        c.execute("UPDATE user_credentials SET keys_encrypted=NULL, key_index=0 WHERE user_id=?", (user_id,))


def get_keys(user_id: int):
    with _conn() as c:
        row = c.execute("SELECT keys_encrypted FROM user_credentials WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row[0]:
        return None
    return _dec(row[0])


def rotate_key(user_id: int):
    with _conn() as c:
        row = c.execute("SELECT keys_encrypted, key_index FROM user_credentials WHERE user_id=?", (user_id,)).fetchone()
        if not row or not row[0]:
            return
        keys = _dec(row[0])
        new_idx = (row[1] + 1) % len(keys)
        c.execute("UPDATE user_credentials SET key_index=? WHERE user_id=?", (new_idx, user_id))


def has_own_key(user_id: int) -> bool:
    return bool(get_keys(user_id))


def check_quota(user_id: int, ignore_own_key: bool = False) -> bool:
    """Check whether user_id can still send a free-tier message today.

    By default, users with their own stored key(s) always pass (unlimited).
    Pass ignore_own_key=True to check the *free-tier* limit itself regardless of
    whether the user has own keys stored — used when a BYOK user's own keys have
    all failed for this request and the bot needs to know whether it's allowed to
    fall back to the shared free key pool.
    """
    if not ignore_own_key and has_own_key(user_id):
        return True
    with _conn() as c:
        row = c.execute("SELECT message_count FROM user_quota WHERE user_id=? AND date=?", (user_id, _today())).fetchone()
    used = row[0] if row else 0
    if used >= FREE_TIER_DAILY_LIMIT:
        return False
    if GLOBAL_DAILY_SOFT_LIMIT:
        with _conn() as c:
            g = c.execute("SELECT message_count FROM global_quota WHERE date=?", (_today(),)).fetchone()
        if g and g[0] >= GLOBAL_DAILY_SOFT_LIMIT:
            return False
    return True


def increment_quota(user_id: int, ignore_own_key: bool = False):
    """Record one free-tier message used today.

    By default a no-op for users with their own stored key(s) (unlimited, nothing to
    track). Pass ignore_own_key=True to force the increment anyway — used when a BYOK
    user actually fell back to a shared free key for this request, so that usage still
    counts against their free-tier allowance.
    """
    if not ignore_own_key and has_own_key(user_id):
        return
    today = _today()
    with _conn() as c:
        c.execute(
            "INSERT INTO user_quota (user_id, date, message_count) VALUES (?,?,1) "
            "ON CONFLICT(user_id, date) DO UPDATE SET message_count=message_count+1",
            (user_id, today)
        )
        c.execute(
            "INSERT INTO global_quota (date, message_count) VALUES (?,1) "
            "ON CONFLICT(date) DO UPDATE SET message_count=message_count+1",
            (today,)
        )


def get_quota_status(user_id: int, ignore_own_key: bool = False):
    """Return (used, limit) for the free-tier allowance.

    By default returns (None, None) for users with their own stored key(s) (unlimited).
    Pass ignore_own_key=True to get the underlying free-tier usage numbers regardless
    (e.g. to report how much of the free-tier fallback a BYOK user has used today).
    """
    if not ignore_own_key and has_own_key(user_id):
        return None, None
    with _conn() as c:
        row = c.execute("SELECT message_count FROM user_quota WHERE user_id=? AND date=?", (user_id, _today())).fetchone()
    used = row[0] if row else 0
    return used, FREE_TIER_DAILY_LIMIT


def check_daily_reset():
    global _KEY_RESET_DATE
    today = _today()
    if _KEY_RESET_DATE != today:
        _KEY_RESET_DATE = today


def delete_all(user_id: int):
    with _conn() as c:
        c.execute("DELETE FROM user_credentials WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM user_quota WHERE user_id=?", (user_id,))


import aiohttp

GEMINI_VALIDATE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_REJECT_STATUSES = (400, 401, 403)


async def validate_key(session: aiohttp.ClientSession, key: str) -> tuple[bool, "str | None"]:
    """Check a Gemini key via models.list (does not touch generation quota).

    Returns (is_valid, reason). is_valid is False only on 400/401/403
    (bad/revoked/unauthorized key). Anything else — 429, 5xx, timeout,
    network error — is treated as valid so a transient issue never blocks
    a working key.
    """
    try:
        async with session.get(
            GEMINI_VALIDATE_URL,
            params={"key": key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in _REJECT_STATUSES:
                reason = f"HTTP {resp.status}"
                try:
                    data = await resp.json()
                    msg = data.get("error", {}).get("message")
                    if msg:
                        reason = f"{resp.status}: {msg}"
                except Exception:
                    pass
                return False, reason
            return True, None
    except Exception:
        return True, None


def split_raw_keys(raw: str) -> list[str]:
    return [_clean(k) for k in raw.split(",") if _clean(k)]