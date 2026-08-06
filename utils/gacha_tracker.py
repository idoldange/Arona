"""
utils/gacha_tracker.py
Tracks Blue Archive gacha pulls, pity, and spark progress per user.

BA Gacha rules (Global):
- Soft pity starts at 50 (rate-up rate increases each pull)
- Hard pity at 200 pulls → guaranteed pickup 3★
- Spark at 200 shards = 1 pickup (200 pulls = 1 spark, or collect from dupes)
- Rate: base 3★ = 2.5%, pickup 3★ = 0.7%
- Each pull beyond 50 adds ~1.5% to pickup rate (soft pity estimate)
"""

import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

DB_DIR  = "./database"
DB_PATH = os.path.join(DB_DIR, "gacha.db")
os.makedirs(DB_DIR, exist_ok=True)


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
            CREATE TABLE IF NOT EXISTS gacha_tracker (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                banner      TEXT    NOT NULL DEFAULT 'current',
                pulls       INTEGER NOT NULL DEFAULT 0,
                sparks      INTEGER NOT NULL DEFAULT 0,
                shards      INTEGER NOT NULL DEFAULT 0,
                total_pulls INTEGER NOT NULL DEFAULT 0,
                last_3star  INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gacha_user ON gacha_tracker(user_id, banner)")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _get_or_create(conn, user_id: int, banner: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM gacha_tracker WHERE user_id=? AND banner=?", (user_id, banner)
    ).fetchone()
    if not row:
        conn.execute(
            """INSERT INTO gacha_tracker
               (user_id, banner, pulls, sparks, shards, total_pulls, last_3star, updated_at)
               VALUES (?,?,0,0,0,0,0,?)""",
            (user_id, banner, _now())
        )
        row = conn.execute(
            "SELECT * FROM gacha_tracker WHERE user_id=? AND banner=?", (user_id, banner)
        ).fetchone()
    return row


# soft pity probability estimate
def _pity_rate(pulls_since_last: int) -> float:
    """Estimated pickup 3★ rate given pulls since last 3★."""
    base = 0.007  # 0.7% base pickup
    if pulls_since_last < 50:
        return base
    # +1.5% per pull from 50 onward, capped at 100%
    extra = (pulls_since_last - 49) * 0.015
    return min(base + extra, 1.0)


def _prob_no_3star_by(pity: int) -> float:
    """Probability of NOT getting pickup 3★ within `pity` pulls."""
    prob = 1.0
    for i in range(pity):
        prob *= (1.0 - _pity_rate(i))
    return prob


# public api

def add_pulls(user_id: int, count: int, got_3star: bool = False,
              got_pickup: bool = False, banner: str = "current") -> dict:
    """
    Record new pulls.
    got_3star: pulled any 3★ this session
    got_pickup: pulled the featured 3★ (resets last_3star pity counter)
    Returns updated state dict.
    """
    with _get_db() as conn:
        row   = _get_or_create(conn, user_id, banner)
        pulls = row["pulls"] + count
        total = row["total_pulls"] + count
        last  = 0 if got_pickup else (row["last_3star"] + count)
        shards = row["shards"]
        sparks = row["sparks"]

        # Each 10-pull gives a shard (standard pity mechanic approximation)
        # Exact shard gain depends on banner type; use pull count as proxy
        shard_gain = count  # 1 shard per pull (standard JP/Global rule)
        shards = min(shards + shard_gain, 200)
        if shards >= 200:
            sparks += 1
            shards = 0

        conn.execute(
            """UPDATE gacha_tracker
               SET pulls=?, sparks=?, shards=?, total_pulls=?, last_3star=?, updated_at=?
               WHERE user_id=? AND banner=?""",
            (pulls, sparks, shards, total, last, _now(), user_id, banner)
        )
    return get_status(user_id, banner)


def reset_banner(user_id: int, banner: str = "current") -> dict:
    """Reset pull count for a new banner (keep sparks & total)."""
    with _get_db() as conn:
        row = _get_or_create(conn, user_id, banner)
        conn.execute(
            """UPDATE gacha_tracker
               SET pulls=0, last_3star=0, updated_at=?
               WHERE user_id=? AND banner=?""",
            (_now(), user_id, banner)
        )
    return get_status(user_id, banner)


def set_shards(user_id: int, shards: int, banner: str = "current") -> dict:
    """Manually set shard count (e.g. after buying from shop)."""
    with _get_db() as conn:
        _get_or_create(conn, user_id, banner)
        sparks_gain = shards // 200
        shards_rem  = shards % 200
        conn.execute(
            """UPDATE gacha_tracker
               SET shards=shards+?, sparks=sparks+?, updated_at=?
               WHERE user_id=? AND banner=?""",
            (shards_rem, sparks_gain, _now(), user_id, banner)
        )
    return get_status(user_id, banner)


def get_status(user_id: int, banner: str = "current") -> dict:
    """Return full status dict for display."""
    with _get_db() as conn:
        row = _get_or_create(conn, user_id, banner)

    pulls       = row["pulls"]
    last_3star  = row["last_3star"]
    shards      = row["shards"]
    sparks      = row["sparks"]
    total_pulls = row["total_pulls"]

    # Probabilities
    pulls_to_pity = max(0, 200 - last_3star)
    prob_next_10  = 1.0 - _prob_no_3star_by(last_3star + 10)
    prob_next_50  = 1.0 - _prob_no_3star_by(last_3star + 50)
    shards_to_spark = 200 - shards

    return {
        "banner":           banner,
        "pulls":            pulls,
        "last_3star_pity":  last_3star,
        "pulls_to_hard_pity": pulls_to_pity,
        "prob_pickup_next_10": round(prob_next_10 * 100, 1),
        "prob_pickup_next_50": round(prob_next_50 * 100, 1),
        "shards":           shards,
        "shards_to_spark":  shards_to_spark,
        "sparks":           sparks,
        "total_pulls":      total_pulls,
    }


def get_all_banners(user_id: int) -> list[dict]:
    """Return status for all banners this user has data on."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT banner FROM gacha_tracker WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
    return [get_status(user_id, r["banner"]) for r in rows]


# Init on import
init_db()
