"""
migrate_msgbank.py
~~~~~~~~~~~~~~~~~~
One-time migration: encode all messages in msg_bank SQLite → ChromaDB collection "msg_bank".
Uses RTX 3090 (cuda:0) for fast encoding, then unloads model from memory and deletes cache.

Usage (run from project root):
    python migrate_msgbank.py
    python migrate_msgbank.py --db database/msg_bank.db --chroma ./database/vector_db
"""

import argparse
import gc
import shutil
from pathlib import Path

import torch
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

BATCH_SIZE = 64  # RTX 3090 can handle large batches


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--db",     default="database/msg_bank.db", help="Path to msg_bank SQLite DB")
    p.add_argument("--chroma", default="./database/vector_db", help="Path to ChromaDB persistent dir")
    return p.parse_args()


def load_model(device: str) -> SentenceTransformer:
    print(f"[1/4] Downloading / loading BAAI/bge-m3 on {device}...")
    model = SentenceTransformer("BAAI/bge-m3", device=device)
    model.eval()
    if device != "cpu":
        print(f"      Model loaded. VRAM used: {torch.cuda.memory_allocated(0)/1e9:.2f} GB")
    else:
        print("      Model loaded.")
    return model


def fetch_all_messages(db_path: str) -> list[dict]:
    import sqlite3
    print(f"[2/4] Reading messages from {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, user_id, channel_name, guild_name, content FROM messages ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    print(f"      Found {len(result)} messages.")
    return result


def encode_and_store(model: SentenceTransformer, rows: list[dict], chroma_path: str):
    print(f"[3/4] Encoding and storing into ChromaDB at {chroma_path}...")

    client = chromadb.PersistentClient(
        path=chroma_path,
        settings=Settings(anonymized_telemetry=False, is_persistent=True)
    )
    collection = client.get_or_create_collection("msg_bank")

    total = len(rows)
    for start in range(0, total, BATCH_SIZE):
        batch     = rows[start:start + BATCH_SIZE]
        ids       = [str(r["id"]) for r in batch]
        contents  = [r["content"] or "" for r in batch]
        metadatas = [
            {
                "user_id": str(r["user_id"]),
                "channel": r["channel_name"] or "",
                "guild":   r["guild_name"] or ""
            }
            for r in batch
        ]

        vectors = model.encode(
            contents,
            normalize_embeddings=True,
            batch_size=BATCH_SIZE,
            show_progress_bar=False
        ).tolist()

        collection.upsert(ids=ids, embeddings=vectors, documents=contents, metadatas=metadatas)
        end = min(start + BATCH_SIZE, total)
        print(f"      [{end}/{total}] stored", end="\r")

    print(f"\n      Done. {total} vectors in collection '{collection.name}'.")


def unload_model(model: SentenceTransformer, device: str):
    print("[4/4] Unloading model and deleting cache...")
    del model
    gc.collect()
    if device != "cpu":
        torch.cuda.empty_cache()
        print(f"      VRAM freed. Remaining: {torch.cuda.memory_allocated(0)/1e9:.2f} GB")

    # Delete model files from HuggingFace cache
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    deleted = False
    for folder in cache_root.glob("models--BAAI--bge-m3"):
        print(f"      Deleting model cache: {folder}")
        shutil.rmtree(folder, ignore_errors=True)
        deleted = True
    if not deleted:
        print("      Model cache not found in default HF cache dir — delete manually if needed.")


def main():
    args = parse_args()

    if not torch.cuda.is_available():
        print("WARN: CUDA not available, falling back to CPU (will be slow).")
        device = "cpu"
    else:
        device = "cuda:0"
        print(f"CUDA detected: {torch.cuda.get_device_name(0)}")

    model = load_model(device)
    rows  = fetch_all_messages(args.db)

    if not rows:
        print("No messages found, nothing to migrate.")
        unload_model(model, device)
        return

    encode_and_store(model, rows, chroma_path=args.chroma)
    unload_model(model, device)
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
