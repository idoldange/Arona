"""
utils/migration_keys.py
──────────────────────────────────────────────────────────────────────────────
Account linking + migration key system.

  link_account(new_id, source_id, source_key)
      → Authenticate with source key, resolve source to its root,
        store new_id → root in account_links.

  unlink_account(user_id, memory, bank)
      → Copy data from root to user_id (saved_info + msg_bank), remove link.

  resolve_id(user_id)
      → O(1) in-memory cache lookup.  Returns root_id or user_id itself.

  get_or_create_key(user_id) / reset_key(user_id)
      → Auth keys for link_account.  Stored in a separate table.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Union

from console import console
from config import DB_DIR

# Config

_DB_PATH = os.path.join(DB_DIR, "migration_keys.db")

# Keys excluded from data copy on unlink
_SKIP_KEYS = {"__impression__"}


# DB + cache

@contextmanager
def _db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def _init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS migration_keys (
                user_id  TEXT PRIMARY KEY,
                key      TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_links (
                linked_id  TEXT PRIMARY KEY,
                root_id    TEXT NOT NULL
            )
        """)
    console.log("MigrationKeys DB initialized.", "DEBUG")


_init_db()

# In-memory resolve cache — {linked_id: root_id}
# Loaded fully at startup; mutated on every write — O(1) reads forever.
_cache: dict[str, str] = {}

def _load_cache():
    global _cache
    with _db() as conn:
        rows = conn.execute("SELECT linked_id, root_id FROM account_links").fetchall()
    _cache = {row[0]: row[1] for row in rows}

_load_cache()


# Auth key API

def get_or_create_key(user_id: Union[str, int]) -> str:
    """Return existing auth key for user, or generate and persist a new one."""
    user_id = str(user_id)
    with _db() as conn:
        row = conn.execute(
            "SELECT key FROM migration_keys WHERE user_id=?", (user_id,)
        ).fetchone()
        if row:
            return row[0]
        new_key = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO migration_keys (user_id, key) VALUES (?,?)",
            (user_id, new_key),
        )
    console.log(f"[migration] Created key for user {user_id}", "INFO")
    return new_key


def reset_key(user_id: Union[str, int]) -> str:
    """Generate a new UUID4 key, immediately invalidating the old one."""
    user_id = str(user_id)
    new_key = str(uuid.uuid4())
    with _db() as conn:
        conn.execute(
            "INSERT INTO migration_keys (user_id,key) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET key=excluded.key",
            (user_id, new_key),
        )
    console.log(f"[migration] Key reset for user {user_id}", "INFO")
    return new_key


def delete_key(user_id: Union[str, int]) -> None:
    """Remove a user's stored migration/auth key entirely (used on full data wipe)."""
    user_id = str(user_id)
    with _db() as conn:
        conn.execute("DELETE FROM migration_keys WHERE user_id=?", (user_id,))
    console.log(f"[migration] Key deleted for user {user_id}", "INFO")


def is_linked(user_id: Union[str, int]) -> bool:
    """True if user_id is currently linked to another account's root."""
    return str(user_id) in _cache


def _validate_key(user_id: str, key: str) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT key FROM migration_keys WHERE user_id=?", (user_id,)
        ).fetchone()
    return bool(row and row[0] == key.strip())


# Link resolution

def resolve_id(user_id: Union[str, int]) -> str:
    """
    Resolve a Discord user_id to its canonical (root) ID.
    Returns user_id itself if no link exists.
    Pure in-memory dict lookup — effectively free.
    """
    uid = str(user_id)
    return _cache.get(uid, uid)


# Link / Unlink

async def link_account(
    new_id: Union[str, int],
    source_id: Union[str, int],
    source_key: str,
    memory=None,
    bank=None,
    ask_gemini_fn=None,
    lite_model: str = "",
    extract_text_fn=None,
) -> str:
    """
    Link new_id → source_id's data, merging any existing data first.

    Merge order:
      - saved_information: new_id's keys written into root (skip conflicts + __impression__)
      - msg_bank: true recency merge via bank.merge_into(new_id, root_id)
      - impression: merge both texts into one, saved on root

    After this call, resolve_id(new_id) == root_id.
    """
    new_id    = str(new_id)
    source_id = str(source_id)

    if new_id == source_id:
        return "Error: cannot link an account to itself."

    if not _validate_key(source_id, source_key):
        return "Error: invalid key for the provided account ID."

    root_id = resolve_id(source_id)

    if new_id == root_id:
        return "Error: cannot link an account to itself (resolved to same root)."

    merge_notes = []

    # saved_information merge (new_id → root, skip conflicts)
    if memory is not None:
        new_data  = memory.get(new_id) or {}
        root_data = memory.get(root_id) or {}
        merged_si = 0
        for key, value in new_data.items():
            if key == _SKIP_KEYS or key in _SKIP_KEYS:
                continue
            if key not in root_data:
                memory.add(root_id, key, value)
                merged_si += 1
        if merged_si:
            merge_notes.append(f"{merged_si} saved info entry/entries merged")

    # msg_bank merge
    if bank is not None:
        try:
            stats = await bank.merge_into(new_id, root_id)
            merge_notes.append(
                f"{stats['rows_kept']} messages kept"
                + (f", {stats['rows_dropped']} dropped (cap)" if stats["rows_dropped"] else "")
            )
        except Exception as e:
            merge_notes.append(f"message merge failed: {e}")

    # impression merge
    if memory is not None and ask_gemini_fn is not None:
        from utils.impression import get_impression, _save_impression, merge_impressions
        imp_root = get_impression(memory, root_id)
        imp_new  = get_impression(memory, new_id)
        if imp_root or imp_new:
            merged_imp = await merge_impressions(
                imp_root, imp_new, ask_gemini_fn, lite_model, extract_text_fn
            )
            if merged_imp:
                _save_impression(memory, root_id, merged_imp)
                merge_notes.append("impressions merged")

    # write link
    with _db() as conn:
        conn.execute(
            "INSERT INTO account_links (linked_id, root_id) VALUES (?,?) "
            "ON CONFLICT(linked_id) DO UPDATE SET root_id=excluded.root_id",
            (new_id, root_id),
        )

    _cache[new_id] = root_id
    console.log(f"[migration] Linked {new_id} → {root_id}", "INFO")

    note = f" ({', '.join(merge_notes)})" if merge_notes else ""
    return f"Accounts linked. This account now shares data with `{root_id}`{note}."


async def unlink_account(
    user_id: Union[str, int],
    memory,      # SavedInformationManager
    bank=None,   # MessageBank (optional)
) -> str:
    """
    Unlink user_id from its root:
      1. Copy saved_information root → user_id (skip conflicts + system keys).
      2. Copy msg_bank history root → user_id (vectors included, no re-encode).
      3. Delete the link and invalidate cache.
    """
    user_id = str(user_id)
    root_id = _cache.get(user_id)

    if root_id is None:
        return "This account is not linked to any other account."

    # saved_information
    source_data = memory.get(root_id) or {}
    target_data = memory.get(user_id) or {}
    migrated_si, skipped_conflict, skipped_system = 0, 0, 0

    for key, value in source_data.items():
        if key in _SKIP_KEYS:
            skipped_system += 1
            continue
        if key in target_data:
            skipped_conflict += 1
            continue
        memory.add(user_id, key, value)
        migrated_si += 1

    si_result = f"{migrated_si} entry/entries copied"
    if skipped_conflict:
        si_result += f", {skipped_conflict} skipped (conflict)"
    if skipped_system:
        si_result += f", {skipped_system} system key(s) excluded"

    # msg_bank
    bank_result = ""
    if bank is not None:
        try:
            stats = await bank.migrate_user_data(root_id, user_id)
            bank_result = (
                f"{stats['rows_migrated']} message(s) copied"
                + (f", {stats['rows_skipped']} skipped (cap)" if stats["rows_skipped"] else "")
                + f", {stats['vectors_migrated']} vector(s)"
            )
        except Exception as e:
            bank_result = f"message history copy failed: {e}"

    # remove link
    with _db() as conn:
        conn.execute("DELETE FROM account_links WHERE linked_id=?", (user_id,))
    del _cache[user_id]

    console.log(f"[migration] Unlinked {user_id} from {root_id}", "INFO")

    lines = [f"Account unlinked from `{root_id}`."]
    lines.append(f"• Saved info: {si_result}.")
    if bank_result:
        lines.append(f"• Message history: {bank_result}.")
    return "\n".join(lines)