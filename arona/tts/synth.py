"""Bot-side orchestration cho `!arona synth` (tach ra tu tts.py).

- `.ust`  -> POST thang len /synth (GPT-SoVITS) nhu cu.
- `.mid`/`.midi` -> moi (track, channel, program) la 1 instrument track (giu velocity/pan/volume/tempo), render bang soundfont, mix lai (khong co vocal).
- `.ustx` (OpenUtau) -> parse project:
    * track vocal      -> doi sang UST roi gui /synth (moi track 1 request),
    * track instrument -> MIDI -> FluidSynth + soundfont (thu muc ./soundfonts), moi track co the dung soundfont/program rieng,
  roi mix tat ca lai (volume/pan/mute/solo cua track duoc ton trong).

Phan loai track (xem classify_track): tag trong ten track `[vocal]` `[inst]` `[drums]` `[gm=25]` `[sf=weeds]` (dau ngoac []/()/{} deu duoc),
neu khong co tag thi doan theo tu khoa trong ten track / ten singer / phonemizer ("piano", "guitar", "strings", "drums", ...);
track co lyric -> vocal; neu khong co lyric thi track khong co singer -> instrument (piano), con lai -> vocal.

Soundfont (./soundfonts, .sf2/.sf3): file ten `<nhac cu>_<nguon>` (piano_fluidr3, guitar_xxx, drums_909, strings_xxx...) = soundfont chuyen cho 1 nhom nhac cu
(xem _SF_FAMILY), cac ten khac = bank GM day du. Chon bang `sf=<ten>` hoac tag `[sf=<ten>]`; track ngoai pham vi font do tu fallback ve font mac dinh.
Cu tha them file vao ./soundfonts la dung duoc ngay (tu quet thu muc).
"""
import asyncio
import io
import os
import math
import re
import struct
import tempfile
import time
import uuid
from dataclasses import dataclass, field

import aiohttp
from pydub import AudioSegment

from config import *
from console import console
from utils.http_session import session_manager

SYNTH_TIMEOUT_S = 3600  # /synth runs TTS per phrase (~5 min for a 4 min song on a GTX 1650); only counts while the job runs, not while queued
synth_lock = asyncio.Lock()

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SOUNDFONT_DIR = os.path.join(ROOT, "soundfonts")
FLUIDSYNTH_EXE = os.path.join(ROOT, "fluidsynth", "bin", "fluidsynth.exe")
if not os.path.exists(FLUIDSYNTH_EXE):
    FLUIDSYNTH_EXE = "fluidsynth"  # PATH (linux/docker)

# Soundfont mac dinh cho instrument: lay file dau tien co trong SOUNDFONT_DIR theo thu tu uu tien (ten khong phan biet hoa thuong, khop 'chua').
SF_PRIORITY = ["generaluser", "musescore_general", "fluidr3_gm", "timgm6mb", "default"]
SF_EXTS = (".sf2", ".sf3")

# Soundfont chuyen cho 1 nhom nhac cu: dat ten `<nhom>_<nguon>.sf2` (vd piano_fluidr3.sf2, guitar_abc.sf2, drums_909.sf2). Tu dau ten file (truoc dau _ - . hoac khoang trang)
# -> cac GM program font do co. Ten khong nam trong bang nay (GeneralUser-GS, WeedsGM3...) = bank GM day du. "drums" = chi co drum kit.
_SF_FAMILY = {
    "piano": range(0, 8), "chromatic": range(8, 16), "mallets": range(8, 16), "organ": range(16, 24), "accordion": range(21, 24),
    "guitar": range(24, 32), "bass": range(32, 40),
    "strings": range(40, 48), "violin": range(40, 42), "cello": range(42, 44), "harp": range(46, 47),
    "ensemble": range(48, 56), "choir": range(52, 55),
    "brass": range(56, 64), "trumpet": range(56, 61), "horn": range(60, 62),
    "reed": range(64, 72), "sax": range(64, 68), "pipe": range(72, 80), "flute": range(72, 80),
    "synth": range(80, 96), "ethnic": range(104, 112), "percussive": range(112, 120), "sfx": range(120, 128),
    "drums": "drums", "drum": "drums",
}

# Tham so gui len server /synth (con lai la tham so rieng cua bot)
SERVER_PARAMS = {"transpose", "auto_octave", "voice_center", "temperature", "top_k", "text_lang"}
DEFAULT_INST_PCT = 50.0  # do to instrument so voi vocal (% RMS; 100 = ngang vocal, 50 = nua = -6 dB) khi ghep chung; doi bang option inst_vol=<%>
FLUID_RATE = 44100
MIX_RATE = 44100

last_synth_info = ""  # mo ta job gan nhat (main.py dung de ghi caption); an toan vi synth_lock chay 1 job/lan


# ===================================================================================================================
#  Goi /synth (GPT-SoVITS)
# ===================================================================================================================
async def _post_synth(body: bytes, content_type: str, params: dict | None = None, timeout_s: int = SYNTH_TIMEOUT_S):
    """POST a UST (raw) or JSON body to the GPT-SoVITS /synth endpoint.
    Returns (wav_bytes, transpose_str, None) on success or (None, None, error_message)."""
    timeout = aiohttp.ClientTimeout(total=timeout_s, sock_read=timeout_s)
    try:
        session = await session_manager.get_session()
        async with session.post(API_URL + "/synth", data=body, params=params or {},
                                headers={"Content-Type": content_type}, timeout=timeout) as response:
            if response.status == 200:
                audio = await response.read()
                _keys = ("Phrases", "Aligned", "Retries", "TotalSec", "Device")
                _st = "  ".join(f"{k}={response.headers.get('X-Synth-' + k)}" for k in _keys)
                console.log(f"Synth generated (Size: {len(audio)}) {_st}")
                return audio, response.headers.get("X-Synth-Transpose"), None
            err = (await response.text())[:500]
            console.log(f"Synth API ERROR:: {response.status} - {err}", "ERROR")
            return None, None, f"{response.status}: {err}"
    except asyncio.TimeoutError:
        return None, None, f"timed out after {timeout_s // 60} minutes"
    except Exception as e:
        console.log(f"Synth connection error: {e}", "ERROR")
        return None, None, f"connection error: {e}"


# ===================================================================================================================
#  USTX
# ===================================================================================================================
@dataclass
class UNote:
    pos: int      # tuyet doi, tick (resolution cua project)
    dur: int
    tone: int
    lyric: str
    vel: int = 96


@dataclass
class UTrack:
    idx: int
    name: str = ""
    singer: str = ""
    phonemizer: str = ""
    notes: list = field(default_factory=list)
    volume_db: float = 0.0
    pan: float = 0.0
    mute: bool = False
    solo: bool = False
    role: str = "vocal"          # "vocal" | "inst"
    program: int = 0             # GM program (instrument)
    drums: bool = False
    sf_hint: str | None = None   # tu tag sf=... trong ten track
    preset: str | None = None    # ten nhac cu that su (tag preset=violin hoac sf=weeds:violin) -> tim trong catalog cua cac soundfont
    fixed: bool = False          # role/program/drums da biet san (track tu file MIDI) -> classify_track khong doan lai


@dataclass
class UProject:
    resolution: int
    tempos: list                 # [(tick, bpm)] tang dan, tick dau = 0
    tracks: list


def parse_midi(body: bytes) -> UProject:
    """File MIDI -> UProject chi co instrument track: moi (track, channel, program) mot UTrack (kenh 10 = drums)."""
    import mido
    mid = mido.MidiFile(file=io.BytesIO(body), clip=True)
    resolution = mid.ticks_per_beat or 480
    tempos: dict[int, float] = {}
    tracks: list[UTrack] = []
    for ti, mtrack in enumerate(mid.tracks):
        tname, abs_t = "", 0
        prog: dict[int, int] = {}
        pan: dict[int, int] = {}
        vol: dict[int, int] = {}
        active: dict = {}      # (ch, note) -> [(start, vel, program)]
        groups: dict = {}      # (ch, program) -> [UNote]

        def _close(ch, note, end):
            lst = active.get((ch, note))
            if lst:
                start, vel, pg = lst.pop(0)
                groups.setdefault((ch, 0 if ch == 9 else pg), []).append(UNote(start, max(1, end - start), note, "", vel))

        for msg in mtrack:
            abs_t += msg.time
            if msg.is_meta:
                if msg.type == "track_name" and not tname:
                    tname = str(msg.name).strip()
                elif msg.type == "set_tempo":
                    tempos[abs_t] = mido.tempo2bpm(msg.tempo)
                continue
            ch = getattr(msg, "channel", None)
            if ch is None:
                continue
            if msg.type == "program_change":
                prog[ch] = msg.program
            elif msg.type == "control_change":
                if msg.control == 10:
                    pan.setdefault(ch, msg.value)
                elif msg.control == 7:
                    vol.setdefault(ch, msg.value)
            elif msg.type == "note_on" and msg.velocity > 0:
                active.setdefault((ch, msg.note), []).append((abs_t, msg.velocity, prog.get(ch, 0)))
            elif msg.type in ("note_off", "note_on"):
                _close(ch, msg.note, abs_t)
        for (ch, note) in list(active):
            while active[(ch, note)]:
                _close(ch, note, abs_t)
        multi = len(groups) > 1
        for (ch, pg), notes in sorted(groups.items()):
            if not notes:
                continue
            base = tname or f"Track{ti + 1}"
            ut = UTrack(idx=len(tracks), name=f"{base} ch{ch + 1}" if multi else base, notes=sorted(notes, key=lambda n: n.pos))
            ut.role, ut.fixed, ut.drums, ut.program = "inst", True, ch == 9, pg
            if ch in pan:
                ut.pan = max(-100.0, min(100.0, (pan[ch] - 64) * 100.0 / 63.0))
            if ch in vol:
                ut.volume_db = max(-24.0, min(6.0, 20 * math.log10(max(vol[ch], 1) / 100.0)))
            tracks.append(ut)
            _classify_tags_only(ut)
    if len(tracks) > 32:
        console.log(f"MIDI: {len(tracks)} track, chi giu 32 track nhieu note nhat", "WARN")
        tracks = sorted(tracks, key=lambda x: -len(x.notes))[:32]
    tl = sorted(tempos.items())
    if not tl:
        tl = [(0, 120.0)]
    if tl[0][0] != 0:
        tl.insert(0, (0, tl[0][1]))
    return UProject(resolution, tl, tracks)


def detect_format(body: bytes) -> str:
    """'midi' neu la file MIDI, 'ustx' neu la project OpenUtau (YAML), nguoc lai 'ust'."""
    if body[:4] == b"MThd":
        return "midi"
    head = body[:8192].decode("utf-8-sig", errors="ignore")
    if re.search(r"^\s*ustx_version\s*:", head, re.M) or re.search(r"^\s*(?:tracks|voice_parts)\s*:", head, re.M):
        return "ustx"
    return "ust"


def parse_ustx(body: bytes) -> UProject:
    import yaml
    text = body.decode("utf-8-sig", errors="replace")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or "tracks" not in doc:
        raise ValueError("khong phai file USTX hop le (thieu 'tracks')")
    resolution = int(doc.get("resolution") or 480)

    tempos = []
    for t in doc.get("tempos") or []:
        try:
            tempos.append((int(t.get("position", 0)), float(t.get("bpm", 120))))
        except Exception:
            continue
    if not tempos:
        tempos = [(0, float(doc.get("bpm") or 120))]
    tempos.sort()
    if tempos[0][0] != 0:
        tempos.insert(0, (0, tempos[0][1]))

    tracks = []
    for i, t in enumerate(doc.get("tracks") or []):
        t = t or {}
        singer = t.get("singer")
        if isinstance(singer, dict):  # vai ban ghi {name: ...}
            singer = singer.get("name", "")
        tracks.append(UTrack(
            idx=i,
            name=str(t.get("track_name") or f"Track{i + 1}"),
            singer=str(singer or ""),
            phonemizer=str(t.get("phonemizer") or ""),
            volume_db=float(t.get("volume") or 0.0),
            pan=float(t.get("pan") or 0.0),
            mute=bool(t.get("mute", False)),
            solo=bool(t.get("solo", False)),
        ))

    for part in doc.get("voice_parts") or []:
        part = part or {}
        ti = int(part.get("track_no", 0))
        if not (0 <= ti < len(tracks)):
            continue
        base = int(part.get("position", 0))
        for n in part.get("notes") or []:
            try:
                dur = int(n["duration"])
                if dur <= 0:
                    continue
                tracks[ti].notes.append(UNote(
                    pos=base + int(n["position"]),
                    dur=dur,
                    tone=int(n["tone"]),
                    lyric=str(n.get("lyric") or ""),
                ))
            except Exception:
                continue
    for t in tracks:
        t.notes.sort(key=lambda x: x.pos)

    if doc.get("wave_parts"):
        console.log(f"USTX: {len(doc['wave_parts'])} wave_part(s) (audio nhung ngoai) bi bo qua", "WARN")
    return UProject(resolution, tempos, tracks)


# ---- phan loai track -----------------------------------------------------------------------------------------------
_TAG_RE = re.compile(r"[\[\(\{]([^\]\)\}]*)[\]\)\}]")


def _parse_tags(name: str) -> dict:
    tags = {}
    for m in _TAG_RE.finditer(name or ""):
        for tok in re.split(r"[\s,;]+", m.group(1).strip()):
            if not tok:
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                tags[k.lower()] = v
            else:
                tags[tok.lower()] = True
    return tags


# (regex, GM program | "drums"): khop dau tien thang. Cu the dat truoc, chung chung dat sau.
_GM_KEYWORDS = [
    (r"drum|\bkit\b|percussion|\bperc\b|snare|\bkick\b|hi-?hat|cymbal|\btoms?\b|taiko", "drums"),
    (r"e\.?\s*piano|electric\s*piano|rhodes|wurli", 4),
    (r"harpsichord", 6), (r"clav", 7), (r"celesta", 8), (r"glock", 9), (r"music\s*box", 10),
    (r"vibraphone|vibes", 11), (r"marimba", 12), (r"xylophone", 13), (r"tubular|chime", 14), (r"dulcimer", 15),
    (r"church\s*organ", 19), (r"organ", 16), (r"accordion", 21), (r"harmonica", 22),
    (r"nylon|classical\s*guitar|ukulele", 24), (r"jazz\s*guitar", 26), (r"clean\s*guitar", 27),
    (r"muted\s*guitar", 28), (r"overdrive", 29), (r"distort|dist\s*guitar", 30),
    (r"e\.?\s*guitar|electric\s*guitar", 27), (r"banjo", 105), (r"shamisen", 106), (r"koto", 107), (r"sitar", 104),
    (r"guitar", 25),
    (r"contrabass", 43), (r"upright|acoustic\s*bass|double\s*bass", 32), (r"synth\s*bass", 38),
    (r"slap", 36), (r"fretless", 35), (r"bass", 33),
    (r"violin", 40), (r"viola", 41), (r"cello", 42), (r"pizz", 45), (r"harp", 46), (r"timpani", 47),
    (r"string|orchestra|ensemble", 48),
    (r"choir|choral|\baah\b|\booh\b", 52),
    (r"muted\s*trumpet", 59), (r"trumpet", 56), (r"trombone", 57), (r"tuba", 58), (r"english\s*horn", 69), (r"horn", 60), (r"brass", 61),
    (r"soprano\s*sax", 64), (r"tenor\s*sax", 66), (r"baritone\s*sax|bari\s*sax", 67), (r"sax", 65),
    (r"oboe", 68), (r"bassoon", 70), (r"clarinet", 71), (r"piccolo", 72),
    (r"flute", 73), (r"recorder", 74), (r"pan\s*flute", 75), (r"shakuhachi", 77), (r"whistle", 78), (r"ocarina", 79),
    (r"square", 80), (r"saw", 81), (r"synth", 80),
    (r"warm\s*pad", 89), (r"halo", 94), (r"sweep", 95), (r"\bpad\b", 88), (r"\bfx\b", 98),
    (r"kalimba", 108), (r"bagpipe", 109), (r"steel\s*drum", 114), (r"wood\s*block", 115),
    (r"grand|piano|keys?\b|klavier", 0),
]
_GM_KEYWORDS = [(re.compile(p, re.I), v) for p, v in _GM_KEYWORDS]

_NO_SINGER = {"", "none", "(none)", "no singer", "<none>", "null", "-", "n/a"}
# Ten track co tu nay -> vocal (truoc khi do tu khoa nhac cu, tranh 'Lead Vocal' bi hieu thanh synth lead)
_VOCAL_WORDS = re.compile(r"vocal|voice|\bvox\b|sing|harmony|backing|\bvo\b|\bbgv\b", re.I)

# lyric -> nhac cu GM drum (tone khong dung cho drums neu lyric la ten tieng)
_DRUM_LYRIC = {
    "kick": 36, "bd": 36, "snare": 38, "sd": 38, "clap": 39, "rim": 37, "hat": 42, "hh": 42, "chh": 42, "ohh": 46,
    "openhat": 46, "tom": 45, "lowtom": 41, "hitom": 50, "crash": 49, "ride": 51, "cowbell": 56, "tamb": 54,
    "tambourine": 54, "shaker": 70, "conga": 63, "bongo": 60,
}


def split_sf_spec(spec) -> tuple[str | None, str | None]:
    """'weeds' -> ('weeds', None); 'weeds:nylon_guitar' -> ('weeds', 'nylon_guitar'); ':violin' -> (None, 'violin')."""
    if not spec:
        return None, None
    font, _, query = str(spec).partition(":")
    return (font.strip() or None), (query.strip() or None)


def _classify_tags_only(t: UTrack) -> None:
    """Track da co role/program (MIDI): chi doc tag sf=... / preset=... trong ten track."""
    tags = _parse_tags(t.name)
    if tags.get("sf") not in (None, True):
        t.sf_hint, t.preset = split_sf_spec(tags["sf"])
    for key in ("preset", "ins"):
        if tags.get(key) not in (None, True):
            t.preset = str(tags[key])
            break


def classify_track(t: UTrack) -> None:
    """Dien t.role / t.program / t.drums / t.sf_hint. Tu khoa chi khop voi TEN TRACK (khong khop ten singer: voicebank nhu
    'Adrien Piano' van la vocal). Track co lyric luon la vocal; neu khong co lyric, singer rong/None -> instrument (piano)."""
    tags = _parse_tags(t.name)
    hay = _TAG_RE.sub(" ", t.name)
    _classify_tags_only(t)
    if t.fixed:
        return

    role = None
    if any(k in tags for k in ("vocal", "voice", "sing", "vo")):
        role = "vocal"
    if any(k in tags for k in ("inst", "instrument", "drum", "drums")) or "gm" in tags or "prog" in tags:
        role = "inst"
    if role is None and t.preset:
        role = "inst"

    program, drums = None, False
    if "drum" in tags or "drums" in tags:
        drums = True
    for key in ("gm", "prog"):
        if key in tags and str(tags[key]).lstrip("-").isdigit():
            program = max(0, min(127, int(tags[key])))
            break
    if role is None and _VOCAL_WORDS.search(hay):
        role = "vocal"
    if program is None and not drums and role != "vocal":
        for rx, val in _GM_KEYWORDS:
            if rx.search(hay):
                if val == "drums":
                    drums = True
                else:
                    program = val
                if role is None:
                    role = "inst"
                break

    if any(note.lyric.strip() for note in t.notes):
        role = "vocal"
    elif role is None:
        role = "inst" if t.singer.strip().lower() in _NO_SINGER else "vocal"
    t.role = role
    t.drums = drums
    t.program = program if program is not None else 0


def _tempo_at(tempos, tick: int) -> float:
    bpm = tempos[0][1]
    for pos, b in tempos:
        if pos <= tick:
            bpm = b
        else:
            break
    return bpm


def ustx_track_to_ust(t: UTrack, proj: UProject) -> bytes:
    """Track vocal -> noi dung UST (UTF-8) cho /synth. Khoang trong giua cac note -> note R; note chong len nhau bi cat bot."""
    k = 480.0 / proj.resolution
    notes, cursor = [], 0
    for n in t.notes:
        start = max(n.pos, cursor)
        end = n.pos + n.dur
        if end <= start:
            continue
        if start > cursor:
            notes.append(("R", 60, start - cursor, cursor))
        notes.append((n.lyric.replace("\n", " ").strip() or "a", max(0, min(127, n.tone)), end - start, start))
        cursor = end
    out = ["[#VERSION]", "UST Version1.2", "[#SETTING]", f"Tempo={proj.tempos[0][1]:.2f}", "Tracks=1",
           f"ProjectName={re.sub(r'[^A-Za-z0-9_-]', '_', t.name) or 'track'}"]
    cur_bpm = proj.tempos[0][1]
    for i, (ly, tone, dur, start) in enumerate(notes):
        out.append(f"[#{i:04d}]")
        out.append(f"Length={max(1, int(round(dur * k)))}")
        out.append(f"Lyric={ly}")
        out.append(f"NoteNum={tone}")
        bpm = _tempo_at(proj.tempos, start)
        if abs(bpm - cur_bpm) > 1e-6:
            out.append(f"Tempo={bpm:.2f}")
            cur_bpm = bpm
    out.append("[#TRACKEND]")
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


# ---- soundfont ------------------------------------------------------------------------------------------------------
def list_soundfonts() -> list[str]:
    """Ten file (khong duoi) cua cac soundfont co trong ./soundfonts."""
    try:
        return sorted(os.path.splitext(f)[0] for f in os.listdir(SOUNDFONT_DIR) if f.lower().endswith(SF_EXTS))
    except FileNotFoundError:
        return []


def _sf_files() -> list[str]:
    try:
        return sorted(f for f in os.listdir(SOUNDFONT_DIR) if f.lower().endswith(SF_EXTS))
    except FileNotFoundError:
        return []


def _sf_family(path: str):
    """Nhom nhac cu cua soundfont theo ten file: range(GM program) | "drums" | None (bank GM day du)."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return _SF_FAMILY.get(re.split(r"[_\-\s.]", stem)[0])


def _is_full_gm(path: str) -> bool:
    name = os.path.basename(path).lower()
    return _sf_family(path) is None and not any(k in name for k in _SF_PROGRAM_MAPS)


def _match_soundfonts(hint: str) -> list[str]:
    """Soundfont khop hint (khong phan biet hoa thuong): trung ten > bat dau bang hint > chua hint; cung hang thi file to (day du) hon dung truoc."""
    h = hint.lower().strip()
    for ext in SF_EXTS:
        if h.endswith(ext):
            h = h[: -len(ext)]
    ranked = []
    for f in _sf_files():
        stem = os.path.splitext(f)[0].lower()
        if h == stem:
            rank = 0
        elif stem.startswith(h):
            rank = 1
        elif h in stem:
            rank = 2
        else:
            continue
        ranked.append((rank, -os.path.getsize(os.path.join(SOUNDFONT_DIR, f)), f))
    return [os.path.join(SOUNDFONT_DIR, f) for _, _, f in sorted(ranked)]


def default_soundfont() -> str | None:
    """Soundfont mac dinh (bank GM day du): theo SF_PRIORITY -> bank GM day du bat ky -> file bat ky."""
    files = _sf_files()
    for want in SF_PRIORITY:
        for f in files:
            if want in f.lower() and _is_full_gm(f):
                return os.path.join(SOUNDFONT_DIR, f)
    for f in files:
        if _is_full_gm(f):
            return os.path.join(SOUNDFONT_DIR, f)
    return os.path.join(SOUNDFONT_DIR, files[0]) if files else None


def resolve_soundfont(hint: str | None = None) -> str | None:
    """hint -> duong dan soundfont khop nhat; khong co hint / khong khop -> soundfont mac dinh."""
    if hint:
        found = _match_soundfonts(hint)
        if found:
            return found[0]
        console.log(f"Soundfont '{hint}' khong co trong {SOUNDFONT_DIR} (co: {list_soundfonts()})", "WARN")
    return default_soundfont()


def describe_soundfonts() -> str:
    """Danh sach gon cho lenh help: bank GM day du va soundfont chuyen theo nhom nhac cu."""
    full, special = [], []
    for f in _sf_files():
        stem = os.path.splitext(f)[0]
        (full if _is_full_gm(f) else special).append(stem)
    parts = []
    if full:
        parts.append("Full GM banks: " + ", ".join(full))
    if special:
        parts.append("Per-instrument: " + ", ".join(special))
    return "\n".join(parts) or "none installed"


# ---- MIDI + FluidSynth ----------------------------------------------------------------------------------------------
# Soundfont khong theo so GM: SSO (Sonatina Symphonic Orchestra) danh so preset rieng -> bang doi GM program -> preset SSO.
# Nhac cu SSO khong co (guitar, bass, synth, sax, drums...) tu dong dung soundfont mac dinh.
_SSO_MAP = {}
for _gm in range(0, 8):
    _SSO_MAP[_gm] = 0          # piano family -> Steinway Concert Grand
_SSO_MAP.update({
    46: 1,                      # harp
    40: 12, 41: 6, 42: 13, 43: 10, 44: 2, 45: 3, 48: 2, 49: 2, 50: 2, 51: 2,   # strings (solo violin/cello, sections)
    52: 35, 53: 35, 54: 35,     # choir
    56: 27, 57: 31, 58: 34, 59: 27, 60: 29, 61: 28, 62: 28, 63: 28,             # brass
    68: 16, 69: 19, 70: 23, 71: 20, 72: 18, 73: 14, 74: 26, 75: 14,              # woodwinds
    47: 40, 9: 39, 13: 42, 14: 43,                                               # timpani, glock, xylophone, chimes
})
_SF_PROGRAM_MAPS = {"sonatina": _SSO_MAP, "sso": _SSO_MAP}   # khop 'chua' trong ten file (vd orchestra_sso.sf2)


def _sf_plan(sf: str, t: UTrack):
    """-> (ok, program): soundfont `sf` co nhac cu cua track `t` khong, va program can dung (SSO co bang doi rieng)."""
    name = os.path.basename(sf).lower()
    for key, pmap in _SF_PROGRAM_MAPS.items():
        if key in name:
            prog = pmap.get(t.program)
            return (not t.drums and prog is not None), (t.program if prog is None else prog)
    fam = _sf_family(sf)
    if fam == "drums":
        return t.drums, t.program
    if fam is not None:
        return (not t.drums and t.program in fam), t.program
    return True, t.program


# ---- catalog nhac cu (preset) trong tung soundfont ---------------------------------------------------------------------
@dataclass
class Pick:
    sf: str
    bank: int
    program: int
    name: str = ""
    explicit: bool = False   # chon theo ten (preset=...) -> drum kit cung duoc chon


_preset_cache: dict = {}     # path -> (mtime, {(bank, program): ten})


def _read_phdr(path: str) -> dict:
    """Doc bang preset (phdr) cua .sf2/.sf3 -> {(bank, program): ten}. Chi doc header, bo qua sample nen file vai tram MB van nhanh."""
    out: dict = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return out
            f.seek(12)
            while True:
                head = f.read(8)
                if len(head) < 8:
                    break
                cid, size = head[:4], struct.unpack("<I", head[4:])[0]
                if cid != b"LIST":
                    f.seek(size + (size & 1), 1)
                    continue
                if f.read(4) != b"pdta":
                    f.seek(size - 4 + (size & 1), 1)
                    continue
                end = f.tell() + size - 4
                while f.tell() + 8 <= end:
                    sh = f.read(8)
                    sid, ssz = sh[:4], struct.unpack("<I", sh[4:])[0]
                    if sid == b"phdr":
                        data = f.read(ssz)
                        for k in range(0, len(data) - 38, 38):    # record cuoi la EOP
                            name = data[k:k + 20].split(b"\0")[0].decode("latin1").strip()
                            prog, bank = struct.unpack("<HH", data[k + 20:k + 24])
                            out.setdefault((bank, prog), name)
                        return out
                    f.seek(ssz + (ssz & 1), 1)
                break
    except Exception as e:
        console.log(f"Khong doc duoc preset cua {os.path.basename(path)}: {e}", "WARN")
    return out


def _presets(path: str) -> dict:
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    c = _preset_cache.get(path)
    if not c or c[0] != mt:
        c = (mt, _read_phdr(path))
        _preset_cache[path] = c
    return c[1]


def _font_order() -> list[str]:
    """Thu tu uu tien khi tim nhac cu: SF_PRIORITY -> bank GM day du khac -> font chuyen nhom / dac biet."""
    def key(p):
        n = os.path.basename(p).lower()
        for i, w in enumerate(SF_PRIORITY):
            if w in n and _is_full_gm(p):
                return (0, i, n)
        return (1 if _is_full_gm(p) else 2, 0, n)
    return sorted((os.path.join(SOUNDFONT_DIR, f) for f in _sf_files()), key=key)


def _tokens(text: str) -> list[str]:
    return [x for x in re.split(r"[\s_+\-./,]+", (text or "").lower()) if x]


def search_presets(query: str, font_hint: str | None = None, drums: bool | None = False) -> list[tuple]:
    """Tim nhac cu theo ten (moi tu trong query phai co trong ten preset) trong tat ca soundfont (hoac font khop font_hint).
    drums: False = chi nhac cu thuong, True = chi drum kit (bank 128), None = ca hai.
    -> [(rank, font_idx, len_ten, bank, program, path, ten)] da sap xep: ten trung > bat dau bang > chua, roi font uu tien, roi ten ngan."""
    toks = _tokens(query)
    if not toks:
        return []
    qn = " ".join(toks)
    fonts = _match_soundfonts(font_hint) if font_hint else _font_order()
    res = []
    for i, path in enumerate(fonts):
        for (bank, prog), name in _presets(path).items():
            if drums is not None and (bank == 128) != drums:
                continue
            nl = " ".join(_tokens(name))
            if not all(tk in nl for tk in toks):
                continue
            res.append((0 if nl == qn else 1 if nl.startswith(qn) else 2, i, len(name), bank, prog, path, name))
    res.sort()
    return res


def preset_search_text(spec: str, max_chars: int = 1800) -> str:
    """Van ban cho lenh `!arona synth list [font:]<ten nhac cu>` (Discord, < 2000 ky tu)."""
    spec = (spec or "").strip()
    font_hint, query = split_sf_spec(spec) if ":" in spec else (None, spec or None)
    if not query:
        lines = ["**Instruments per soundfont** (search: `!arona synth list <name>`, e.g. `list violin`, `list weeds:nylon guitar`):"]
        for p in _font_order():
            idx = _presets(p)
            kits = sum(1 for b, _ in idx if b == 128)
            lines.append(f"`{os.path.splitext(os.path.basename(p))[0]}`: {len(idx) - kits} instruments" + (f" + {kits} drum kits" if kits else ""))
        return "\n".join(lines)[:max_chars]
    res = search_presets(query, font_hint, None)
    if not res:
        return f'No instrument matching "{query}"' + (f" in `{font_hint}`" if font_hint else "") + ". Try a shorter word (e.g. `guitar`, `violin`, `choir`)."
    by_font: dict = {}
    for _, _, _, bank, prog, path, name in res:
        by_font.setdefault(path, []).append((bank, prog, name))
    out = [f'**{len(res)} matches for "{query}"** — use `[preset={_tokens(query)[0]}]` / `[sf=<font>:<name_with_underscores>]` in a track name:']
    used = len(out[0])
    shown = 0
    for path, items in by_font.items():
        parts = []
        for bank, prog, name in items[:6]:
            parts.append(f"kit {name} ({prog})" if bank == 128 else (f"{name} ({prog})" if bank == 0 else f"{name} (b{bank}:{prog})"))
        extra = f" +{len(items) - 6} more" if len(items) > 6 else ""
        line = f"`{os.path.splitext(os.path.basename(path))[0]}`: " + ", ".join(parts) + extra
        if used + len(line) + 1 > max_chars:
            out.append(f"... and {len(by_font) - shown} more soundfonts (narrow your search)")
            break
        out.append(line)
        used += len(line) + 1
        shown += 1
    return "\n".join(out)


def _has_preset(path: str, bank: int, prog: int) -> bool:
    if bank == 128:
        return _sf_family(path) == "drums" or any(b == 128 for b, _ in _presets(path))
    return (bank, prog) in _presets(path)


def pick_soundfont(t: UTrack, font_hint: str | None = None, query: str | None = None) -> Pick | None:
    """Chon nhac cu that su cho track:
    1. co ten nhac cu (preset=... / sf=font:ten) -> tim preset theo ten trong catalog (khong quan tam bank/font, tru khi co font_hint);
    2. khong thi GM program cua track o font_hint (neu font do co nhac cu do), roi lan luot cac bank GM day du theo SF_PRIORITY (bo font nao thieu preset)."""
    if query:
        found = search_presets(query, font_hint, bool(t.drums))
        if found:
            _, _, _, bank, prog, path, name = found[0]
            return Pick(path, bank, prog, name, True)
        console.log(f"Khong tim thay nhac cu '{query}'" + (f" trong '{font_hint}'" if font_hint else "") + f" cho track '{t.name}' -> dung GM program {t.program}", "WARN")
    bank = 128 if t.drums else 0
    cands = _match_soundfonts(font_hint) if font_hint else []
    if font_hint and not cands:
        console.log(f"Soundfont '{font_hint}' khong co trong {SOUNDFONT_DIR} (co: {list_soundfonts()})", "WARN")
    chain = cands + [p for p in _font_order() if _is_full_gm(p) and p not in cands]
    for sf in chain:
        ok, prog = _sf_plan(sf, t)
        if ok and _has_preset(sf, bank, prog):
            if cands and sf not in cands:
                console.log(f"Soundfont '{font_hint}' khong co nhac cu cho track '{t.name}' -> dung soundfont mac dinh", "WARN")
            return Pick(sf, bank, prog, "" if t.drums else _presets(sf).get((bank, prog), ""))
    default = default_soundfont()
    return Pick(default, bank, t.program) if default else None


def build_midi(t: UTrack, proj: UProject, program: int | None = None, bank: int = 0) -> bytes:
    import mido
    ch = 9 if t.drums else 0
    events = []   # (tick, order, msg)  order: 0 = off/meta, 1 = on
    for n in t.notes:
        tone = max(0, min(127, n.tone))
        if t.drums:
            tone = _DRUM_LYRIC.get(re.sub(r"[^a-z]", "", n.lyric.lower()), tone)
        events.append((n.pos, 1, mido.Message("note_on", channel=ch, note=tone, velocity=max(1, min(127, n.vel)))))
        events.append((n.pos + n.dur, 0, mido.Message("note_off", channel=ch, note=tone, velocity=0)))
    for pos, bpm in proj.tempos:
        events.append((pos, -1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))))
    events.sort(key=lambda e: (e[0], e[1]))

    track = mido.MidiTrack()
    prog = t.program if program is None else program
    if not t.drums:
        if bank:
            track.append(mido.Message("control_change", channel=ch, control=0, value=min(127, bank), time=0))
        track.append(mido.Message("program_change", channel=ch, program=prog, time=0))
    elif program is not None:   # chon drum kit (kenh 10 tu dung bank 128)
        track.append(mido.Message("program_change", channel=ch, program=prog, time=0))
    track.append(mido.Message("control_change", channel=ch, control=7, value=110, time=0))
    pan = max(-100.0, min(100.0, t.pan))
    track.append(mido.Message("control_change", channel=ch, control=10, value=int(round(64 + pan * 63 / 100.0)), time=0))
    last = 0
    for tick, _, msg in events:
        msg.time = max(0, tick - last)
        track.append(msg)
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=proj.resolution * 2))  # duoi nhac
    mid = mido.MidiFile(type=0, ticks_per_beat=proj.resolution)
    mid.tracks.append(track)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


async def render_instrument(t: UTrack, proj: UProject, sf_path: str, program: int | None = None, bank: int = 0) -> AudioSegment | None:
    mid_bytes = await asyncio.to_thread(build_midi, t, proj, program, bank)
    uid = uuid.uuid4().hex
    tmp = tempfile.gettempdir()
    mid_path, wav_path = os.path.join(tmp, f"synth_{uid}.mid"), os.path.join(tmp, f"synth_{uid}.wav")
    try:
        with open(mid_path, "wb") as f:
            f.write(mid_bytes)
        proc = await asyncio.create_subprocess_exec(
            FLUIDSYNTH_EXE, "-ni", "-g", "0.8", "-r", str(FLUID_RATE), "-F", wav_path, sf_path, mid_path,
            cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            console.log(f"FluidSynth timeout (track '{t.name}')", "ERROR")
            return None
        if proc.returncode != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) < 100:
            console.log(f"FluidSynth loi (track '{t.name}', rc={proc.returncode}): {(err or b'').decode(errors='replace')[-400:]}", "ERROR")
            return None
        return await asyncio.to_thread(AudioSegment.from_wav, wav_path)
    except FileNotFoundError:
        console.log("Khong tim thay fluidsynth (fluidsynth/bin/fluidsynth.exe)", "ERROR")
        return None
    finally:
        for p in (mid_path, wav_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ---- mix ------------------------------------------------------------------------------------------------------------
def _prep(seg: AudioSegment, gain_db: float, pan: float) -> AudioSegment:
    seg = seg.set_frame_rate(MIX_RATE).set_channels(2)
    if gain_db:
        seg = seg.apply_gain(gain_db)
    if pan:
        seg = seg.pan(max(-1.0, min(1.0, pan / 100.0)))
    return seg


def _inst_params(params) -> tuple[float, float]:
    """-> (inst_vol %, inst_db). inst_vol: do to instrument so voi vocal (100 = ngang vocal, mac dinh 50); inst_db: chinh them theo dB."""
    params = params or {}
    pct, db = DEFAULT_INST_PCT, 0.0
    try:
        v = str(params.get("inst_vol", "")).strip().rstrip("%")
        if v:
            pct = max(0.0, min(500.0, float(v)))
    except ValueError:
        pass
    try:
        if params.get("inst_db") not in (None, ""):
            db = float(params["inst_db"])
    except ValueError:
        pass
    return pct, db


def _overlay_all(layers: list[AudioSegment]) -> AudioSegment:
    base = AudioSegment.silent(duration=max(len(s) for s in layers), frame_rate=MIX_RATE).set_channels(2)
    for s in layers:
        base = base.overlay(s)
    return base


def _balance_inst(vocal_layers: list[AudioSegment], inst_layers: list[AudioSegment], pct: float, extra_db: float = 0.0) -> list[AudioSegment]:
    """Chinh gain instrument de do to (RMS) = pct% do to vocal (100% = ngang, 50% = -6 dB), cong them extra_db.
    Khong co vocal -> chi ap extra_db (cuoi cung _mix se normalize). pct = 0 -> bo instrument."""
    if not inst_layers or pct <= 0:
        return []
    gain = extra_db
    if vocal_layers:
        v, i = _overlay_all(vocal_layers).dBFS, _overlay_all(inst_layers).dBFS
        if v > -90 and i > -90:
            gain += v + 20 * math.log10(pct / 100.0) - i
    gain = max(-40.0, min(40.0, gain))
    return [s.apply_gain(gain) for s in inst_layers]


def _mix(layers: list[AudioSegment]) -> bytes:
    from pydub import effects
    total = max(len(s) for s in layers)
    base = AudioSegment.silent(duration=total, frame_rate=MIX_RATE).set_channels(2)
    head = -(6.0 + 3.0 * math.log2(max(1, len(layers))))   # chua headroom truoc khi cong, tranh clip; normalize lai o cuoi
    for s in layers:
        base = base.overlay(s.apply_gain(head))
    base = effects.normalize(base, headroom=1.0)
    buf = io.BytesIO()
    base.export(buf, format="wav")
    return buf.getvalue()


def _lang_from_phonemizer(ph: str) -> str | None:
    p = ph.lower()
    if re.search(r"english|arpasing|\ben\b", p):
        return "en"
    if re.search(r"japanese|\bja\b|jp|kana|romaji", p):
        return "ja"
    if re.search(r"cantonese|\byue\b", p):
        return "yue"
    if re.search(r"chinese|mandarin|\bzh\b", p):
        return "zh"
    if re.search(r"korean|\bko\b", p):
        return "ko"
    return None


async def _synth_ustx(body: bytes, params: dict, timeout_s: int):
    return await _synth_project(parse_ustx(body), params, timeout_s)


async def _synth_midi(body: bytes, params: dict, timeout_s: int, raw: bool = False):
    return await _synth_project(await asyncio.to_thread(parse_midi, body), params, timeout_s, raw)


async def _synth_project(proj: UProject, params: dict, timeout_s: int, raw: bool = False):
    """raw=True -> tra ve (list AudioSegment da chinh gain/pan, ...) chua mix/normalize de ghep voi file khac."""
    global last_synth_info
    skip_inst = str(params.get("inst", "")).lower() in ("0", "false", "no", "off")
    only_vocals_off = str(params.get("vocals", "")).lower() in ("0", "false", "no", "off")
    inst_pct, inst_db = _inst_params(params)
    font_opt, preset_opt = split_sf_spec(params.get("sf"))
    server_params = {k: v for k, v in params.items() if k in SERVER_PARAMS}

    for t in proj.tracks:
        classify_track(t)
    active = [t for t in proj.tracks if t.notes and not (skip_inst and t.role == "inst")]
    if any(t.solo for t in active):
        active = [t for t in active if t.solo]
    active = [t for t in active if not t.mute]
    if not active:
        return None, None, "khong co track nao co note (hoac tat ca bi mute)"

    layers: list[AudioSegment] = []       # vocal
    inst_layers: list[AudioSegment] = []  # instrument (can bang do to so voi vocal sau khi vocal gen xong)
    desc: list[str] = []
    first_transpose = None

    # --- instrument (nhanh) ---
    inst_tracks = [t for t in active if t.role == "inst"]
    sem = asyncio.Semaphore(2)

    async def _do_inst(t: UTrack):
        pk = pick_soundfont(t, t.sf_hint or font_opt, t.preset or preset_opt)
        if not pk:
            return t, None, None
        async with sem:
            seg = await render_instrument(t, proj, pk.sf, pk.program if (pk.explicit or not t.drums) else None, pk.bank)
        return t, seg, pk

    inst_results = await asyncio.gather(*[_do_inst(t) for t in inst_tracks]) if inst_tracks else []
    warn = []
    for t, seg, pk in inst_results:
        sf = pk.sf if pk else None
        label = (pk.name if pk else "") or ("drums" if t.drums else f"GM{pk.program if pk else t.program}")
        if seg is None:
            warn.append(t.name)
            console.log(f"USTX: instrument track '{t.name}' khong render duoc (soundfont={sf})", "WARN")
            continue
        inst_layers.append(_prep(seg, t.volume_db, 0))  # pan cua instrument da nam trong MIDI (CC10)
        desc.append(f"{t.name}→{label}/{os.path.basename(sf)}")

    # --- vocal (tuan tu: GPU/CPU TTS) ---
    vocal_tracks = [] if only_vocals_off else [t for t in active if t.role == "vocal"]
    for t in vocal_tracks:
        p = dict(server_params)
        if "text_lang" not in p:
            lang = _lang_from_phonemizer(t.phonemizer)
            if lang:
                p["text_lang"] = lang
        ust = ustx_track_to_ust(t, proj)
        console.log(f"USTX: vocal track '{t.name}' ({len(t.notes)} notes, lang={p.get('text_lang', 'default')}) -> /synth", "INFO")
        audio, transpose, err = await _post_synth(ust, "application/octet-stream", p, timeout_s)
        if not audio:
            return None, None, f"vocal track '{t.name}': {err}"
        if first_transpose is None:
            first_transpose = transpose
        seg = await asyncio.to_thread(AudioSegment.from_file, io.BytesIO(audio), "wav")
        layers.append(_prep(seg, t.volume_db, t.pan))
        desc.append(f"{t.name}→vocal")

    if not layers and not inst_layers:
        return None, None, "khong render duoc track nao" + (f" (instrument loi: {', '.join(warn)})" if warn else "")
    last_synth_info = ("; ".join(desc) + (f" | failed: {', '.join(warn)}" if warn else ""))[:600]
    if raw:
        return layers + inst_layers, first_transpose, None
    layers = layers + _balance_inst(layers, inst_layers, inst_pct, inst_db)
    if not layers:
        return None, None, "instrument volume = 0 va khong co vocal de ghep"
    wav = await asyncio.to_thread(_mix, layers)
    return wav, first_transpose, None


# ===================================================================================================================
#  Entry point (main.py goi ham nay)
# ===================================================================================================================
async def synth_song(body: bytes, content_type: str, params: dict | None = None, timeout_s: int = SYNTH_TIMEOUT_S):
    """UST (raw) -> /synth nhu cu; USTX -> vocal qua /synth + instrument qua soundfont, mix lai.
    Returns (wav_bytes, transpose_str, None) on success or (None, None, error_message)."""
    global last_synth_info
    last_synth_info = ""
    params = dict(params or {})
    fmt = detect_format(body)
    if fmt in ("ustx", "midi"):
        t0 = time.time()
        try:
            res = await (_synth_ustx if fmt == "ustx" else _synth_midi)(body, params, timeout_s)
        except Exception as e:
            console.log(f"{fmt.upper()} synth error: {e}", "ERROR")
            return None, None, f"{fmt.upper()} error: {e}"
        console.log(f"{fmt.upper()} synth done in {time.time() - t0:.1f}s: {last_synth_info}", "INFO")
        return res
    return await _post_synth(body, content_type, {k: v for k, v in params.items() if k in SERVER_PARAMS}, timeout_s)


async def synth_multiple_tracks(
    tracks: list[tuple[str, bytes, str]],
    params: dict | None = None,
    timeout_s: int = SYNTH_TIMEOUT_S,
):
    """Synthesize separate UST/USTX files and mix their audio, aligned at the start."""
    global last_synth_info
    layers = []
    inst_layers = []
    track_info = []
    midi_jobs = []   # file MIDI: render sau khi vocal (ust/ustx) gen xong, roi ghep vao cung mot lan mix
    for name, body, content_type in tracks:
        if detect_format(body) == "midi":
            midi_jobs.append((name, body))
            continue
        audio, transpose, err = await synth_song(body, content_type, params, timeout_s)
        if not audio:
            return None, None, f"track '{name}': {err}"
        try:
            segment = await asyncio.to_thread(AudioSegment.from_file, io.BytesIO(audio), "wav")
        except Exception as e:
            console.log(f"Failed to decode synth audio for '{name}': {e}", "ERROR")
            return None, None, f"could not decode audio for '{name}': {e}"
        layers.append(segment)
        details = [name]
        if transpose not in (None, "", "0"):
            try:
                details.append(f"transposed {int(transpose):+d} semitones")
            except (TypeError, ValueError):
                details.append(f"transpose {transpose}")
        if last_synth_info:
            details.append(last_synth_info)
        track_info.append(": ".join(details))

    skip_inst = str((params or {}).get("inst", "")).lower() in ("0", "false", "no", "off")
    for name, body in ([] if skip_inst else midi_jobs):
        try:
            segs, _, err = await _synth_midi(body, dict(params or {}), timeout_s, raw=True)
        except Exception as e:
            console.log(f"MIDI synth error for '{name}': {e}", "ERROR")
            return None, None, f"track '{name}': MIDI error: {e}"
        if not segs:
            return None, None, f"track '{name}': {err}"
        inst_layers.extend(segs)
        track_info.append(f"{name}: {last_synth_info}" if last_synth_info else name)

    if not layers and not inst_layers:
        return None, None, "no input tracks to synthesize"
    inst_pct, inst_db = _inst_params(params)
    layers = layers + _balance_inst(layers, inst_layers, inst_pct, inst_db)
    if not layers:
        return None, None, "instrument volume is 0 and there is no vocal to mix"
    try:
        mixed = await asyncio.to_thread(_mix, layers)
    except Exception as e:
        console.log(f"Failed to mix uploaded synth tracks: {e}", "ERROR")
        return None, None, f"could not mix tracks: {e}"
    last_synth_info = "; ".join(track_info)[:600]
    return mixed, None, None
