"""Pitch bend cua 1 note (don vi giong OpenUtau/UTAU) - khong phu thuoc Discord/aiohttp/synth nen test duoc doc lap.

Dang chuan dung o moi noi (project JSON gui cho DAW, body cua /api/vocal/*, UNote.pitch):
    {"points": [{"x": ms tu dau note (am = bat dau som), "y": 0.1 semitone (10 cent) so voi note,
                 "shape": "io" | "l" | "i" | "o"}, ...],
     "snapFirst": bool}
shape cua 1 diem la kieu noi tu diem do den diem ke tiep:
    io = sine in-out (mac dinh)   l = tuyen tinh   i = sine in   o = sine out
snapFirst: diem dau lay y = (tone note truoc - tone note nay) * 10 neu 2 note sat nhau, nguoc lai 0 (nhu OpenUtau).

Doc duoc: USTX (`pitch: {data: [{x, y, shape}], snap_first}`), UST (PBS/PBW/PBY/PBM), JSON cua DAW (`points`/`snapFirst`).
Ghi ra: UST (PBS/PBW/PBY/PBM) cho synth/ttsapi.
"""
from __future__ import annotations

SHAPES = ("io", "l", "i", "o")
X_MIN, X_MAX = -2000.0, 120000.0
Y_LIMIT = 1200.0
MAX_POINTS = 256

# OpenUtau <-> UTAU PBM: o <-> r (sine out), l <-> s (linear), i <-> j (sine in), io <-> "" (sine in-out)
_SHAPE_TO_PBM = {"io": "", "l": "s", "i": "j", "o": "r"}
_PBM_TO_SHAPE = {"": "io", "s": "l", "j": "i", "r": "o"}


def _num(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _r1(value: float) -> float:
    return round(value * 10.0) / 10.0


def normalize(raw) -> dict | None:
    """Nhan dict/list bat ky (USTX, JSON cua DAW) -> dang chuan hoac None neu khong co diem hop le."""
    if isinstance(raw, dict):
        items = raw.get("points")
        if items is None:
            items = raw.get("data")
        snap = raw.get("snapFirst", raw.get("snap_first", True))
    elif isinstance(raw, (list, tuple)):
        items, snap = raw, True
    else:
        return None
    if not isinstance(items, (list, tuple)):
        return None
    points = []
    for item in items[:MAX_POINTS]:
        if isinstance(item, dict):
            x, y, shape = _num(item.get("x")), _num(item.get("y")), item.get("shape")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y, shape = _num(item[0]), _num(item[1]), item[2] if len(item) > 2 else None
        else:
            continue
        if x is None or y is None:
            continue
        points.append({
            "x": _r1(max(X_MIN, min(X_MAX, x))),
            "y": _r1(max(-Y_LIMIT, min(Y_LIMIT, y))),
            "shape": shape if shape in SHAPES else "io",     # "sp" (spline) cua OpenUtau -> io
        })
    if not points:
        return None
    points.sort(key=lambda p: p["x"])
    return {"points": points, "snapFirst": snap is not False and snap != 0 and snap != "false"}


def effective_points(pitch: dict, tone: int, prev_tone: int | None) -> list[dict]:
    """Danh sach diem sau khi ap snapFirst. prev_tone = tone note truoc neu sat nhau, nguoc lai None."""
    points = [dict(p) for p in pitch["points"]]
    if pitch.get("snapFirst", True):
        points[0]["y"] = _r1((prev_tone - tone) * 10.0) if prev_tone is not None else 0.0
    return points


def _fmt(value: float) -> str:
    text = ("%.3f" % value).rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def to_ust_lines(pitch: dict | None, tone: int, prev_tone: int | None = None) -> list[str]:
    """Cac dong UST `PBS=`, `PBW=`, `PBY=`, `PBM=` (rong neu note khong co pitch)."""
    pitch = normalize(pitch) if pitch is not None else None
    if pitch is None:
        return []
    points = effective_points(pitch, tone, prev_tone)
    if len(points) == 1:                       # 1 diem = giu nguyen y: thanh 2 diem de UST bieu dien duoc
        points.append({"x": points[0]["x"] + 10.0, "y": points[0]["y"], "shape": points[0]["shape"]})
    widths = [_fmt(points[i]["x"] - points[i - 1]["x"]) for i in range(1, len(points))]
    ys = [_fmt(points[i]["y"]) for i in range(1, len(points))]
    modes = [_SHAPE_TO_PBM[p["shape"]] for p in points]
    return [f"PBS={_fmt(points[0]['x'])};{_fmt(points[0]['y'])}",
            "PBW=" + ",".join(widths),
            "PBY=" + ",".join(ys),
            "PBM=" + ",".join(modes)]


def _split_floats(text) -> list[float]:
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        v = _num(part) if part else None
        out.append(v if v is not None else 0.0)
    return out


def from_ust(pbs, pbw, pby, pbm) -> dict | None:
    """PBS/PBW/PBY/PBM cua 1 note UST -> dang chuan (giong ParsePitchBend cua OpenUtau)."""
    has_pbs = bool(str(pbs or "").strip())
    widths = _split_floats(pbw) if str(pbw or "").strip() else []
    ys = _split_floats(pby) if str(pby or "").strip() else []
    if not has_pbs and not widths:
        return None
    x0 = y0 = 0.0
    if has_pbs:
        parts = str(pbs).replace(",", ";").split(";")
        x0 = _num(parts[0]) or 0.0
        y0 = _num(parts[1]) if len(parts) > 1 else 0.0
        y0 = y0 or 0.0
    while len(ys) < len(widths):
        ys.append(0.0)
    modes = [m.strip() for m in str(pbm or "").split(",")]
    points = [{"x": x0, "y": y0}]
    x = x0
    for w, y in zip(widths, ys):
        x += w
        points.append({"x": x, "y": y})
    for i, p in enumerate(points):
        p["shape"] = _PBM_TO_SHAPE.get(modes[i] if i < len(modes) else "", "io")
    if len(points) < 2 and not has_pbs:
        return None
    # UST khong luu snapFirst: y cua diem dau chinh la glide da tinh san -> giu nguyen, khong snap lai
    return normalize({"points": points, "snapFirst": False})
