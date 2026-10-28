"""Chia track vocal thanh chunk nho de chi gen lai chunk bi sua (khong dung Discord/aiohttp -> test duoc doc lap).

Y tuong:
- Chunk bi cat o cac khoang lang (>= MIN_GAP_SEC) giua 2 note, nen moi chunk la 1 "cau hat" doc lap voi cac chunk khac
  (GPT-SoVITS vong lap theo phrase, nen cat o cho nghi khong lam doi giong).
- Chunk qua dai (> MAX_CHUNK_SEC) bi cat tiep o khoang lang lon nhat ben trong, het cach thi cat o ranh gioi note giua chunk.
- Moi chunk co `hash` = sha1(noi dung UST cua chunk + tham so gen). UST cua chunk BAT DAU TU NOTE DAU TIEN (khong co rest dau),
  nen keo ca chunk sang cho khac tren timeline van trung cache, chi `start_sec` doi.
- Cache hit/miss quyet dinh boi hash -> sua 1 note chi doi hash cua dung chunk chua note do.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from arona.activity import pitch as pitchmod

MIN_GAP_SEC = 0.35      # khoang lang toi thieu de cat chunk
MAX_CHUNK_SEC = 12.0    # chunk dai hon se bi cat nho them
MAX_CHUNK_NOTES = 48    # chunk nhieu note hon cung bi cat (gen cham theo so phrase)
UST_PPQ = 480           # UST luon la 480 tick / beat (giong ustx_track_to_ust ben synth.py)


@dataclass(frozen=True)
class Note:
    pos: int     # tick tuyet doi, theo ppq cua project
    dur: int
    tone: int
    lyric: str
    pitch: dict | None = None    # pitch bend cua note (xem arona/activity/pitch.py)


@dataclass
class Chunk:
    index: int
    start_tick: int
    end_tick: int
    start_sec: float
    end_sec: float
    notes: list
    hash: str = ""

    def info(self) -> dict:
        return {"index": self.index, "hash": self.hash, "start_sec": round(self.start_sec, 4),
                "end_sec": round(self.end_sec, 4), "notes": len(self.notes)}


def normalize_tempos(tempos) -> list[tuple[int, float]]:
    """[[tick, bpm], ...] -> danh sach sap xep, luon co phan tu tick 0."""
    out = sorted(((int(t), float(b)) for t, b in (tempos or []) if float(b) > 0), key=lambda x: x[0])
    if not out or out[0][0] > 0:
        out.insert(0, (0, out[0][1] if out else 120.0))
    return out


def tempo_at(tempos: list[tuple[int, float]], tick: int) -> float:
    bpm = tempos[0][1]
    for t, b in tempos:
        if t <= tick:
            bpm = b
        else:
            break
    return bpm


def tick_to_sec(tick: int, ppq: int, tempos: list[tuple[int, float]]) -> float:
    sec, prev_t, prev_b = 0.0, 0, tempos[0][1]
    for t, b in tempos[1:]:
        if t >= tick:
            break
        sec += (t - prev_t) * 60.0 / (prev_b * ppq)
        prev_t, prev_b = t, b
    return sec + (tick - prev_t) * 60.0 / (prev_b * ppq)


def clean_notes(raw_notes) -> list[Note]:
    """Sap xep, cat phan chong len nhau (giong ustx_track_to_ust), bo note rong."""
    out, cursor = [], 0
    for n in sorted(raw_notes, key=lambda n: (int(n["pos"]), int(n["tone"]))):
        pos, dur = int(n["pos"]), int(n["dur"])
        start, end = max(pos, cursor), pos + dur
        if end <= start:
            continue
        lyric = str(n.get("lyric", "")).replace("\n", " ").strip() or "a"
        out.append(Note(start, end - start, max(0, min(127, int(n["tone"]))), lyric, pitchmod.normalize(n.get("pitch"))))
        cursor = end
    return out


def auto_transpose(raw_notes, voice_center: float = 66.0) -> int:
    """Octave shift cho CA TRACK (giong quy tac auto_octave cua /synth).

    Phai tinh tu toan bo note cua track, khong phai tung chunk: neu de server tinh rieng
    thi moi chunk se roi vao 1 quang 8 khac nhau -> giong bi nhay octave giua cac cau.
    """
    nums = sorted(int(n["tone"]) for n in raw_notes or []
                  if n.get("transpose") is None      # track chinh tay khong tham gia tinh auto
                  and str(n.get("lyric", "")).strip().lower() not in ("", "r"))
    if not nums:
        return 0
    mid = len(nums) // 2
    med = float(nums[mid]) if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2.0
    return 12 * int(round((float(voice_center) - med) / 12.0))


def apply_track_transpose(raw_notes, base: int):
    """Track co `transpose` thu cong (absolute, semitone) thi gen dung muc do, khong theo auto.

    Synth se cong them `base` (transpose chung cua ca bai) vao moi note, nen note cua track thu cong
    duoc dich san (manual - base) de sau khi cong lai thanh dung `manual`. Note khong co `transpose` giu nguyen.
    """
    out = []
    for n in raw_notes or []:
        manual = n.get("transpose")
        if manual is None:
            out.append(n)
            continue
        out.append({**n, "tone": int(n["tone"]) + int(manual) - int(base)})
    return out


def _split_long(notes: list[Note], ppq: int, tempos) -> list[list[Note]]:
    """Cat chunk qua dai o khoang lang lon nhat ben trong (de quy)."""
    def too_long(ns):
        dur = tick_to_sec(ns[-1].pos + ns[-1].dur, ppq, tempos) - tick_to_sec(ns[0].pos, ppq, tempos)
        return len(ns) > MAX_CHUNK_NOTES or dur > MAX_CHUNK_SEC

    if len(notes) < 2 or not too_long(notes):
        return [notes]
    gaps = [(notes[i + 1].pos - (notes[i].pos + notes[i].dur), i) for i in range(len(notes) - 1)]
    best_gap, cut = max(gaps)
    if best_gap <= 0:            # lien tuc khong nghi -> cat o giua chunk
        cut = len(notes) // 2 - 1
    return _split_long(notes[:cut + 1], ppq, tempos) + _split_long(notes[cut + 1:], ppq, tempos)


def split_chunks(notes: list[Note], ppq: int, tempos) -> list[Chunk]:
    tempos = normalize_tempos(tempos)
    groups, cur = [], []
    for n in notes:
        if cur:
            gap_sec = tick_to_sec(n.pos, ppq, tempos) - tick_to_sec(cur[-1].pos + cur[-1].dur, ppq, tempos)
            if gap_sec >= MIN_GAP_SEC:
                groups.append(cur)
                cur = []
        cur.append(n)
    if cur:
        groups.append(cur)
    final = [g for grp in groups for g in _split_long(grp, ppq, tempos)]
    chunks = []
    for i, g in enumerate(final):
        s_tick, e_tick = g[0].pos, g[-1].pos + g[-1].dur
        chunks.append(Chunk(i, s_tick, e_tick, tick_to_sec(s_tick, ppq, tempos), tick_to_sec(e_tick, ppq, tempos), g))
    return chunks


def chunk_to_ust(chunk: Chunk, ppq: int, tempos, name: str = "chunk") -> bytes:
    """UST (UTF-8, giong dinh dang ustx_track_to_ust) cho 1 chunk; bat dau tu note dau, rest giua note giu nguyen."""
    tempos = normalize_tempos(tempos)
    k = UST_PPQ / ppq
    notes, cursor, prev_tone = [], chunk.start_tick, None     # prev_tone: tone note truoc neu sat nhau (cho snapFirst)
    for n in chunk.notes:
        if n.pos > cursor:
            notes.append(("R", 60, n.pos - cursor, cursor, None, None))
            prev_tone = None
        notes.append((n.lyric, n.tone, n.dur, n.pos, n.pitch, prev_tone))
        prev_tone = n.tone
        cursor = n.pos + n.dur
    first_bpm = tempo_at(tempos, chunk.start_tick)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "chunk"
    out = ["[#VERSION]", "UST Version1.2", "[#SETTING]", f"Tempo={first_bpm:.2f}", "Tracks=1", f"ProjectName={safe}"]
    cur_bpm = first_bpm
    for i, (ly, tone, dur, start, pit, prev) in enumerate(notes):
        out += [f"[#{i:04d}]", f"Length={max(1, int(round(dur * k)))}", f"Lyric={ly}", f"NoteNum={tone}"]
        out += pitchmod.to_ust_lines(pit, tone, prev)
        bpm = tempo_at(tempos, start)
        if abs(bpm - cur_bpm) > 1e-6:
            out.append(f"Tempo={bpm:.2f}")
            cur_bpm = bpm
    out.append("[#TRACKEND]")
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def chunk_hash(ust: bytes, params: dict) -> str:
    h = hashlib.sha1()
    h.update(ust)
    h.update(json.dumps(params or {}, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()[:20]


def plan_track(raw_notes, ppq: int, tempos, params: dict | None = None, name: str = "chunk") -> tuple[list[Chunk], list[bytes]]:
    """Notes tho tu client -> (chunks da co hash, ust cua tung chunk)."""
    ppq = max(1, int(ppq))
    tempos = normalize_tempos(tempos)
    chunks = split_chunks(clean_notes(raw_notes), ppq, tempos)
    usts = []
    for c in chunks:
        ust = chunk_to_ust(c, ppq, tempos, name)
        c.hash = chunk_hash(ust, params or {})
        usts.append(ust)
    return chunks, usts
