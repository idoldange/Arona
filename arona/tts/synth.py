"""Bot-side orchestration cho `!arona synth` (tach ra tu tts.py).

- `.ust`  -> POST thang len /synth (GPT-SoVITS) nhu cu.
- `.ustx` (OpenUtau) -> parse project:
    * track vocal      -> doi sang UST roi gui /synth (moi track 1 request),
    * track instrument -> MIDI -> FluidSynth + soundfont (thu muc ./soundfonts), moi track co the dung soundfont/program rieng,
  roi mix tat ca lai (volume/pan/mute/solo cua track duoc ton trong).

Phan loai track (xem classify_track): tag trong ten track `[vocal]` `[inst]` `[drums]` `[gm=25]` `[sf=weeds]` (dau ngoac []/()/{} deu duoc),
neu khong co tag thi doan theo tu khoa trong ten track / ten singer / phonemizer ("piano", "guitar", "strings", "drums", ...);
track khong co singer -> instrument (piano); con lai -> vocal.
"""
import asyncio
import io
import os
import re
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

# Tham so gui len server /synth (con lai la tham so rieng cua bot)
SERVER_PARAMS = {"transpose", "auto_octave", "voice_center", "temperature", "top_k", "text_lang"}
DEFAULT_INST_DB = -3.0   # instrument nho hon vocal mot chut khi mix
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


@dataclass
class UProject:
    resolution: int
    tempos: list                 # [(tick, bpm)] tang dan, tick dau = 0
    tracks: list


def detect_format(body: bytes) -> str:
    """'ustx' neu la project OpenUtau (YAML), nguoc lai 'ust'."""
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
                    lyric=str(n.get("lyric") if n.get("lyric") not in (None, "") else "a"),
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


def classify_track(t: UTrack) -> None:
    """Dien t.role / t.program / t.drums / t.sf_hint. Tu khoa chi khop voi TEN TRACK (khong khop ten singer: voicebank nhu
    'Adrien Piano' van la vocal). Singer rong/None -> instrument (piano)."""
    tags = _parse_tags(t.name)
    hay = _TAG_RE.sub(" ", t.name)
    t.sf_hint = str(tags["sf"]) if tags.get("sf") not in (None, True) else None

    role = None
    if any(k in tags for k in ("vocal", "voice", "sing", "vo")):
        role = "vocal"
    if any(k in tags for k in ("inst", "instrument", "drum", "drums")) or "gm" in tags or "prog" in tags:
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

    if role is None:
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


def resolve_soundfont(hint: str | None = None) -> str | None:
    """hint (khop 'chua', khong phan biet hoa thuong) -> duong dan; khong co hint / khong khop -> theo SF_PRIORITY -> file bat ky."""
    try:
        files = sorted(f for f in os.listdir(SOUNDFONT_DIR) if f.lower().endswith(SF_EXTS))
    except FileNotFoundError:
        return None
    if hint:
        h = hint.lower()
        for f in files:
            if h in f.lower():
                return os.path.join(SOUNDFONT_DIR, f)
        console.log(f"Soundfont '{hint}' khong co trong {SOUNDFONT_DIR} (co: {list_soundfonts()})", "WARN")
    for want in SF_PRIORITY:
        for f in files:
            if want in f.lower():
                return os.path.join(SOUNDFONT_DIR, f)
    return os.path.join(SOUNDFONT_DIR, files[0]) if files else None


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
_SF_SPECIAL = {
    "sonatina": {"map": _SSO_MAP},
    "sso": {"map": _SSO_MAP},
    "909_drum": {"drums_only": True},
}


def pick_soundfont(t: UTrack, hint: str | None):
    """-> (sf_path, program). Soundfont dac biet (SSO, drum-only) tu doi program / fallback ve soundfont mac dinh."""
    sf = resolve_soundfont(hint)
    if not sf:
        return None, t.program
    name = os.path.basename(sf).lower()
    for key, spec in _SF_SPECIAL.items():
        if key not in name:
            continue
        ok, prog = True, t.program
        if spec.get("drums_only") and not t.drums:
            ok = False
        if "map" in spec:
            prog = spec["map"].get(t.program)
            if t.drums or prog is None:
                ok = False
        if ok:
            return sf, prog
        console.log(f"Soundfont {os.path.basename(sf)} khong co nhac cu cho track '{t.name}' -> dung soundfont mac dinh", "WARN")
        return resolve_soundfont(None), t.program
    return sf, t.program


def build_midi(t: UTrack, proj: UProject, program: int | None = None) -> bytes:
    import mido
    ch = 9 if t.drums else 0
    events = []   # (tick, order, msg)  order: 0 = off/meta, 1 = on
    for n in t.notes:
        tone = max(0, min(127, n.tone))
        if t.drums:
            tone = _DRUM_LYRIC.get(re.sub(r"[^a-z]", "", n.lyric.lower()), tone)
        events.append((n.pos, 1, mido.Message("note_on", channel=ch, note=tone, velocity=96)))
        events.append((n.pos + n.dur, 0, mido.Message("note_off", channel=ch, note=tone, velocity=0)))
    for pos, bpm in proj.tempos:
        events.append((pos, -1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))))
    events.sort(key=lambda e: (e[0], e[1]))

    track = mido.MidiTrack()
    if not t.drums:
        track.append(mido.Message("program_change", channel=ch, program=t.program if program is None else program, time=0))
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


async def render_instrument(t: UTrack, proj: UProject, sf_path: str, program: int | None = None) -> AudioSegment | None:
    mid_bytes = await asyncio.to_thread(build_midi, t, proj, program)
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


def _mix(layers: list[AudioSegment]) -> bytes:
    from pydub import effects
    total = max(len(s) for s in layers)
    base = AudioSegment.silent(duration=total, frame_rate=MIX_RATE).set_channels(2)
    for s in layers:
        base = base.overlay(s)
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
    global last_synth_info
    proj = parse_ustx(body)
    skip_inst = str(params.get("inst", "")).lower() in ("0", "false", "no", "off")
    only_vocals_off = str(params.get("vocals", "")).lower() in ("0", "false", "no", "off")
    inst_db = DEFAULT_INST_DB
    try:
        if params.get("inst_db") not in (None, ""):
            inst_db = float(params["inst_db"])
    except ValueError:
        pass
    sf_opt = params.get("sf") or None
    server_params = {k: v for k, v in params.items() if k in SERVER_PARAMS}

    for t in proj.tracks:
        classify_track(t)
    active = [t for t in proj.tracks if t.notes and not (skip_inst and t.role == "inst")]
    if any(t.solo for t in active):
        active = [t for t in active if t.solo]
    active = [t for t in active if not t.mute]
    if not active:
        return None, None, "USTX khong co track nao co note (hoac tat ca bi mute)"

    layers: list[AudioSegment] = []
    desc: list[str] = []
    first_transpose = None

    # --- instrument (nhanh) ---
    inst_tracks = [t for t in active if t.role == "inst"]
    sem = asyncio.Semaphore(2)

    async def _do_inst(t: UTrack):
        sf, prog = pick_soundfont(t, t.sf_hint or sf_opt)
        if not sf:
            return t, None, None, prog
        async with sem:
            seg = await render_instrument(t, proj, sf, prog)
        return t, seg, sf, prog

    inst_results = await asyncio.gather(*[_do_inst(t) for t in inst_tracks]) if inst_tracks else []
    warn = []
    for t, seg, sf, prog in inst_results:
        label = "drums" if t.drums else (f"GM{prog}" if prog == t.program else f"preset{prog}")
        if seg is None:
            warn.append(t.name)
            console.log(f"USTX: instrument track '{t.name}' khong render duoc (soundfont={sf})", "WARN")
            continue
        layers.append(_prep(seg, t.volume_db + inst_db, 0))  # pan cua instrument da nam trong MIDI (CC10)
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

    if not layers:
        return None, None, "khong render duoc track nao" + (f" (instrument loi: {', '.join(warn)})" if warn else "")
    wav = await asyncio.to_thread(_mix, layers)
    last_synth_info = ("; ".join(desc) + (f" | failed: {', '.join(warn)}" if warn else ""))[:600]
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
    if detect_format(body) == "ustx":
        t0 = time.time()
        try:
            res = await _synth_ustx(body, params, timeout_s)
        except Exception as e:
            console.log(f"USTX synth error: {e}", "ERROR")
            return None, None, f"USTX error: {e}"
        console.log(f"USTX synth done in {time.time() - t0:.1f}s: {last_synth_info}", "INFO")
        return res
    return await _post_synth(body, content_type, {k: v for k, v in params.items() if k in SERVER_PARAMS}, timeout_s)
