"""
affection/relation.py
~~~~~~~~~~~~~~~~~~~~~
Durable per-user relationship axes: the long-lived part of "how she feels about X" that the 24h mood offset
(mood.py) and the single bond number (bond.py) cannot hold.

    trust, respect, playfulness, ease, openness  rest at 0.5      tension rests at 0
    familiarity is NOT stored: it is the bond.

Slow lazy decay toward rest (no tick). The model writes through  <rel>trust+3 tension-2 | reason</rel>.
Anti-spam is prompt-side: recent changes are shown back to the model. Only technical clamps live here.
"""

import asyncio
import json
import re
import time
from collections import deque

import aiosqlite

import config as C
from console import console
from affection import mood as _mood   # clean_reason, _relax, _clamp, _age_text, _change_word

DB_PATH = C.AFFECTION_DB_PATH
AXES = ("trust", "respect", "tension", "playfulness", "ease", "openness")
REST = {a: (0.0 if a == "tension" else 0.5) for a in AXES}

_state: dict[int, dict] = {}        # uid -> {"v": {axis: level}, "t": epoch of last decay}
_events: dict[int, deque] = {}      # uid -> recent {"t","dm","r","d"} (RAM only, feeds the prompt)
_pending: set = set()
_lock = asyncio.Lock()
_TOKEN = re.compile(r"([a-z]+)\s*([+\-−–—]?)\s*(\d+(?:\.\d+)?)", re.I)


def _on() -> bool:
    return bool(getattr(C, "REL_ENABLED", True))


# Tag parsing

def parse_tag_body(body: str) -> tuple[dict, str]:
    """'trust+3 tension-2 | reason' -> ({axis: delta}, reason). Never raises; unknown axes are ignored."""
    left, _sep, right = str(body).partition("|")
    reason = _mood.clean_reason(right)
    cap = C.REL_TAG_MAX * C.REL_UNIT
    deltas: dict[str, float] = {}
    for name, sign, num in _TOKEN.findall(left):
        name = name.lower()
        if name not in AXES or (name not in deltas and len(deltas) >= C.REL_TAG_MAX_COUNT):
            continue
        s = -1.0 if sign in ("-", "−", "–", "—") else 1.0
        deltas[name] = _mood._clamp(deltas.get(name, 0.0) + s * min(float(num), C.REL_TAG_MAX) * C.REL_UNIT, -cap, cap)
    return {a: d for a, d in deltas.items() if d}, reason


# State

def _fresh(uid: int, now: float) -> dict | None:
    """The user's state with pending decay applied; dropped once everything is back at rest."""
    st = _state.get(uid)
    if st is None:
        return None
    dt = now - st["t"]
    if dt > 0:
        v = st["v"]
        for a in AXES:
            v[a] = _mood._relax(v[a], REST[a], float(C.REL_HALFLIFE[a]), dt)
        st["t"] = now
        if all(abs(v[a] - REST[a]) < 0.004 for a in AXES):
            del _state[uid]
            return None
    return st


def get(uid: int | None, now: float | None = None) -> dict:
    if uid is None:
        return dict(REST)
    st = _fresh(int(uid), time.time() if now is None else now)
    return dict(st["v"]) if st else dict(REST)


def apply(uid: int, deltas: dict, reason: str = "", is_dm: bool = False, now: float | None = None) -> dict:
    """Apply deltas (already in 0..1 units) to this user. Returns what was actually applied per axis."""
    if not _on() or not deltas:
        return {}
    uid, now = int(uid), time.time() if now is None else now
    st = _fresh(uid, now)
    if st is None:
        st = _state[uid] = {"v": dict(REST), "t": now}
    applied = {}
    for a, d in deltas.items():
        if a not in AXES:
            continue
        cur = st["v"][a]
        st["v"][a] = _mood._clamp(cur + float(d))
        applied[a] = st["v"][a] - cur
    applied = {a: round(x, 3) for a, x in applied.items() if abs(x) >= 0.002}
    if applied:
        _events.setdefault(uid, deque(maxlen=C.REL_EVENTS_KEEP)).append(
            {"t": now, "dm": bool(is_dm), "r": _mood.clean_reason(reason), "d": applied})
        _schedule_flush(uid)
    return applied


def apply_tag(body: str, uid: int, is_dm: bool = False, now: float | None = None) -> dict:
    deltas, reason = parse_tag_body(body)
    applied = apply(uid, deltas, reason, is_dm, now)
    if applied:
        console.log(f"[rel] user={uid} {applied}", "DEBUG")
    return applied


def reset(uid: int):
    _state.pop(int(uid), None)
    _events.pop(int(uid), None)
    _schedule_flush(int(uid))


# Effects on the other systems (all neutral = 1.0 when disabled or at rest)

def hl_mult(uid: int | None) -> float:
    """Multiplier on the half-life of negative feelings toward this user: tension lingers, trust forgives."""
    if uid is None or not _on():
        return 1.0
    v = get(uid)
    return (1.0 + C.REL_TENSION_HL_MULT * v["tension"]) * (1.0 - C.REL_TRUST_FORGIVE * (v["trust"] - 0.5))


def exp_mult(uid: int | None) -> float:
    """Multiplier on bond gained per message: tension slows it."""
    if uid is None or not _on():
        return 1.0
    return 1.0 - C.REL_TENSION_EXP_CUT * get(uid)["tension"]


def special_ok(uid: int | None) -> bool:
    if uid is None or not _on():
        return True
    v = get(uid)
    return v["trust"] >= C.REL_SPECIAL_MIN_TRUST and v["tension"] <= C.REL_SPECIAL_MAX_TENSION


# Readout

def _word(dev: float) -> str:
    return ("very high" if dev >= 0.3 else "high" if dev >= C.REL_SHOW_DEV
            else "very low" if dev <= -0.3 else "low" if dev <= -C.REL_SHOW_DEV else "")


def levels(uid: int | None, now: float | None = None) -> list[tuple[str, str]]:
    """[(axis, word)] for axes that deviate from rest enough to matter."""
    v = get(uid, now)
    return [(a, w) for a in AXES if (w := _word(v[a] - REST[a]))]


def _changes(uid: int, is_dm: bool, now: float) -> list[str]:
    out = []
    for ev in _events.get(uid, ()):
        if now - ev["t"] > C.REL_EVENTS_WINDOW:
            continue
        moves = ", ".join(f"{a} {'+' if x > 0 else '-'}{_mood._change_word(x)}"
                          for a, x in sorted(ev["d"].items(), key=lambda kv: -abs(kv[1])))
        text = "a private matter" if (ev["dm"] and not is_dm) else ev["r"]
        out.append(moves + (f' "{text}"' if text else "") + f" ({_mood._age_text(now, ev['t'])})")
    return out


def prompt_line(uid: int | None, is_dm: bool = False, now: float | None = None) -> str:
    """Up to two prompt lines; empty (zero tokens) when the relationship is at rest and nothing changed lately."""
    if uid is None or not _on():
        return ""
    uid, now = int(uid), time.time() if now is None else now
    lv, ch = levels(uid, now), _changes(uid, is_dm, now)
    lines = []
    if lv:
        lines.append("Toward them: " + ", ".join(f"{a} {w}" for a, w in lv))
    if ch:
        lines.append("Shifted lately (old→new): " + "; ".join(ch))
    return "\n".join(lines)


def embed_line(uid: int | None) -> str:
    v = get(uid)
    return " · ".join(f"{a} {v[a] * 100:.0f}" for a in AXES)


# DB (tiny table, write-through in the background)

def _schedule_flush(uid: int):
    try:
        task = asyncio.get_running_loop().create_task(_flush(uid))
    except RuntimeError:
        return   # no running loop (tests): the next change persists it
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _flush(uid: int):
    st = _state.get(uid)
    row = None if st is None else (json.dumps({a: round(x, 4) for a, x in st["v"].items()}), st["t"])
    try:
        async with _lock:
            async with aiosqlite.connect(DB_PATH) as db:
                if row is None:
                    await db.execute("DELETE FROM user_rel WHERE user_id = ?", (uid,))
                else:
                    await db.execute(
                        "INSERT INTO user_rel (user_id, state, updated) VALUES (?, ?, ?) "
                        "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, updated = excluded.updated",
                        (uid, row[0], row[1]))
                await db.commit()
    except Exception as e:
        console.log(f"[rel] flush FAILED user={uid}: {e}", "ERROR")


async def initialize():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS user_rel ("
                         "user_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL)")
        await db.commit()
        async with db.execute("SELECT user_id, state, updated FROM user_rel") as cur:
            for uid, state, updated in await cur.fetchall():
                try:
                    raw = json.loads(state)
                    _state[int(uid)] = {"v": {a: _mood._clamp(float(raw.get(a, REST[a]))) for a in AXES},
                                        "t": float(updated)}
                except Exception:
                    continue
    console.log(f"[rel] loaded {len(_state)} user(s)", "INFO")


async def shutdown():
    if _pending:
        await asyncio.gather(*_pending, return_exceptions=True)


def _reset_state():
    """Test helper."""
    _state.clear()
    _events.clear()
