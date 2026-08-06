from typing import Union, Dict, Any, Optional
import json
import sqlite3
import os
from contextlib import contextmanager
from console import console

# Config

#config.py

from config import *

DB_PATH = SAVEDINFO_DB_PATH  # Alias for clarity in this module
# Legacy JSON path — only used during one-time migration
_LEGACY_JSON = os.path.join(DB_DIR, "longterm_memory", "user_memories.json")


@contextmanager
def _get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def _init_db():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_information (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  TEXT    NOT NULL,
                key      TEXT    NOT NULL,
                value    TEXT    NOT NULL,
                UNIQUE(user_id, key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON saved_information(user_id)")
    console.log("SavedInformation DB initialized.", "DEBUG")


def _migrate_from_json():
    """
    One-time migration: reads legacy JSON file -> inserts into SQLite -> deletes JSON.
    Safe to call on every start; exits early if JSON doesn't exist.
    """
    if not os.path.exists(_LEGACY_JSON):
        return

    console.log("Migrating legacy user_memories.json -> SQLite...", "INFO")
    try:
        with open(_LEGACY_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        migrated = 0
        with _get_db() as conn:
            for user_id, kv in data.items():
                for key, value in kv.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO saved_information (user_id, key, value) VALUES (?, ?, ?)",
                        (str(user_id), key, str(value)),
                    )
                    migrated += 1

        console.log(f"Migration complete: {migrated} entries imported.", "INFO")

        os.remove(_LEGACY_JSON)
        console.log(f"Deleted legacy file: {_LEGACY_JSON}", "INFO")

        try:
            os.rmdir(os.path.dirname(_LEGACY_JSON))
        except OSError:
            pass

    except Exception as e:
        console.log(f"Migration failed: {e}", "ERROR")


# Public API

class SavedInformationManager:
    """
    Persistent per-user key-value store backed by SQLite.
    Replaces the old JSON-based UserMemoryManager.
    Exposed to Gemini as the `saved_information` tool.
    """

    def __init__(self):
        _init_db()
        _migrate_from_json()

    def add(self, user_id: Union[str, int], key: str, value: str) -> str:
        user_id = str(user_id)
        console.log(f"[saved_information] add  user={user_id}  key='{key}'", "INFO")

        with _get_db() as conn:
            row = conn.execute(
                "SELECT key FROM saved_information WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()

            if row:
                idx = 1
                new_key = f"{key}_{idx}"
                while conn.execute(
                    "SELECT 1 FROM saved_information WHERE user_id=? AND key=?",
                    (user_id, new_key),
                ).fetchone():
                    idx += 1
                    new_key = f"{key}_{idx}"

                conn.execute(
                    "INSERT INTO saved_information (user_id, key, value) VALUES (?, ?, ?)",
                    (user_id, new_key, value),
                )
                console.log(f"[saved_information] Key '{key}' existed -> saved as '{new_key}'", "WARN")
                return f"Key '{key}' already exists. Saved as '{new_key}' instead."

            conn.execute(
                "INSERT INTO saved_information (user_id, key, value) VALUES (?, ?, ?)",
                (user_id, key, value),
            )

        console.log(f"[saved_information] Added  user={user_id}  key='{key}'", "INFO")
        return f"Saved information added: '{key}'."

    def edit(self, user_id: Union[str, int], key: str, value: str) -> str:
        user_id = str(user_id)
        console.log(f"[saved_information] edit  user={user_id}  key='{key}'", "INFO")

        with _get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM saved_information WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE saved_information SET value=? WHERE user_id=? AND key=?",
                    (value, user_id, key),
                )
                console.log(f"[saved_information] Updated  user={user_id}  key='{key}'", "INFO")
                return f"Saved information updated: '{key}'."
            else:
                console.log(f"[saved_information] Key '{key}' not found for user={user_id}. Creating.", "WARN")
                conn.execute(
                    "INSERT INTO saved_information (user_id, key, value) VALUES (?, ?, ?)",
                    (user_id, key, value),
                )
                return f"Key '{key}' did not exist. Created new saved information entry."

    def delete(self, user_id: Union[str, int], key: str) -> str:
        user_id = str(user_id)
        console.log(f"[saved_information] delete  user={user_id}  key='{key}'", "INFO")

        with _get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM saved_information WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()

            if not row:
                console.log(f"[saved_information] Key '{key}' not found for user={user_id}.", "ERROR")
                return f"No saved information found for key '{key}'."

            conn.execute(
                "DELETE FROM saved_information WHERE user_id=? AND key=?",
                (user_id, key),
            )

        console.log(f"[saved_information] Deleted  user={user_id}  key='{key}'", "INFO")
        return f"Saved information deleted: '{key}'."

    def delete_all(self, user_id: Union[str, int]) -> int:
        """Delete every saved_information entry for user_id. Returns rows deleted."""
        user_id = str(user_id)
        with _get_db() as conn:
            cursor = conn.execute(
                "DELETE FROM saved_information WHERE user_id=?",
                (user_id,),
            )
            deleted = cursor.rowcount

        console.log(f"[saved_information] Deleted ALL ({deleted}) entries for user={user_id}", "WARN")
        return deleted

    def get(self, user_id: Union[str, int]) -> Optional[Dict[str, str]]:
        user_id = str(user_id)
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM saved_information WHERE user_id=? ORDER BY key",
                (user_id,),
            ).fetchall()

        if not rows:
            return None
        return {key: value for key, value in rows}


# Singleton
saved_information = SavedInformationManager()

# Backward-compat alias
memory = saved_information