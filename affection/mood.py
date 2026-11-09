"""
affection/mood.py
~~~~~~~~~~~~~~~~~
Arona's emotions: a VECTOR of 9 intensities (0..1) instead of a single number.

    joy, warmth, excitement, sadness, anger, anxiety, surprise, fatigue, curiosity

Layers
  * GLOBAL vector  - Arona's general state: ticks every TICK_INTERVAL, relaxes toward baseline, emotions
                     interfere with each other (EMO_INTERACTIONS), CPU temperature and sleep/night push on it.
  * PER-USER offset - what a specific person did to her. Decays lazily (no tick), forgives faster when bond is
                     high, and only leaks a little into the global vector (positive 25%, negative 10%, capped/hour).
  * CAUSE journal  - short "why" notes per emotion so the model can stay consistent. Reasons coming from DMs are
                     flagged private and only shown in full to that person, in DM.
  * COLLAPSE       - which 1-2 emotions are "shown" (label / halo / prompt) is sampled, then held 30-60s so it does
                     not flicker; a big swing (surprise, large gain, new clear winner) overrides the hold.

Legacy API kept: get(), label(), nudge(), wake(), just_woke(), is_sleeping(), trigger_shocked(), is_shocked(),
shocked_reason(). get() is a VALENCE (-100..100) measured relative to baseline (baseline == 0).

Only touched from the asyncio event loop (the temperature read runs in an executor and does not touch state).
"""

import asyncio
import json
import math
import random
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timedelta

import aiosqlite

import config as C
from console import console

DB_PATH = C.AFFECTION_DB_PATH
_lock = asyncio.Lock()

try:  # zoneinfo needs the tzdata package on Windows; fall back to the host's local time.
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(C.TIMEZONE)
except Exception:
    _TZ = None

# State

_vec: dict[str, float] = dict(C.EMO_BASELINE)
_tick_counter: int = 0
_last_temp: float | None = None
_last_tick: float = time.monotonic()
_last_message_time: float = 0.0     # monotonic timestamp of the last user interaction
_is_sleeping: bool = False
_wake_time: float = -9999.0         # monotonic timestamp of last wake-up
_msg_times: deque = deque()         # epoch seconds of recent user messages (fatigue / activity)

_hist: deque = deque(maxlen=1440)   # (epoch, valence, {emotion: level}) sampled each tick (~4 h) - debug graph
_events: deque = deque(maxlen=80)   # {"t","uid","dm","r","d"} recent emotion tags - debug graph markers
_users: dict[int, dict] = {}        # uid -> {"off": {emotion: float}, "t": epoch of last decay, "seen": epoch}
_dirty_users: set = set()
_deleted_users: set = set()
_causes: list[dict] = []            # {"id","uid","e","d","r","p","s","at"}
_cause_seq: int = 0
_dirty_causes: bool = False
_collapse: dict = {}                # viewer key (uid or 0) -> collapse record
_leak: dict[str, list] = {}         # emotion -> [window_start_epoch, leaked_total]
_temp_bucket: str = "ok"
_last_temp_cause: float = -1e9
_night_cause_day: str = ""

_EMOS = C.EMOTIONS


# Small helpers

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _relax(x: float, target: float, halflife: float, dt: float) -> float:
    if dt <= 0 or halflife <= 0:
        return x
    return target + (x - target) * math.pow(0.5, dt / halflife)


def _local_dt(now: float) -> datetime:
    return datetime.fromtimestamp(now, _TZ) if _TZ else datetime.fromtimestamp(now)


def _special_leak(uid: int | None) -> float:
    try:
        from affection import special as _sp
        return _sp.leak_mult(uid)
    except Exception:
        return 1.0


def _special_warmth(uid: int | None) -> float:
    try:
        from affection import special as _sp
        return _sp.warmth_bonus(uid)
    except Exception:
        return 0.0


def _bond_value(uid: int | None) -> float:
    if uid is None:
        return 0.0
    try:
        from affection import bond as _b   # lazy: avoids an import cycle
        return _b.peek(uid)
    except Exception:
        return 0.0


_UNSAFE = re.compile(r"[<>\[\]`|{}\\]")


def clean_reason(text) -> str:
    """Sanitise a model-written reason before it is stored or shown (it ends up inside a later prompt)."""
    if not text:
        return ""
    t = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Zl", "Zp"))
    t = _UNSAFE.sub(" ", t).replace('"', "'")
    t = re.sub(r"\s+", " ", t).strip()
    return t[: C.CAUSE_REASON_MAX].strip()


# Valence (legacy single number)

def _raw(v: dict) -> float:
    return sum(C.VALENCE_WEIGHTS.get(e, 0.0) * v.get(e, 0.0) for e in _EMOS)


def valence(v: dict) -> float:
    """-100..100, 0 == baseline (the resting state)."""
    return _clamp(100.0 * (_raw(v) - _raw(C.EMO_BASELINE)), -100.0, 100.0)


# Tag parsing

_SIGN = "+\\-−–—"
_LEGACY_NUM = re.compile(rf"([{_SIGN}]?)\s*(\d+(?:\.\d+)?)")
_EMO_TOKEN = re.compile(rf"([a-z]+)\s*([{_SIGN}]?)\s*(\d+(?:\.\d+)?)", re.I)


def parse_tag_body(body: str) -> tuple[dict, str, bool]:
    """
    '<mood>BODY</mood>' -> (deltas in intensity, cleaned reason, is_legacy).
      new:    'joy+8 anxiety+3 | reason'   (closed vocabulary, <= EMO_TAG_MAX_COUNT emotions, +-EMO_TAG_MAX units)
      legacy: '12' / '-5'                  (N/30 x 0.15 -> joy if positive, sadness if negative)
    Never raises; unknown emotion names are ignored.
    """
    left, _sep, right = str(body).partition("|")
    reason = clean_reason(right)
    left = left.strip()
    m = _LEGACY_NUM.fullmatch(left)
    if m:
        sign = -1.0 if m.group(1) in ("-", "−", "–", "—") else 1.0
        n = _clamp(sign * float(m.group(2)), -30.0, 30.0)
        d = n * C.LEGACY_TAG_SCALE
        if d > 0:
            return {"joy": d}, reason, True
        if d < 0:
            return {"sadness": -d}, reason, True
        return {}, reason, True
    deltas: dict[str, float] = {}
    for name, sign, num in _EMO_TOKEN.findall(left):
        name = name.lower()
        if name not in _EMOS:
            continue
        if name not in deltas and len(deltas) >= C.EMO_TAG_MAX_COUNT:
            continue
        units = min(float(num), float(C.EMO_TAG_MAX))
        s = -1.0 if sign in ("-", "−", "–", "—") else 1.0
        deltas[name] = _clamp(deltas.get(name, 0.0) + s * units * C.EMO_UNIT,
                              -C.EMO_TAG_MAX * C.EMO_UNIT, C.EMO_TAG_MAX * C.EMO_UNIT)
    return {k: v for k, v in deltas.items() if v != 0.0}, reason, False


# Cause journal

def _cause_halflife(c: dict) -> float:
    e = c["e"]
    if c["uid"] is None or e in ("surprise", "fatigue"):
        return float(C.EMO_HALFLIFE[e])
    return float(C.USER_HALFLIFE.get(e, C.EMO_HALFLIFE[e]))


def _contrib(c: dict, now: float) -> float:
    return c["d"] * math.pow(0.5, max(0.0, now - c["at"]) / _cause_halflife(c))


def _viewer_weight(c: dict, viewer: int | None) -> float:
    """Other people's causes only matter to this viewer in proportion to how much leaked into the global mood."""
    if c["uid"] is None or c["uid"] == viewer:
        return 1.0
    if c["e"] in C.POSITIVE_EMOS:
        return C.LEAK_POSITIVE
    if c["e"] in C.NEGATIVE_EMOS:
        return C.LEAK_NEGATIVE
    return 1.0


def _resolve_causes(uid: int | None, emotion: str, amount: float):
    """A negative delta (apology, good news) eats the matching causes, oldest first."""
    global _dirty_causes
    remaining = amount
    for c in sorted((c for c in _causes if c["uid"] == uid and c["e"] == emotion), key=lambda c: c["at"]):
        if remaining <= 0:
            break
        take = min(c["d"], remaining)
        c["d"] -= take
        remaining -= take
        _dirty_causes = True
    _causes[:] = [c for c in _causes if c["d"] > C.CAUSE_PRUNE_MIN]


def _add_cause(uid: int | None, emotion: str, delta: float, reason: str,
               private: bool = False, system: bool = False, now: float | None = None):
    global _cause_seq, _dirty_causes
    reason = clean_reason(reason)
    if not reason or abs(delta) < C.CAUSE_MIN_DELTA:
        return
    now = time.time() if now is None else now
    if delta < 0:
        _resolve_causes(uid, emotion, -delta)
        return
    _cause_seq += 1
    _causes.append({"id": _cause_seq, "uid": uid, "e": emotion, "d": float(delta), "r": reason,
                    "p": bool(private), "s": bool(system), "at": now})
    same = [c for c in _causes if c["uid"] == uid and c["e"] == emotion]
    for old in sorted(same, key=lambda c: c["at"])[: max(0, len(same) - C.CAUSE_MAX_PER_EMO)]:
        _causes.remove(old)
    if len(_causes) > C.CAUSE_TOTAL_MAX:
        del _causes[: len(_causes) - C.CAUSE_TOTAL_MAX]
    _dirty_causes = True


def _prune_causes(now: float):
    global _dirty_causes
    before = len(_causes)
    _causes[:] = [c for c in _causes
                  if now - c["at"] <= C.CAUSE_MAX_AGE and _contrib(c, now) >= C.CAUSE_PRUNE_MIN]
    if len(_causes) != before:
        _dirty_causes = True


def _why_lines(emotion: str, viewer: int | None, is_dm: bool, now: float) -> list[str]:
    items = []
    for c in _causes:
        if c["e"] != emotion:
            continue
        amt = _contrib(c, now) * _viewer_weight(c, viewer)
        if amt < C.CAUSE_SHOW_MIN:
            continue
        mine = viewer is not None and c["uid"] == viewer
        text = "a private matter" if (c["p"] and not (mine and is_dm)) else clean_reason(c["r"])
        items.append((amt, text, c["at"], c["s"]))
    items.sort(key=lambda x: -x[0])
    out = []
    for _amt, text, at, is_sys in items[:2]:
        mins = max(0, int((now - at) / 60))
        age = "just now" if mins < 2 else (f"~{mins}m ago" if mins < 120 else f"~{mins // 60}h ago")
        out.append(f"{emotion}: {text} ({age})" if is_sys else f'{emotion}: "{text}" ({age})')
    return out


# Per-user offsets

def _user_state(uid: int, now: float, create: bool = False) -> dict | None:
    st = _users.get(uid)
    if st is None and create:
        st = {"off": {}, "t": now, "seen": now}
        _users[uid] = st
        _deleted_users.discard(uid)
    return st


def _decay_offset(st: dict, uid: int, now: float):
    """Lazy decay since st['t']. Time past SLEEP_AFTER counts as 'offline' (negative feelings fade faster)."""
    dt = max(0.0, now - st["t"])
    if dt <= 0:
        return
    bond = _bond_value(uid)
    forgive = 1.0 - min(C.BOND_FORGIVE_MAX, bond / C.BOND_FORGIVE_DIV)
    floor_warmth = bond * C.WARMTH_FLOOR_PER_BOND
    online = min(dt, C.SLEEP_AFTER)
    offline = dt - online
    for e, x in list(st["off"].items()):
        h = float(C.USER_HALFLIFE.get(e, 3600.0))
        if e in C.NEGATIVE_EMOS:
            h *= forgive
        target = floor_warmth if (e == "warmth" and x > 0) else 0.0
        x = _relax(x, target, h, online)
        if offline > 0:
            x = _relax(x, target, h * C.OFFLINE_DECAY_MULT.get(e, 1.0), offline)
        st["off"][e] = x
    st["off"] = {e: x for e, x in st["off"].items() if abs(x) >= 0.003}
    st["t"] = now
    _dirty_users.add(uid)


def touch_user(uid: int, now: float | None = None):
    """Mark a user active (keeps their offset from being purged) and apply pending decay."""
    now = time.time() if now is None else now
    st = _users.get(int(uid))
    if st is not None:
        _decay_offset(st, int(uid), now)
        st["seen"] = now
        _dirty_users.add(int(uid))


def _purge_users(now: float):
    for uid, st in list(_users.items()):
        if now - st["seen"] > C.USER_IDLE_PURGE:
            del _users[uid]
            _dirty_users.discard(uid)
            _deleted_users.add(uid)


def effective(viewer: int | None = None, now: float | None = None) -> dict:
    """Global vector + this viewer's offset (+ a small personal warmth bonus from bond)."""
    now = time.time() if now is None else now
    out = dict(_vec)
    if viewer is None:
        return out
    viewer = int(viewer)
    st = _users.get(viewer)
    if st is not None:
        _decay_offset(st, viewer, now)
        for e, x in st["off"].items():
            out[e] = _clamp(out[e] + x)
    out["warmth"] = _clamp(out["warmth"] + min(C.BOND_WARMTH_MAX, _bond_value(viewer) * C.BOND_WARMTH_PER_POINT)
                           + _special_warmth(viewer))
    return out


# Applying changes

def _add_global(e: str, d: float):
    _vec[e] = _clamp(_vec[e] + d)


def _leak_global(e: str, d: float, now: float, mult: float = 1.0):
    ratio = C.LEAK_POSITIVE if e in C.POSITIVE_EMOS else C.LEAK_NEGATIVE
    amount = d * ratio * mult
    w = _leak.get(e)
    if w is None or now - w[0] >= 3600.0:
        w = _leak[e] = [now, 0.0]
    room = C.LEAK_HOURLY_CAP - w[1]
    if room <= 0:
        return
    take = math.copysign(min(abs(amount), room), amount)
    w[1] += abs(take)
    _add_global(e, take)


def apply_emotions(deltas: dict, reason: str = "", user_id: int | None = None,
                   is_dm: bool = False, now: float | None = None) -> dict:
    """
    Apply parsed tag deltas (intensity units). With a user, deltas go to that user's offset and leak a little
    into the global vector (surprise/fatigue are global). Without a user (scheduler) they go to the global vector.
    Returns what was actually applied per emotion (for logging/tests).
    """
    now = time.time() if now is None else now
    reason = clean_reason(reason)
    applied: dict[str, float] = {}
    cap = C.EMO_TAG_MAX * C.EMO_UNIT
    deltas = {e: _clamp(float(d), -cap, cap) for e, d in deltas.items() if e in _EMOS and d}
    if not deltas:
        return applied
    if user_id is None:
        for e, d in deltas.items():
            before = _vec[e]
            _add_global(e, d)
            applied[e] = _vec[e] - before
            _add_cause(None, e, applied[e], reason, private=False, now=now)
        return applied
    uid = int(user_id)
    st = _user_state(uid, now, create=True)
    _decay_offset(st, uid, now)
    st["seen"] = now
    forgive = 1.0 - min(C.BOND_FORGIVE_MAX, _bond_value(uid) / C.BOND_FORGIVE_DIV)
    for e, d in deltas.items():
        if e in ("surprise", "fatigue"):
            before = _vec[e]
            _add_global(e, d)
            applied[e] = _vec[e] - before
            _add_cause(uid, e, applied[e], reason, private=is_dm, now=now)
            continue
        if e in C.NEGATIVE_EMOS and d > 0:
            d *= forgive
        cur = st["off"].get(e, 0.0)
        new = _clamp(cur + d, -C.USER_OFFSET_MAX, C.USER_OFFSET_MAX)
        st["off"][e] = new
        applied[e] = new - cur
        _leak_global(e, applied[e], now, _special_leak(uid))
        _add_cause(uid, e, applied[e], reason, private=is_dm, now=now)
    _dirty_users.add(uid)
    _events.append({"t": now, "uid": uid, "dm": bool(is_dm), "r": reason or "",
                    "d": {e: round(x, 3) for e, x in applied.items() if abs(x) >= 0.005}})
    return applied


# Collapse / readout

def _salience(vec: dict) -> dict:
    return {e: max(0.0, vec[e] - C.EMO_BASELINE[e]) for e in _EMOS}


def _pick(vec: dict) -> list[str]:
    sal = _salience(vec)
    top = max(sal.values())
    if top < 0.05:
        return []
    weights = {e: math.exp((s - top) / C.COLLAPSE_TEMP) for e, s in sal.items() if s > 0.0}
    r = random.random() * sum(weights.values())
    first = next(iter(weights))
    acc = 0.0
    for e, w in weights.items():
        acc += w
        if r <= acc:
            first = e
            break
    chosen = [first]
    rest = sorted(((s, e) for e, s in sal.items() if e != first), reverse=True)
    if rest and rest[0][0] >= C.COLLAPSE_SECOND_MIN and rest[0][0] >= C.COLLAPSE_SECOND_RATIO * sal[first]:
        chosen.append(rest[0][1])
    return chosen


def _collapse_get(key: int, vec: dict) -> list[str]:
    now_m = time.monotonic()
    c = _collapse.get(key)
    if c is not None and now_m < c["until"]:
        if now_m - c["last_override"] >= C.COLLAPSE_OVERRIDE_MIN_GAP:
            sal = _salience(vec)
            others_gain = max((vec[e] - c["snap"][e] for e in _EMOS if e not in c["emos"]), default=0.0)
            shown_top = max((sal[e] for e in c["emos"]), default=0.0)
            override = (
                (vec["surprise"] >= C.COLLAPSE_OVERRIDE_SURPRISE and "surprise" not in c["emos"])
                or others_gain >= C.COLLAPSE_OVERRIDE_DELTA
                or (max(sal.values()) - shown_top) >= C.COLLAPSE_OVERRIDE_MARGIN
            )
            if override:
                c = None
                last_override = now_m
            else:
                return c["emos"]
        else:
            return c["emos"]
    else:
        last_override = c["last_override"] if c is not None else -1e9
    emos = _pick(vec)
    _collapse[key] = {
        "emos": emos, "snap": dict(vec), "last_override": last_override,
        "until": now_m + random.uniform(C.COLLAPSE_MIN, C.COLLAPSE_MAX),
    }
    return emos


def _label_for(vec: dict, dom: list[str]) -> str:
    """Label/halo from the dominant emotion. Thresholds are the level ABOVE baseline (surprise's baseline is 0)."""
    if not dom:
        return "default"
    e = dom[0]
    over = vec[e] - C.EMO_BASELINE[e]
    if e == "joy":
        return "delighted" if vec[e] >= C.LABEL_DELIGHTED_JOY else ("happy" if over >= C.LABEL_THRESHOLDS["joy"] else "default")
    if e == "warmth":
        return "happy" if over >= C.LABEL_THRESHOLDS["warmth"] else "default"
    names = {"surprise": "shocked", "anger": "angered", "sadness": "sad", "excitement": "motivated"}
    return names[e] if e in names and over >= C.LABEL_THRESHOLDS[e] else "default"


def _word(x: float) -> str:
    return "overwhelming" if x >= 0.75 else "strong" if x >= 0.55 else "moderate" if x >= 0.35 \
        else "slight" if x >= 0.15 else "faint"


def _blend(vec: dict, dom: list[str]) -> str:
    if not dom:
        return "calm"
    parts = [f"mostly {dom[0]} ({_word(vec[dom[0]])})"]
    if len(dom) > 1:
        parts.append(f"with some {dom[1]} ({_word(vec[dom[1]])})")
    if "fatigue" not in dom and vec["fatigue"] >= 0.30:
        parts.append(f"also tired (fatigue {_word(vec['fatigue'])})")
    return ", ".join(parts)


def describe(viewer: int | None = None, is_dm: bool = False, now: float | None = None) -> dict:
    """Readout for the prompt / UI: label, blend text, valence, 'why' lines (privacy-aware) and the full vector."""
    now = time.time() if now is None else now
    vec = effective(viewer, now)
    dom = _collapse_get(int(viewer) if viewer is not None else 0, vec)
    lbl = _label_for(vec, dom)
    shown = list(dom)
    if vec["fatigue"] >= 0.30 and "fatigue" not in shown:
        shown.append("fatigue")
    why: list[str] = []
    for e in shown:
        why += _why_lines(e, viewer, is_dm, now)
    return {"label": lbl, "desc": C.MOOD_LABEL_DESC.get(lbl, ""), "blend": _blend(vec, dom),
            "valence": valence(vec), "why": why[:4], "dominant": dom, "vec": vec}


# Legacy-compatible public API

def get() -> float:
    """Global valence (-100..100, baseline = 0)."""
    return valence(_vec)


def get_for(uid: int | None) -> float:
    return valence(effective(uid))


def label(viewer: int | None = None) -> tuple[str, str]:
    d = describe(viewer)
    return d["label"], (d["desc"] + " " + d["blend"]).strip()


def is_sleeping() -> bool:
    return _is_sleeping


def _touch(count_activity: bool):
    global _last_message_time, _is_sleeping, _wake_time
    now = time.monotonic()
    if _is_sleeping:
        _wake_time = now
    _last_message_time = now
    _is_sleeping = False
    if count_activity:
        t = time.time()
        _msg_times.append(t)
        while _msg_times and t - _msg_times[0] > C.FATIGUE_ACTIVITY_WINDOW:
            _msg_times.popleft()


def wake():
    """A user interaction happened: reset the idle timer (and wake her up if she was sleeping)."""
    was_asleep = _is_sleeping
    _touch(True)
    if was_asleep:
        _add_global("fatigue", C.FATIGUE_WAKE_BUMP)
        _add_cause(None, "fatigue", C.FATIGUE_WAKE_BUMP, "she just woke up from sleeping", system=True)


def just_woke(window: float = C.WAKE_DISPLAY_WINDOW) -> bool:
    return (time.monotonic() - _wake_time) < window


def nudge(delta: float):
    """Legacy numeric nudge (-30..30 style): positive -> joy, negative -> sadness (global)."""
    _touch(False)
    d = float(delta) * C.LEGACY_TAG_SCALE
    if d > 0:
        _add_global("joy", d)
    elif d < 0:
        _add_global("sadness", -d)


def trigger_shocked(reason: str, duration: float | None = None, user_id: int | None = None, is_dm: bool = False):
    """Spike surprise (global). It decays by itself (half-life 60s, so the 'shocked' label lasts about a minute)."""
    reason = clean_reason(reason)
    if not reason:
        console.log("[mood] trigger_shocked() called with empty reason, ignoring", "WARN")
        return
    _add_global("surprise", 0.6)
    _add_cause(user_id, "surprise", 0.6, reason, private=is_dm)
    console.log(f"[mood] shocked triggered (reason_len={len(reason)} dm={is_dm})", "DEBUG")


def is_shocked() -> bool:
    return _vec["surprise"] >= C.LABEL_THRESHOLDS["surprise"]


def shocked_reason(viewer: int | None = None, is_dm: bool = False) -> str:
    if not is_shocked():
        return ""
    lines = _why_lines("surprise", viewer, is_dm, time.time())
    if not lines:
        return ""
    m = re.match(r'surprise: "?(.*?)"? \(', lines[0])
    return m.group(1) if m else ""


# Fatigue / temperature

def _fatigue_target(now: float, sleeping: bool) -> float:
    if sleeping:
        return 0.0
    h = _local_dt(now).hour
    night = C.FATIGUE_NIGHT_BONUS if (h >= 23 or h < 6) else (C.FATIGUE_LATE_BONUS if (h == 22 or h == 6) else 0.0)
    while _msg_times and now - _msg_times[0] > C.FATIGUE_ACTIVITY_WINDOW:
        _msg_times.popleft()
    act = min(C.FATIGUE_ACTIVITY_MAX, C.FATIGUE_ACTIVITY_MAX * len(_msg_times) / C.FATIGUE_ACTIVITY_FULL)
    return min(0.9, C.EMO_BASELINE["fatigue"] + night + act)


def _read_temp() -> float | None:
    # Windows - ACPI thermal zones, no external app needed
    try:
        import wmi
        w = wmi.WMI(namespace="root\\wmi")
        zones = w.MSAcpi_ThermalZoneTemperature()
        if zones:
            return max((z.CurrentTemperature / 10.0) - 273.15 for z in zones)
    except Exception:
        pass
    # Linux fallback
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        if temps:
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps and temps[key]:
                    return max(e.current for e in temps[key])
            for entries in temps.values():
                if entries:
                    return max(e.current for e in entries)
    except Exception:
        pass
    return None


def _temp_delta(temp: float | None) -> float:
    if temp is None:
        return 0.0
    if temp < C.TEMP_FREEZE:
        return C.TEMP_FREEZE_DELTA
    if temp < C.TEMP_COOL:
        return C.TEMP_COOL_DELTA
    if temp < C.TEMP_SWEET:
        return C.TEMP_SWEET_DELTA
    if temp < C.TEMP_WARM:
        return 0.0
    if temp < C.TEMP_HOT:
        return C.TEMP_WARM_DELTA
    if temp < C.TEMP_OVER:
        return C.TEMP_HOT_DELTA
    return C.TEMP_OVER_DELTA


def _note_temperature(temp: float | None, now: float):
    """Backend-known cause: log it once when the machine gets hot (not for the 'too cold / idle' range)."""
    global _temp_bucket, _last_temp_cause
    bucket = "ok"
    if temp is not None:
        bucket = "over" if temp >= C.TEMP_OVER else "hot" if temp >= C.TEMP_HOT else "ok"
    if bucket != "ok" and bucket != _temp_bucket and now - _last_temp_cause > 600.0:
        _add_cause(None, "anxiety", 0.08,
                   "the machine she runs on is overheating" if bucket == "over" else "the machine she runs on is running hot",
                   system=True, now=now)
        _last_temp_cause = now
    _temp_bucket = bucket


def _note_night(now: float):
    global _night_cause_day
    dt = _local_dt(now)
    if not (dt.hour >= 23 or dt.hour < 6):
        return
    key = (dt - timedelta(hours=7)).date().isoformat()
    if key != _night_cause_day:
        _night_cause_day = key
        _add_cause(None, "fatigue", 0.15, "it is very late at night", system=True, now=now)


# Tick

def _step(dt: float, now: float, sleeping: bool, temp_delta: float = 0.0):
    """One global update of `dt` seconds. Pure state change (no I/O) so it can be unit-tested."""
    v = _vec
    base = C.EMO_BASELINE
    # 1. relax toward baseline (fatigue relaxes toward a computed target instead)
    for e in _EMOS:
        if e == "fatigue":
            continue
        h = float(C.EMO_HALFLIFE[e])
        if sleeping:
            h *= C.OFFLINE_DECAY_MULT.get(e, 1.0)
        v[e] = _relax(v[e], base[e], h, dt)
    target = _fatigue_target(now, sleeping)
    if sleeping:
        fh = float(C.FATIGUE_SLEEP_HALFLIFE)
    elif target > v["fatigue"]:
        fh = float(C.FATIGUE_RISE_HALFLIFE)
    else:
        fh = float(C.EMO_HALFLIFE["fatigue"])
    v["fatigue"] = _relax(v["fatigue"], target, fh, dt)
    # 2. interference between emotions (salience measured at the start of the step)
    k = dt / C.TICK_INTERVAL
    sal = _salience(v)
    for src, dst, coef in C.EMO_INTERACTIONS:
        v[dst] += coef * sal[src] * k
    # 3. CPU temperature pressure (hot -> anxious/irritable, comfortable -> a little warmer)
    if temp_delta < 0:
        v["anxiety"] += C.TEMP_ANXIETY_COEF * abs(temp_delta) * k
        v["anger"] += C.TEMP_ANGER_COEF * abs(temp_delta) * k
    elif temp_delta > 0:
        v["warmth"] += C.TEMP_WARMTH_COEF * temp_delta * k
    # 4. sleeping regenerates joy (up to SLEEP_JOY_CAP), like the old HAPPY_CAP regen
    if sleeping and v["joy"] < C.SLEEP_JOY_CAP:
        v["joy"] += C.SLEEP_JOY_REGEN_PER_TICK * k
    # 5. tiny random drift so she is never perfectly static
    for e in ("joy", "curiosity", "anxiety"):
        v[e] += random.gauss(0.0, C.EMO_DRIFT_STD) * math.sqrt(max(k, 0.0))
    for e in _EMOS:
        v[e] = _clamp(v[e])


def _sample(now: float) -> None:
    eff = effective(None, now)
    _hist.append((now, round(valence(eff), 2), {e: round(eff[e], 4) for e in _EMOS}))


def _special_label(uid: int) -> str:
    try:
        from affection import special as _sp
        i = _sp.get(uid)
        return (i["label"] + (" (strained)" if i["strained"] else "")) if i else ""
    except Exception:
        return ""


def history(hours: float = 1.0, max_points: int = 360) -> dict:
    """Debug graph data: global emotion levels over time (downsampled), recent tag events, current per-user top emotions."""
    cut = time.time() - hours * 3600
    h = [x for x in _hist if x[0] >= cut]
    if len(h) > max_points:
        step = len(h) / max_points
        h = [h[int(i * step)] for i in range(max_points)] + [h[-1]]
    now = time.time()
    users = []
    for uid in list(_users):
        eff = effective(uid, now)
        top = sorted(_EMOS, key=lambda e: eff[e] - C.EMO_BASELINE[e], reverse=True)[:2]
        users.append({"uid": uid, "rel": _special_label(uid), "valence": round(valence(eff), 1),
                      "top": [[e, round(eff[e], 2)] for e in top]})
    users.sort(key=lambda u: -abs(u["valence"]))
    return {"emos": list(_EMOS), "base": C.EMO_BASELINE, "now": now,
            "pts": [{"t": t, "val": v, "v": vec} for t, v, vec in h],
            "events": list(_events), "users": users[:12]}


async def tick_loop():
    """Run as asyncio.create_task(). Never raises."""
    global _tick_counter, _last_temp, _is_sleeping, _last_tick
    while True:
        await asyncio.sleep(C.TICK_INTERVAL)
        _tick_counter += 1
        try:
            now_m = time.monotonic()
            dt = min(600.0, max(0.1, now_m - _last_tick))
            _last_tick = now_m
            _is_sleeping = (now_m - _last_message_time) >= C.SLEEP_AFTER
            if _tick_counter % C.TEMP_READ_EVERY_N == 0:
                _last_temp = await asyncio.get_running_loop().run_in_executor(None, _read_temp)
            now = time.time()
            td = _temp_delta(_last_temp)
            if _last_temp is not None and _last_temp < C.TEMP_FREEZE:
                td = 0.0   # an idle/cool machine is not an emotion
            _note_temperature(_last_temp, now)
            _note_night(now)
            _step(dt, now, _is_sleeping, td)
            _sample(now)
            if _tick_counter % 6 == 0:
                _prune_causes(now)
                _purge_users(now)
            if _tick_counter % C.DB_FLUSH_EVERY_N == 0:
                await _flush_all()
        except Exception as e:
            console.log(f"[mood] tick_loop error: {e}", "ERROR")


# DB

async def _init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS global_mood (
                id   INTEGER PRIMARY KEY CHECK (id = 0),
                mood REAL    NOT NULL DEFAULT 0.0
            )
        """)
        await db.execute("INSERT OR IGNORE INTO global_mood (id, mood) VALUES (0, 0.0)")
        async with db.execute("PRAGMA table_info(global_mood)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        if "state" not in cols:   # migration: the vector is the source of truth, `mood` is kept for compatibility
            await db.execute("ALTER TABLE global_mood ADD COLUMN state TEXT")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_mood (
                user_id   INTEGER PRIMARY KEY,
                state     TEXT NOT NULL,
                updated   REAL NOT NULL,
                last_seen REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mood_cause (
                id      INTEGER PRIMARY KEY,
                user_id INTEGER,
                emotion TEXT NOT NULL,
                delta   REAL NOT NULL,
                reason  TEXT NOT NULL,
                private INTEGER NOT NULL DEFAULT 0,
                system  INTEGER NOT NULL DEFAULT 0,
                at      REAL NOT NULL
            )
        """)
        await db.commit()


def _seed_from_legacy(mood_value: float) -> dict:
    """Old single-number mood -> vector (same scale as a legacy tag: N/30 x 0.15)."""
    v = dict(C.EMO_BASELINE)
    d = float(mood_value) * C.LEGACY_TAG_SCALE
    if d > 0:
        v["joy"] = _clamp(v["joy"] + d)
    elif d < 0:
        v["sadness"] = _clamp(v["sadness"] - d)
    return v


async def _load():
    global _vec, _cause_seq
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT mood, state FROM global_mood WHERE id = 0") as cur:
            row = await cur.fetchone()
        mood_value, state = (row[0], row[1]) if row else (0.0, None)
        loaded = None
        if state:
            try:
                raw = json.loads(state).get("v", {})
                loaded = {e: _clamp(float(raw.get(e, C.EMO_BASELINE[e]))) for e in _EMOS}
            except Exception as e:
                console.log(f"[mood] bad saved state, reseeding from legacy mood: {e}", "WARN")
        _vec = loaded if loaded is not None else _seed_from_legacy(mood_value or 0.0)
        async with db.execute("SELECT user_id, state, updated, last_seen FROM user_mood") as cur:
            for uid, st, updated, seen in await cur.fetchall():
                try:
                    off = {e: float(x) for e, x in json.loads(st).get("off", {}).items() if e in _EMOS}
                except Exception:
                    off = {}
                _users[int(uid)] = {"off": off, "t": float(updated), "seen": float(seen)}
        async with db.execute("SELECT id, user_id, emotion, delta, reason, private, system, at FROM mood_cause") as cur:
            for cid, uid, e, d, r, p, s, at in await cur.fetchall():
                if e in _EMOS:
                    _causes.append({"id": int(cid), "uid": uid, "e": e, "d": float(d), "r": clean_reason(r),
                                    "p": bool(p), "s": bool(s), "at": float(at)})
    _cause_seq = max((c["id"] for c in _causes), default=0)
    _prune_causes(time.time())
    console.log(f"[mood] loaded: valence={valence(_vec):+.1f} users={len(_users)} causes={len(_causes)}", "INFO")


async def _flush_all(force: bool = False):
    """Write the global vector, dirty user offsets and the cause journal. Rows are snapshotted before awaiting."""
    global _dirty_causes
    state = json.dumps({"v": {e: round(_vec[e], 5) for e in _EMOS}})
    mood_value = valence(_vec)
    users = [(uid, json.dumps({"off": {e: round(x, 5) for e, x in _users[uid]["off"].items()}}),
              _users[uid]["t"], _users[uid]["seen"]) for uid in list(_dirty_users) if uid in _users]
    deleted = list(_deleted_users)
    causes = None
    if _dirty_causes or force:
        causes = [(c["id"], c["uid"], c["e"], c["d"], c["r"], int(c["p"]), int(c["s"]), c["at"]) for c in _causes]
    _dirty_users.clear()
    _deleted_users.clear()
    _dirty_causes = False
    try:
        async with _lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE global_mood SET mood = ?, state = ? WHERE id = 0", (mood_value, state))
                for uid, st, updated, seen in users:
                    await db.execute(
                        "INSERT INTO user_mood (user_id, state, updated, last_seen) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, updated = excluded.updated, "
                        "last_seen = excluded.last_seen", (uid, st, updated, seen))
                for uid in deleted:
                    await db.execute("DELETE FROM user_mood WHERE user_id = ?", (uid,))
                if causes is not None:
                    await db.execute("DELETE FROM mood_cause")
                    await db.executemany(
                        "INSERT INTO mood_cause (id, user_id, emotion, delta, reason, private, system, at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", causes)
                await db.commit()
    except Exception as e:
        # put the dirty flags back so the next flush retries
        _dirty_users.update(u[0] for u in users)
        _deleted_users.update(deleted)
        if causes is not None:
            _dirty_causes = True
        console.log(f"[mood] flush FAILED: {e}", "ERROR")


# Public lifecycle

async def initialize():
    global _last_message_time, _last_tick
    await _init_db()
    await _load()
    _last_message_time = time.monotonic()   # do not start the day asleep
    _last_tick = time.monotonic()


async def shutdown():
    """Call during bot shutdown so the vector, offsets and causes are saved."""
    await _flush_all(force=True)


def _reset_state():
    """Test helper: back to a fresh in-memory state."""
    global _vec, _tick_counter, _last_temp, _last_message_time, _is_sleeping, _wake_time, _cause_seq
    global _dirty_causes, _temp_bucket, _last_temp_cause, _night_cause_day
    _vec = dict(C.EMO_BASELINE)
    _tick_counter = 0
    _last_temp = None
    _last_message_time = time.monotonic()
    _is_sleeping = False
    _wake_time = -9999.0
    _msg_times.clear()
    _users.clear()
    _hist.clear()
    _events.clear()
    _dirty_users.clear()
    _deleted_users.clear()
    _causes.clear()
    _cause_seq = 0
    _dirty_causes = False
    _collapse.clear()
    _leak.clear()
    _temp_bucket = "ok"
    _last_temp_cause = -1e9
    _night_cause_day = ""
