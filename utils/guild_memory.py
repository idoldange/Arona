import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

from config import *

DB_PATH = os.path.join(DB_DIR, "guild_memory.db")
os.makedirs(DB_DIR, exist_ok=True)

MAX_MEMORY_LEN = -1  # unlimited length for persisted guild memory


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
            CREATE TABLE IF NOT EXISTS guild_memory (
                guild_id    INTEGER PRIMARY KEY,
                memory      TEXT    NOT NULL DEFAULT '',
                updated_at  TEXT    NOT NULL,
                updated_by  INTEGER
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get_memory(guild_id: int) -> str:
    """Return memory string for a guild, or '' if none."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT memory FROM guild_memory WHERE guild_id=?", (guild_id,)
        ).fetchone()
    return row["memory"].strip() if row else ""


def set_memory(guild_id: int, memory: str, updated_by: int = None) -> str:
    """Overwrite guild memory."""
    memory = memory.strip() if MAX_MEMORY_LEN < 0 else memory.strip()[:MAX_MEMORY_LEN]
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO guild_memory (guild_id, memory, updated_at, updated_by)
               VALUES (?,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                 memory=excluded.memory,
                 updated_at=excluded.updated_at,
                 updated_by=excluded.updated_by""",
            (guild_id, memory, _now(), updated_by)
        )
    return memory


def append_memory(guild_id: int, note: str, updated_by: int = None) -> str:
    """Append a note to existing guild memory."""
    existing = get_memory(guild_id)
    combined = (existing + "\n" + note.strip()).strip()
    return set_memory(guild_id, combined, updated_by)


def clear_memory(guild_id: int) -> None:
    """Delete guild memory."""
    with _get_db() as conn:
        conn.execute("DELETE FROM guild_memory WHERE guild_id=?", (guild_id,))


def build_prompt_block(guild_id: int) -> str:
    """Return a formatted block for injection into the system prompt, or ''."""
    mem = get_memory(guild_id)
    if not mem:
        return ""
    return f"## Guild Memory\n{mem}\n"


# Init on import
init_db()
