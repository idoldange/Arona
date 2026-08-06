import aiosqlite
import time
import datetime
import asyncio
import numpy as np
from typing import List, Dict, Any
from utils.vector_database import rag_engine


class MessageBank:
    def __init__(self, db_path: str = "msg_bank.db"):
        self.db_path = db_path
        self.vec_collection = None  # set by initialize(), guard against pre-init calls

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    channel_name TEXT,
                    guild_name TEXT,
                    display_name TEXT,
                    content TEXT,
                    is_bot INTEGER DEFAULT 0,
                    created_at REAL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON messages (user_id)")
            await db.commit()
        # Separate ChromaDB collection for message vectors
        self.vec_collection = rag_engine.client.get_or_create_collection("msg_bank")

    def _check_initialized(self):
        if self.vec_collection is None:
            raise RuntimeError("MessageBank.initialize() has not been called yet.")

    async def add_messages(self, user_id: int | str, channel_name: str, guild_name: str, messages: List[Dict[str, Any]]):
        """
        Save multiple messages at once.
        messages: list of dicts like [{'name': '...', 'content': '...', 'is_bot': 0}, ...]
        """
        self._check_initialized()
        user_id_str = str(user_id)
        timestamp = time.time()

        data_to_insert = [
            (user_id_str, channel_name, guild_name, m['name'], m['content'], m.get('is_bot', 0), timestamp + i * 0.001)
            for i, m in enumerate(messages)
        ]

        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """INSERT INTO messages 
                   (user_id, channel_name, guild_name, display_name, content, is_bot, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                data_to_insert
            )
            # Find overflow ids to sync with vector db
            cursor = await db.execute("""
                SELECT id FROM messages WHERE user_id = ?
                ORDER BY created_at DESC LIMIT -1 OFFSET 600
            """, (user_id_str,))
            overflow_ids = [str(r[0]) for r in await cursor.fetchall()]
            if overflow_ids:
                await db.execute(
                    f"DELETE FROM messages WHERE id IN ({','.join('?' * len(overflow_ids))})",
                    overflow_ids
                )
            # Get inserted row ids for encoding
            cursor2 = await db.execute(
                "SELECT id, content FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id_str, len(data_to_insert))
            )
            inserted = await cursor2.fetchall()
            await db.commit()

        # Delete overflow vectors
        if overflow_ids:
            try:
                self.vec_collection.delete(ids=overflow_ids)
            except Exception:
                pass

        # Encode and store vectors (fire-and-forget)
        asyncio.create_task(self._encode_and_store(inserted, user_id_str, channel_name, guild_name))

    async def _encode_and_store(self, inserted: list, user_id_str: str, channel_name: str, guild_name: str):
        try:
            ids = [str(r[0]) for r in inserted]
            contents = [r[1] or '' for r in inserted]
            loop = asyncio.get_running_loop()
            vectors = await loop.run_in_executor(
                None,
                lambda: rag_engine.model.encode(contents, normalize_embeddings=True, batch_size=32).tolist()
            )
            self.vec_collection.upsert(
                ids=ids,
                embeddings=vectors,
                documents=contents,
                metadatas=[{"user_id": user_id_str, "channel": channel_name, "guild": guild_name}] * len(ids)
            )
        except Exception:
            pass

    async def get_recent_messages(self, user_id: int | str, limit: int = 50) -> List[Dict[str, Any]]:
        self._check_initialized()
        user_id_str = str(user_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, display_name, content, is_bot, channel_name, guild_name, created_at
                   FROM messages WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id_str, limit)
            )
            rows = await cursor.fetchall()
            # Return oldest-first so format_for_gemini doesn't need to reverse
            return [dict(row) for row in reversed(rows)]

    async def search_messages(self, user_id: int | str, query: str, limit: int | str = 10) -> List[Dict[str, Any]]:
        self._check_initialized()
        user_id_str = str(user_id)
        limit = int(limit)

        loop = asyncio.get_running_loop()
        query_vec = await loop.run_in_executor(
            None,
            lambda: rag_engine.model.encode([query], normalize_embeddings=True)[0].tolist()
        )

        # Query vector db filtered by user_id — search scope 600, but cap returned results by limit (max 50)
        results = self.vec_collection.query(
            query_embeddings=[query_vec],
            n_results=600,
            where={"user_id": user_id_str}
        )

        if not results['ids'] or not results['ids'][0]:
            return []

        matched_ids = results['ids'][0][:limit]

        # Fetch full rows from SQLite by id, filtered by user_id to prevent cross-user leakage
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ','.join('?' * len(matched_ids))
            cursor = await db.execute(
                f"""SELECT id, display_name, content, is_bot, channel_name, guild_name, created_at
                    FROM messages
                    WHERE id IN ({placeholders}) AND user_id = ?""",
                (*matched_ids, user_id_str)
            )
            # Key by id (string) — avoids collision on duplicate content
            rows = {str(r['id']): dict(r) for r in await cursor.fetchall()}

        # Return in semantic score order (preserving ChromaDB ranking)
        ordered = []
        for row_id in matched_ids:
            row = rows.get(str(row_id))
            if row:
                ordered.append(row)
        return ordered

    async def merge_into(self, source_id: int | str, target_id: int | str) -> dict:
        """
        True recency merge: pool all messages from source + target,
        keep the 600 most recent, write them all under target_id.
        Vectors from source are copied directly; target-origin rows are re-encoded.
        Returns {"rows_kept": int, "rows_dropped": int, "vectors_upserted": int}
        """
        self._check_initialized()
        source_id_str = str(source_id)
        target_id_str = str(target_id)

        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """SELECT id, user_id, channel_name, guild_name, display_name,
                          content, is_bot, created_at
                   FROM messages WHERE user_id IN (?, ?)
                   ORDER BY created_at DESC""",
                (source_id_str, target_id_str),
            )
            all_rows = await cur.fetchall()

        # Deduplicate by (content, created_at)
        seen, unique_rows = set(), []
        for row in all_rows:
            key = (row[5], row[7])
            if key not in seen:
                seen.add(key)
                unique_rows.append(row)

        kept    = unique_rows[:600]
        dropped = len(unique_rows) - len(kept)
        old_ids_source = [str(r[0]) for r in kept if r[1] == source_id_str]
        all_old_ids    = [str(r[0]) for r in all_rows]

        # Fetch source vectors before deletion
        source_vecs: dict[str, dict] = {}
        if old_ids_source:
            try:
                loop = asyncio.get_running_loop()
                vr = await loop.run_in_executor(
                    None,
                    lambda: self.vec_collection.get(
                        ids=old_ids_source,
                        include=["embeddings", "documents", "metadatas"],
                    ),
                )
                if vr and vr["ids"]:
                    for i, vid in enumerate(vr["ids"]):
                        emb = (vr["embeddings"] or [None] * len(vr["ids"]))[i]
                        if emb is not None:
                            source_vecs[str(vid)] = {
                                "emb": emb,
                                "doc": (vr["documents"] or [""])[i] or "",
                            }
            except Exception:
                pass

        # Atomically replace with merged set
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM messages WHERE user_id IN (?, ?)",
                (source_id_str, target_id_str),
            )
            old_to_new: dict[str, str] = {}
            for row in reversed(kept):
                old_id, _, channel, guild, display, content, is_bot, created_at = row
                cur = await db.execute(
                    """INSERT INTO messages
                       (user_id, channel_name, guild_name, display_name, content, is_bot, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (target_id_str, channel, guild, display, content, is_bot, created_at),
                )
                old_to_new[str(old_id)] = str(cur.lastrowid)
            await db.commit()

        # Delete old vectors
        try:
            loop = asyncio.get_running_loop()
            if all_old_ids:
                await loop.run_in_executor(
                    None, lambda: self.vec_collection.delete(ids=all_old_ids)
                )
        except Exception:
            pass

        # Re-upsert vectors
        vectors_upserted = 0
        try:
            upsert_ids, upsert_embs, upsert_docs, upsert_metas = [], [], [], []
            need_encode: list[tuple[str, str]] = []

            for old_id, new_id in old_to_new.items():
                if old_id in source_vecs:
                    v = source_vecs[old_id]
                    upsert_ids.append(new_id)
                    upsert_embs.append(v["emb"])
                    upsert_docs.append(v["doc"])
                    upsert_metas.append({"user_id": target_id_str})
                else:
                    for row in kept:
                        if str(row[0]) == old_id:
                            need_encode.append((new_id, row[5] or ""))
                            break

            if need_encode:
                contents = [c for _, c in need_encode]
                loop = asyncio.get_running_loop()
                vecs = await loop.run_in_executor(
                    None,
                    lambda: rag_engine.model.encode(
                        contents, normalize_embeddings=True, batch_size=32
                    ).tolist(),
                )
                for (new_id, content), vec in zip(need_encode, vecs):
                    upsert_ids.append(new_id)
                    upsert_embs.append(vec)
                    upsert_docs.append(content)
                    upsert_metas.append({"user_id": target_id_str})

            if upsert_ids:
                await loop.run_in_executor(
                    None,
                    lambda: self.vec_collection.upsert(
                        ids=upsert_ids,
                        embeddings=upsert_embs,
                        documents=upsert_docs,
                        metadatas=upsert_metas,
                    ),
                )
                vectors_upserted = len(upsert_ids)
        except Exception:
            pass

        return {"rows_kept": len(kept), "rows_dropped": dropped, "vectors_upserted": vectors_upserted}

    async def migrate_user_data(self, source_id: int | str, target_id: int | str) -> dict:
        """
        Copy message history (SQLite rows + ChromaDB vectors) from source → target.

        - Respects the 600-row cap: only migrates up to (600 - current_target_rows) rows,
          picking the most recent ones from source.
        - Vectors are copied directly by ID — no re-encoding.
        - ChromaDB metadata user_id is updated to target; channel/guild are preserved.

        Returns {"rows_migrated": int, "rows_skipped": int, "vectors_migrated": int}
        """
        self._check_initialized()
        source_id_str = str(source_id)
        target_id_str = str(target_id)

        async with aiosqlite.connect(self.db_path) as db:
            # How many rows does target already have?
            cur = await db.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ?", (target_id_str,)
            )
            (target_count,) = await cur.fetchone()
            slots_available = max(0, 600 - target_count)

            # Fetch the most-recent source rows we can actually fit
            cur = await db.execute(
                """SELECT id, channel_name, guild_name, display_name, content, is_bot, created_at
                   FROM messages WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (source_id_str, slots_available),
            )
            source_rows = await cur.fetchall()

        if not source_rows:
            return {"rows_migrated": 0, "rows_skipped": 0, "vectors_migrated": 0}

        # Total source count for skipped calculation
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ?", (source_id_str,)
            )
            (source_total,) = await cur.fetchone()

        rows_skipped = max(0, source_total - len(source_rows))

        # Insert rows one-by-one to capture new IDs, build old→new mapping
        old_to_new: dict[str, str] = {}
        async with aiosqlite.connect(self.db_path) as db:
            for row in source_rows:
                old_id, channel, guild, display, content, is_bot, created_at = row
                cur = await db.execute(
                    """INSERT INTO messages
                       (user_id, channel_name, guild_name, display_name, content, is_bot, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (target_id_str, channel, guild, display, content, is_bot, created_at),
                )
                old_to_new[str(old_id)] = str(cur.lastrowid)
            await db.commit()

        # Copy ChromaDB vectors by old IDs — no re-encoding needed
        vectors_migrated = 0
        try:
            loop = asyncio.get_running_loop()
            old_ids = list(old_to_new.keys())

            vec_result = await loop.run_in_executor(
                None,
                lambda: self.vec_collection.get(
                    ids=old_ids,
                    include=["embeddings", "documents", "metadatas"],
                ),
            )

            if vec_result and vec_result["ids"]:
                upsert_ids, upsert_embeddings, upsert_docs, upsert_metas = [], [], [], []
                for i, old_id in enumerate(vec_result["ids"]):
                    new_id = old_to_new.get(str(old_id))
                    if new_id is None:
                        continue
                    emb = vec_result["embeddings"][i]
                    if emb is None:
                        continue
                    # Preserve channel/guild metadata, update user_id
                    meta = dict(vec_result["metadatas"][i]) if vec_result["metadatas"] else {}
                    meta["user_id"] = target_id_str
                    upsert_ids.append(new_id)
                    upsert_embeddings.append(emb)
                    upsert_docs.append(vec_result["documents"][i] or "")
                    upsert_metas.append(meta)

                if upsert_ids:
                    await loop.run_in_executor(
                        None,
                        lambda: self.vec_collection.upsert(
                            ids=upsert_ids,
                            embeddings=upsert_embeddings,
                            documents=upsert_docs,
                            metadatas=upsert_metas,
                        ),
                    )
                    vectors_migrated = len(upsert_ids)
        except Exception:
            pass  # vectors are best-effort; SQLite migration is already committed

        return {
            "rows_migrated": len(source_rows),
            "rows_skipped": rows_skipped,
            "vectors_migrated": vectors_migrated,
        }

    async def delete_all_for_user(self, user_id: int | str) -> dict:
        """
        Permanently delete every stored message (SQLite rows + ChromaDB vectors)
        belonging to user_id. Returns {"rows_deleted": int, "vectors_deleted": int}.
        """
        self._check_initialized()
        user_id_str = str(user_id)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM messages WHERE user_id = ?", (user_id_str,)
            )
            ids = [str(r[0]) for r in await cursor.fetchall()]

            await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id_str,))
            await db.commit()

        vectors_deleted = 0
        if ids:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: self.vec_collection.delete(ids=ids))
                vectors_deleted = len(ids)
            except Exception:
                pass  # best-effort; SQLite rows are already gone

        return {"rows_deleted": len(ids), "vectors_deleted": vectors_deleted}

    def format_for_gemini(self, rows: List[Dict[str, Any]], bot_name: str = "Arona") -> str:
        """
        Expects rows already in chronological order (oldest first).
        Both get_recent_messages and search_messages return oldest-first.
        """
        formatted_list = []
        for msg in rows:
            author = bot_name if msg['is_bot'] == 1 else msg['display_name']
            created_at_dt = datetime.datetime.utcfromtimestamp(msg['created_at'])
            time_str = created_at_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
            line = f"[{time_str}][{msg['guild_name']}|{msg['channel_name']}] {author}: {msg['content']}"
            formatted_list.append(line)
        return "\n".join(formatted_list)