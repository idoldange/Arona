import os
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from config import FREE_TIER_DAILY_LIMIT, GLOBAL_DAILY_SOFT_LIMIT
from config import BYOK_DB_PATH as DB_PATH

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


def _dec(blob: bytes) -> list[str]:
    return json.loads(_fernet.decrypt(blob).decode())


def _today() -> str:
    pacific = datetime.now(timezone.utc) - timedelta(hours=8)
    return pacific.date().isoformat()


def add_keys(user_id: int, raw: str) -> list[str]:
    new_keys = [k.strip() for k in raw.split(",") if k.strip()]
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


def check_quota(user_id: int) -> bool:
    if has_own_key(user_id):
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


def increment_quota(user_id: int):
    if has_own_key(user_id):
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


def get_quota_status(user_id: int):
    if has_own_key(user_id):
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
