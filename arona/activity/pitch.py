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

SHAPES = ("io", "l", "i", "o", "sp")
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
    vib = normalize_vibrato(raw.get("vibrato")) if isinstance(raw, dict) else None
    if not points and vib is None:
        return None
    if not points:                                  # chi co vibrato: duong pitch phang, khong snap
        return {"points": [{"x": -25.0, "y": 0.0, "shape": "io"}, {"x": 25.0, "y": 0.0, "shape": "io"}],
                "snapFirst": False, "vibrato": vib}
    points.sort(key=lambda p: p["x"])
    res = {"points": points, "snapFirst": snap is not False and snap != 0 and snap != "false"}
    if vib:
        res["vibrato"] = vib
    return res


VIB_FIELDS = (("length", 0.0, 100.0), ("period", 5.0, 500.0), ("depth", 0.0, 200.0), ("in", 0.0, 100.0),
              ("out", 0.0, 100.0), ("shift", 0.0, 100.0), ("drift", -100.0, 100.0), ("volLink", -100.0, 100.0))
_VIB_DEFAULT = {"length": 0.0, "period": 175.0, "depth": 25.0, "in": 10.0, "out": 10.0, "shift": 0.0, "drift": 0.0, "volLink": 0.0}


def normalize_vibrato(raw) -> dict | None:
    """Vibrato cua note (OpenUtau): length % note, period ms, depth cent, in/out % vibrato, shift/drift/volLink %. length<=0 = tat."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key, lo, hi in VIB_FIELDS:
        v = _num(raw.get(key, raw.get("vol_link") if key == "volLink" else None))
        out[key] = _r1(max(lo, min(hi, v))) if v is not None else _VIB_DEFAULT[key]
    return out if out["length"] > 0 and out["depth"] > 0 else None


def vbr_line(vib: dict | None) -> list[str]:
    v = normalize_vibrato(vib)
    if v is None:
        return []
    vals = [v["length"], v["period"], v["depth"], v["in"], v["out"], v["shift"], v["drift"], v["volLink"]]
    return ["VBR=" + ",".join(_fmt(x) for x in vals)]


def from_vbr(text) -> dict | None:
    v = _split_floats(text)
    if len(v) < 3:
        return None
    v += [0.0] * (8 - len(v))
    return normalize_vibrato(dict(zip(("length", "period", "depth", "in", "out", "shift", "drift", "volLink"), v[:8])))


def from_note(n: dict) -> dict | None:
    """Pitch + vibrato cua 1 note dang JSON (USTX: pitch/vibrato ngang hang; DAW: vibrato nam trong pitch)."""
    p, v = n.get("pitch"), n.get("vibrato")
    if isinstance(p, dict):
        if v is not None and "vibrato" not in p:
            p = {**p, "vibrato": v}
    elif v is not None:
        p = {"points": [], "vibrato": v}
    return normalize(p)


def effective_points(pitch: dict, tone: int, prev_tone: int | None) -> list[dict]:
    """Danh sach diem sau khi ap snapFirst. prev_tone = tone note truoc neu sat nhau, nguoc lai None."""
    points = [dict(p) for p in pitch["points"]]
    if pitch.get("snapFirst", True):
        points[0]["y"] = _r1((prev_tone - tone) * 10.0) if prev_tone is not None else 0.0
    return points


def _fmt(value: float) -> str:
    text = ("%.3f" % value).rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def expand_spline(points: list[dict]) -> list[dict]:
    """UST khong co spline: doan 'sp' (cubic Hermite, tiep tuyen Catmull-Rom nhu client) duoc lay mau thanh cac doan thang ngan."""
    out: list[dict] = []
    n = len(points)
    for i, a in enumerate(points):
        if a["shape"] != "sp" or i == n - 1:
            out.append({**a, "shape": "io" if a["shape"] == "sp" else a["shape"]})
            continue
        b = points[i + 1]
        dx = b["x"] - a["x"]
        slope = (b["y"] - a["y"]) / dx if dx else 0.0
        p, q = (points[i - 1] if i > 0 else None), (points[i + 2] if i + 2 < n else None)
        m0 = (b["y"] - p["y"]) / (b["x"] - p["x"]) if p and b["x"] != p["x"] else slope
        m1 = (q["y"] - a["y"]) / (q["x"] - a["x"]) if q and q["x"] != a["x"] else slope
        steps = max(1, min(40, int(dx // 10)))
        for k in range(steps):
            u = k / steps
            u2, u3 = u * u, u * u * u
            y = ((2 * u3 - 3 * u2 + 1) * a["y"] + (u3 - 2 * u2 + u) * dx * m0
                 + (-2 * u3 + 3 * u2) * b["y"] + (u3 - u2) * dx * m1)
            out.append({"x": _r1(a["x"] + dx * u), "y": _r1(y), "shape": "l"})
    return out


def to_ust_lines(pitch: dict | None, tone: int, prev_tone: int | None = None) -> list[str]:
    """Cac dong UST `PBS=`, `PBW=`, `PBY=`, `PBM=` (rong neu note khong co pitch)."""
    pitch = normalize(pitch) if pitch is not None else None
    if pitch is None:
        return []
    vbr = vbr_line(pitch.get("vibrato"))
    points = expand_spline(effective_points(pitch, tone, prev_tone))
    if len(points) == 1:                       # 1 diem = giu nguyen y: thanh 2 diem de UST bieu dien duoc
        points.append({"x": points[0]["x"] + 10.0, "y": points[0]["y"], "shape": points[0]["shape"]})
    widths = [_fmt(points[i]["x"] - points[i - 1]["x"]) for i in range(1, len(points))]
    ys = [_fmt(points[i]["y"]) for i in range(1, len(points))]
    modes = [_SHAPE_TO_PBM[p["shape"]] for p in points]
    return [f"PBS={_fmt(points[0]['x'])};{_fmt(points[0]['y'])}",
            "PBW=" + ",".join(widths),
            "PBY=" + ",".join(ys),
            "PBM=" + ",".join(modes)] + vbr


def _split_floats(text) -> list[float]:
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        v = _num(part) if part else None
        out.append(v if v is not None else 0.0)
    return out


def from_ust(pbs, pbw, pby, pbm, vbr=None) -> dict | None:
    res = _from_ust_bend(pbs, pbw, pby, pbm)
    vib = from_vbr(vbr) if vbr else None
    if vib:
        res = dict(res or {"points": [], "snapFirst": False})
        if not res["points"]:
            res = normalize({"points": [], "vibrato": vib})
        else:
            res["vibrato"] = vib
    return res


def _from_ust_bend(pbs, pbw, pby, pbm) -> dict | None:
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
