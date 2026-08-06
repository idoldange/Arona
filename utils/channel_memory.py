"""
utils/channel_memory.py
Per-channel persistent memory — Arona remembers channel-specific context
(tech stack, decisions, ongoing projects, etc.) across conversations.
"""

import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

DB_DIR  = "./database"
DB_PATH = os.path.join(DB_DIR, "channel_memory.db")
os.makedirs(DB_DIR, exist_ok=True)

MAX_MEMORY_LEN = -1  # unlimited length for persisted channel memory


@contextmanager
def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_db():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_memory (
                channel_id  INTEGER PRIMARY KEY,
                memory      TEXT    NOT NULL DEFAULT '',
                updated_at  TEXT    NOT NULL,
                updated_by  INTEGER
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get_memory(channel_id: int) -> str:
    """Return memory string for a channel, or '' if none."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT memory FROM channel_memory WHERE channel_id=?", (channel_id,)
        ).fetchone()
    return row["memory"].strip() if row else ""


def set_memory(channel_id: int, memory: str, updated_by: int = None) -> str:
    """Overwrite channel memory."""
    memory = memory.strip() if MAX_MEMORY_LEN < 0 else memory.strip()[:MAX_MEMORY_LEN]
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO channel_memory (channel_id, memory, updated_at, updated_by)
               VALUES (?,?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 memory=excluded.memory,
                 updated_at=excluded.updated_at,
                 updated_by=excluded.updated_by""",
            (channel_id, memory, _now(), updated_by)
        )
    return memory


def append_memory(channel_id: int, note: str, updated_by: int = None) -> str:
    """Append a note to existing channel memory."""
    existing = get_memory(channel_id)
    combined = (existing + "\n" + note.strip()).strip()
    return set_memory(channel_id, combined, updated_by)


def clear_memory(channel_id: int) -> None:
    """Delete channel memory."""
    with _get_db() as conn:
        conn.execute("DELETE FROM channel_memory WHERE channel_id=?", (channel_id,))


def build_prompt_block(channel_id: int) -> str:
    """Return a formatted block for injection into the system prompt, or ''."""
    mem = get_memory(channel_id)
    if not mem:
        return ""
    return f"## Channel Memory\n{mem}\n"


# Init on import
init_db()
