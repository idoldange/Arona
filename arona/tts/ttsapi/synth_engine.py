"""
UTAU-style note renderer used by /synth in api_v2.py.

Kept in its own module so it can't interfere with the /tts code path.
Everything here is pure DSP (WORLD vocoder via pyworld) - no TTS model access.

Implemented UTAU concepts:
  - pitch (notenum) + modulation (0 = flat/monotone, 100 = keep natural contour)
  - consonant velocity (consonant stretched independently of the vowel)
  - preutterance (consonant is placed BEFORE the note's beat so the vowel lands on it)
  - overlap (crossfade between the previous note's tail and this note's head)
  - flags: g (gender/formant), B (breathiness), t (pitch shift in cents)
  - intensity (per-note volume), R / empty lyric = rest
"""
import os
import re
import hashlib
import numpy as np
import pyworld as pw

FRAME_MS = 5.0


# ---------------------------------------------------------------- clip cleaning
def _voiced_runs(voiced, max_gap=2):
    runs, start, last, gap = [], None, None, 0
    for i, v in enumerate(voiced):
        if v:
            if start is None:
                start = i
            last, gap = i, 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                runs.append((start, last))
                start, gap = None, 0
    if start is not None:
        runs.append((start, last))
    return runs


def clean_clip(audio, sr, max_cons_ms=100.0, tail_ms=30.0, min_voiced_ms=80.0, max_breathiness=0.82,
              merge_repeats=False):
    """
    GPT-SoVITS on a single mora sometimes emits long breath/exhale noise before or
    after the syllable (or instead of it) - and sometimes the "voiced" run pyworld
    finds IS the exhale: a breathy aspiration can carry a weak, noisy pitch that dio
    still tracks as f0>0, which is exactly how a consonant like "g" comes out sounding
    like a plain "h". So on top of requiring a long-enough voiced run, we also check
    that run's low-band aperiodicity (from d4c) - a real vowel is harmonic/tonal there
    (low aperiodicity), breath is noise-like (aperiodicity near 1). Returns
    (clip, ok, voiced_ms); ok False means "re-roll", either too short or too breathy.

    merge_repeats=True: the text sent to TTS was the mora repeated several times (to get
    natural sustain material for a long note) - keep the WHOLE span from the first onset
    to the last offset instead of just the single longest voiced run, so the brief dips
    between repeated attacks don't get treated as "end of the syllable" and truncate
    everything after the first repeat.
    """
    x = np.ascontiguousarray(audio.astype(np.float64))
    try:
        f0, t = pw.dio(x, sr, frame_period=FRAME_MS)
        f0 = pw.stonemask(x, f0, t, sr)
        ap = pw.d4c(x, f0, t, sr)
    except Exception:
        return audio, False, 0.0
    runs = _voiced_runs(f0 > 0)
    if not runs:
        return audio, False, 0.0
    if merge_repeats:
        s, e = runs[0][0], runs[-1][1]
    else:
        s, e = max(runs, key=lambda r: r[1] - r[0])
    voiced_ms = (e - s + 1) * FRAME_MS
    low_band = ap[s:e + 1, :max(1, ap.shape[1] // 4)]
    breathiness = float(np.mean(low_band)) if low_band.size else 1.0
    ok = voiced_ms >= min_voiced_ms and breathiness <= max_breathiness

    # Energy-gated lead-in/tail: a real consonant burst sits RIGHT against the vowel
    # with no gap; a separate breath (inhale before speaking, exhale after) usually has
    # at least a brief quieter patch between it and the actual syllable. Walk outward
    # from the voiced run and stop as soon as we hit a couple of consecutive quiet
    # frames, instead of always grabbing the full max_cons_ms/tail_ms window - that
    # fixed window is what was pulling in a "ha"-like breath sitting just outside it.
    hop = int(round(sr * FRAME_MS / 1000.0))
    n_frames = len(f0)
    rms = np.array([
        float(np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2))) if (i + 1) * hop <= len(x) else 0.0
        for i in range(n_frames)
    ])
    peak_v = rms[s:e + 1].max() if e >= s else 1e-6
    gate = max(1e-6, peak_v * 0.12)          # tail: giu nguyen nhu cu
    # LEAD-IN: phu am (s/h/k/t...) chi bang ~ -20..-30dB so voi nguyen am. Gate cu 0.12 (-18dB)
    # lam vong lap dung ngay sau 2 frame -> mat 1/2 phu am (vd 'ha' onset 110ms -> 45ms).
    # Gate moi 0.03 (-30dB) va cho phep den 3 frame yen lang lien tiep (dong am cua t/k)
    # truoc khi coi la 'khoang lang giua hoi tho va am tiet'.
    lead_gate = max(1e-6, peak_v * 0.06)   # 0.03 la qua thap -> lot ca tieng tho; 0.12 la qua cao -> mat phu am

    a0 = s
    max_lead = int(max_cons_ms / FRAME_MS)
    quiet = 0
    for i in range(s - 1, max(-1, s - 1 - max_lead) - 1, -1):
        if rms[i] < lead_gate:
            quiet += 1
            if quiet >= 2:
                break
        else:
            quiet = 0
        a0 = i

    a1 = e
    max_tail = int(tail_ms / FRAME_MS)
    quiet = 0
    for i in range(e + 1, min(n_frames, e + 1 + max_tail)):
        if rms[i] < gate:
            quiet += 1
            if quiet >= 2:
                break
        else:
            quiet = 0
        a1 = i

    i0 = int(a0 * FRAME_MS / 1000.0 * sr)
    i1 = int((a1 + 1) * FRAME_MS / 1000.0 * sr)
    return audio[i0:i1].copy(), ok, float(voiced_ms)


def consonant_score(clip, sr):
    """0..2 - clip co phu am dau ro khong (do dai lead-in truoc nguyen am + nang luong cua no so voi nguyen am).
    Dung de chon giua cac take (plain vs 'っ'+mora) cua cung 1 mora."""
    try:
        x = np.ascontiguousarray(clip.astype(np.float64))
        f0, t = pw.dio(x, sr, frame_period=FRAME_MS)
        f0 = pw.stonemask(x, f0, t, sr)
        runs = _voiced_runs(f0 > 0)
        if not runs:
            return 0.0
        s, e = max(runs, key=lambda r: r[1] - r[0])
        hop = int(round(sr * FRAME_MS / 1000.0))
        vow = float(np.sqrt(np.mean(x[s * hop:(e + 1) * hop] ** 2))) + 1e-9
        lead = x[max(0, s - int(120.0 / FRAME_MS)) * hop:s * hop]
        if len(lead) < hop * 4:
            return 0.0
        lead_ms = len(lead) / sr * 1000.0
        db = 20.0 * np.log10(float(np.sqrt(np.mean(lead ** 2))) / vow + 1e-9)
        return float(min(lead_ms, 100.0) / 100.0 + np.clip((db + 30.0) / 25.0, 0.0, 1.0))
    except Exception:
        return 0.0


_WHISPER = None


def _get_whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "asr", "models",
                            "faster-whisper-large-v3-turbo")
        _WHISPER = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=max(4, (os.cpu_count() or 8)))
    return _WHISPER


def _kana_norm(s):
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:  # katakana -> hiragana
            ch = chr(o - 0x60)
        if ch in "ー、。,.!?！？ 　~〜…\"'「」":
            continue
        out.append(ch)
    return "".join(out)


def verify_mora(audio, sr, lyric):
    """True if Japanese ASR hears the target mora in the clip (False on any failure)."""
    try:
        import librosa
        w = _get_whisper()
        pad = np.zeros(int(sr * 0.4), dtype=np.float32)
        a = np.concatenate([pad, audio.astype(np.float32), pad])
        a16 = librosa.resample(a, orig_sr=sr, target_sr=16000)
        segs, _ = w.transcribe(a16, language="ja", beam_size=1, temperature=0.0,
                               without_timestamps=True, max_new_tokens=12,
                               condition_on_previous_text=False, vad_filter=False)
        text = _kana_norm("".join(s.text for s in segs))
    except Exception:
        return False
    target = _kana_norm(lyric)
    return target != "" and target in text


def make_pitched_ref(ref_path, notenum, out_dir):
    """Reference clip shifted (F0 only, formants kept) so its median pitch = notenum."""
    import soundfile as sf
    os.makedirs(out_dir, exist_ok=True)
    key = hashlib.sha1(f"{ref_path}|{int(notenum)}|{os.path.getmtime(ref_path)}".encode()).hexdigest()[:16]
    out = os.path.abspath(os.path.join(out_dir, f"ref_{key}.wav"))
    if os.path.exists(out):
        return out
    y, sr = sf.read(ref_path, dtype="float64")
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = np.ascontiguousarray(y)
    f0, t = pw.dio(y, sr, frame_period=FRAME_MS)
    f0 = pw.stonemask(y, f0, t, sr)
    sp = pw.cheaptrick(y, f0, t, sr)
    ap = pw.d4c(y, f0, t, sr)
    voiced = f0 > 0
    if not voiced.any():
        return ref_path
    base = float(np.exp(np.median(np.log(f0[voiced]))))
    target = 440.0 * (2.0 ** ((int(notenum) - 69) / 12.0))
    f0n = np.where(voiced, f0 * (target / base), 0.0)
    y2 = pw.synthesize(f0n, sp, ap, sr, FRAME_MS)
    peak = float(np.abs(y2).max())
    if peak > 0:
        y2 = y2 / peak * 0.9
    sf.write(out, y2.astype(np.float32), sr)
    return out


# ---------------------------------------------------------------- flags
def parse_flags(s):
    out = {}
    if not s:
        return out
    for m in re.finditer(r"([A-Za-z])(-?\d+(?:\.\d+)?)", str(s)):
        out[m.group(1)] = float(m.group(2))
    return out


def _decode_text(data):
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _num(v, default=None):
    try:
        return float(str(v).strip())
    except Exception:
        return default


def parse_ust(data):
    """
    Parse a UTAU .ust file (bytes or str; utf-8 or Shift-JIS) into the same note
    dicts /synth takes as JSON. Length is converted from ticks (480 = 1 quarter)
    to ms using the current tempo (a note-level Tempo= persists to later notes).
    Returns (notes, first_tempo).
    """
    text = _decode_text(data)
    sections, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = {"name": m.group(1).strip(), "kv": {}}
            sections.append(cur)
            continue
        if cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur["kv"][k.strip()] = v.strip()

    tempo = 120.0
    for s in sections:
        if s["name"].upper() == "#SETTING":
            tempo = _num(s["kv"].get("Tempo"), 120.0) or 120.0
    first_tempo = tempo

    notes = []
    for s in sections:
        if not re.fullmatch(r"#\d+", s["name"]):
            continue  # skips #SETTING/#PREV/#NEXT/#INSERT/#DELETE/#TRACKEND
        kv = s["kv"]
        t = _num(kv.get("Tempo"))
        if t and t > 0:
            tempo = t
        ticks = _num(kv.get("Length"), 480.0)
        lyric = kv.get("Lyric", "").strip()
        if " " in lyric:  # VCV/CVVC alias like "a あ" -> keep the current mora
            lyric = lyric.split()[-1]
        # alias chi la 1-2 chu latin ("t", "k", "-") = phu am cuoi/ngat hoi cua VCV bank, KHONG phai mora.
        # Dua vao TTS thi ra rac/tieng tho -> coi la nghi (giu nguyen do dai).
        if lyric.upper() != "R" and re.fullmatch(r"[A-Za-z\-\u2010-\u2015]{1,3}", lyric):
            lyric = "R"
        note = {"lyric": lyric, "length": round(ticks / 480.0 * 60000.0 / tempo, 3), "tempo": tempo}
        nn = _num(kv.get("NoteNum"))
        if nn is not None:
            note["notenum"] = int(nn)
        for src, dst in (("Velocity", "velocity"), ("Intensity", "intensity"),
                         ("PreUtterance", "preutterance"), ("VoiceOverlap", "overlap")):
            v = _num(kv.get(src))
            if v is not None:
                note[dst] = v
        mod = _num(kv.get("Moduration"), _num(kv.get("Modulation")))  # UTAU really writes "Moduration"
        if mod is not None:
            note["modulation"] = mod
        if kv.get("Flags"):
            note["flags"] = kv["Flags"]
        # pitch bend / vibrato cua UTAU (giu nguyen chuoi, parse o pitch_cents_curve)
        for src, dst in (("PBS", "pbs"), ("PBW", "pbw"), ("PBY", "pby"), ("PBM", "pbm"), ("VBR", "vbr")):
            if kv.get(src):
                note[dst] = kv[src]
        notes.append(note)

    if not notes:
        raise ValueError("no notes found in ust")
    return notes, first_tempo


# ---------------------------------------------------------------- resize helpers
def _resize_2d(arr, new_len):
    old = arr.shape[0]
    if old == new_len or new_len <= 0 or old == 0:
        return arr if new_len > 0 else arr[:0]
    pos = np.linspace(0, old - 1, new_len)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, old - 1)
    frac = (pos - i0)[:, None]
    return arr[i0] * (1.0 - frac) + arr[i1] * frac


def _resize_f0(f0, new_len):
    # interpolate in log domain over voiced frames only, so voiced/unvoiced
    # boundaries don't produce fake pitch glides through 0 Hz
    old = len(f0)
    if new_len <= 0:
        return f0[:0]
    if old == new_len:
        return f0
    voiced = f0 > 0
    if old == 0 or not voiced.any():
        return np.zeros(new_len)
    idx = np.arange(old)
    lf0 = np.log(np.where(voiced, f0, 1.0))
    lf0 = np.interp(idx, idx[voiced], lf0[voiced])
    x_old = np.linspace(0, 1, old)
    x_new = np.linspace(0, 1, new_len)
    lf0n = np.interp(x_new, x_old, lf0)
    vn = voiced[np.round(np.linspace(0, old - 1, new_len)).astype(int)]
    return np.where(vn, np.exp(lf0n), 0.0)


def _warp_envelope(sp, k):
    # new(f) = sp(f * k): k > 1 moves formants down (deeper), k < 1 moves them up
    K = sp.shape[1]
    src = np.clip(np.arange(K) * k, 0, K - 1)
    i0 = np.floor(src).astype(int)
    i1 = np.minimum(i0 + 1, K - 1)
    frac = (src - i0)[None, :]
    return sp[:, i0] * (1.0 - frac) + sp[:, i1] * frac


def _detect_consonant_frames(f0, n):
    # consonant = everything before the vowel becomes stably voiced. If the clip
    # is voiced from frame 0 (vowel-only or voiced consonant like ma/ra) we
    # fall back to a short fixed onset so velocity still has something to act on.
    voiced = f0 > 0
    if n < 6:
        return 0
    if not voiced.any():
        return int(n * 0.4)
    run = 3
    first = None
    for i in range(0, n - run + 1):
        if voiced[i:i + run].all():
            first = i
            break
    if first is None:
        first = int(np.argmax(voiced))
    c = first if first >= 2 else min(int(30.0 / FRAME_MS), int(n * 0.25))
    return int(min(c, n * 0.5, int(120.0 / FRAME_MS)))


# ---------------------------------------------------------------- pitch curve (UTAU PBS/PBW/PBY + VBR)
def _floats(s):
    out = []
    for p in str(s or "").split(","):
        p = p.strip()
        try:
            out.append(float(p))
        except ValueError:
            out.append(0.0 if p == "" else 0.0)
    return out


def pitch_cents_curve(t_ms, pbs=None, pbw=None, pby=None, pbm=None, vbr=None, length_ms=None, humanize=1.0, seed=0):
    """
    Offset (cents) so voi notenum, danh gia tai cac moc t_ms (ms tinh tu BEAT cua note, am = phan preutterance).
      - PBS=x;y  diem dau (ms, don vi 10 cent) - y != 0 chinh la glide tu cao do note truoc
      - PBW      do rong cac doan (ms), PBY = y cua cac diem tiep theo (10 cent), diem cuoi = 0
      - PBM      kieu noi: '' = cosine (s-curve), s = thang, r = ease-in, j = ease-out
      - VBR      len%,chu ky ms,bien do cent,fade-in%,fade-out%,pha%,offset%,height% (nam o cuoi note)
    + humanize: jitter cham (~+-4 cent) cho bot 'robot' khi pitch phang.
    """
    t = np.asarray(t_ms, dtype=np.float64)
    cents = np.zeros_like(t)
    if pbw:
        x0, y0 = 0.0, 0.0
        if pbs:
            parts = str(pbs).split(";")
            try:
                x0 = float(parts[0])
            except ValueError:
                x0 = 0.0
            if len(parts) > 1:
                try:
                    y0 = float(parts[1])
                except ValueError:
                    y0 = 0.0
        ws, ys = _floats(pbw), _floats(pby)
        modes = [m.strip() for m in str(pbm or "").split(",")]
        xs, yv = [x0], [y0]
        for i, w in enumerate(ws):
            xs.append(xs[-1] + w)
            yv.append(ys[i] if i < len(ys) else 0.0)
        xs, yv = np.array(xs), np.array(yv)
        idx = np.clip(np.searchsorted(xs, t, side="right") - 1, 0, len(xs) - 2) if len(xs) > 1 else np.zeros(len(t), int)
        if len(xs) > 1:
            seg_w = np.maximum(xs[idx + 1] - xs[idx], 1e-6)
            u = np.clip((t - xs[idx]) / seg_w, 0.0, 1.0)
            mode = np.array([modes[i] if i < len(modes) else "" for i in idx])
            e = 0.5 - 0.5 * np.cos(np.pi * u)              # '' cosine
            e = np.where(mode == "s", u, e)
            e = np.where(mode == "r", u * u, e)
            e = np.where(mode == "j", 1.0 - (1.0 - u) ** 2, e)
            c = yv[idx] + (yv[idx + 1] - yv[idx]) * e
            c = np.where(t < xs[0], yv[0], c)
            c = np.where(t >= xs[-1], yv[-1], c)
            cents = c * 10.0                                 # PBY don vi = 10 cent
    if vbr and length_ms:
        v = _floats(vbr)
        v += [0.0] * (8 - len(v))
        pct, period, depth, fin, fout, phase = v[0], v[1], v[2], v[3], v[4], v[5]
        if pct > 0 and period > 0 and depth > 0:
            vlen = length_ms * min(pct, 100.0) / 100.0
            vs = length_ms - vlen
            u = (t - vs) / max(vlen, 1e-6)
            env = np.clip(np.minimum(u / max(fin / 100.0, 1e-3), (1.0 - u) / max(fout / 100.0, 1e-3)), 0.0, 1.0)
            env = np.where((u < 0) | (u > 1), 0.0, env)
            cents = cents + np.minimum(depth, 80.0) * env * np.sin(2 * np.pi * ((t - vs) / period + phase / 100.0))
    if humanize > 0 and len(t) > 4:
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        n = rng.standard_normal(len(t))
        k = np.ones(9) / 9.0                                # ~45ms smoothing -> cham, khong ro thanh 'run'
        n = np.convolve(n, k, mode="same")
        cents = cents + humanize * 4.0 * n / max(float(np.std(n)), 1e-6)
    return cents


# ---------------------------------------------------------------- per-note render
def render_note(audio, sr, notenum=None, length_ms=None, velocity=100.0,
                modulation=0.0, flags="", intensity=100.0, clarity=1.0, pitch_natural=0.35,
                pitch_bend=None, humanize=1.0):
    """
    Returns (audio_float32, consonant_samples).
    `length_ms` is the duration of the VOWEL part (= the note's beat length, UTAU
    style); the consonant is added in front of it and becomes the preutterance.
    clarity 0..1+: less noise in voiced frames, sharper formants, louder consonants.
    pitch_natural 0..1: how much of the syllable's natural pitch contour is kept even
    when modulation=0 (a perfectly flat F0 sounds buzzy/robotic).
    """
    if len(audio) == 0:
        return audio, 0
    x = np.ascontiguousarray(audio.astype(np.float64))
    try:
        f0, t = pw.harvest(x, sr, f0_floor=70.0, f0_ceil=900.0, frame_period=FRAME_MS)
        sp = pw.cheaptrick(x, f0, t, sr)
        ap = pw.d4c(x, f0, t, sr)
    except Exception:
        return audio, 0

    n = len(f0)
    fl = parse_flags(flags)
    voiced = f0 > 0

    # ---- pitch: flatten to the syllable's own median, then move to the target note
    if notenum is not None and voiced.any():
        base = float(np.exp(np.median(np.log(f0[voiced]))))
        target = 440.0 * (2.0 ** ((notenum - 69) / 12.0))
        if "t" in fl:
            target *= 2.0 ** (fl["t"] / 1200.0)
        mod = float(np.clip(max(modulation / 100.0, pitch_natural), 0.0, 2.0))
        f0 = np.where(voiced, target * (f0 / base) ** mod, 0.0)

    # ---- clarity: cleaner voiced frames + crisper formants (before flags so B/g stack on top)
    if clarity > 0:
        vm = (f0 > 0)[:, None]
        # cu: ap*(1-0.5c) va sp**(1+0.15c) -> giong bi 'sach' qua muc = buzz/robot + thô. Giam manh.
        ap = np.where(vm, ap * (1.0 - 0.2 * min(clarity, 1.5)), ap)
        e0 = sp.sum(axis=1, keepdims=True)
        sp = sp ** (1.0 + 0.06 * clarity)
        sp = sp * (e0 / np.maximum(sp.sum(axis=1, keepdims=True), 1e-300))

    # ---- flags on the spectral side
    if "g" in fl and fl["g"] != 0:
        sp = _warp_envelope(sp, 2.0 ** (fl["g"] / 200.0))
    if "B" in fl:
        b = float(np.clip((fl["B"] - 50.0) / 50.0, -1.0, 1.0))
        if b > 0:
            ap = ap + (1.0 - ap) * b
        elif b < 0:
            ap = ap * (1.0 + b)
        ap = np.clip(ap, 0.0, 1.0)

    # ---- consonant velocity + vowel duration
    c0 = _detect_consonant_frames(f0, n)
    if n - c0 < 2:
        c0 = max(0, n - 2)
    # trailing unvoiced release (exhale/breath after the vowel): keep it short and
    # never stretch it - stretching it is what turned a short note into a long sigh
    v_idx = np.where(f0[c0:] > 0)[0]
    v_end = c0 + (int(v_idx[-1]) + 1 if len(v_idx) else n - c0)
    tail_frames = min(n - v_end, int(20.0 / FRAME_MS))
    core_end = v_end
    vowel_target = int(round(length_ms / FRAME_MS)) if length_ms else (core_end - c0)
    vowel_target = max(2, vowel_target)

    # ---- do dai: phu am va do "noi" cua nguyen am phai rat hop le
    # 1) phu am an het phan nguyen am. T tren beat 115ms, 1 ti on 'ku' bi do lai 90ms ->
    #    chi con 25ms nguyen am -> mat tiet. Gioi han phu am ~40% cua do dai beat.
    _ccap = max(1, int(round(vowel_target * 0.4)))
    if c0 > _ccap:
        c0 = _ccap
    # 2) khong "noi" nguyen am qua 1.6 lan. Clip 'ri' chi con 110ms nguyen am cho beat
    #    346ms -> nen 3.14 lan; duong F0 cung bi nen 3.14 lan nen rung nhanh, nghe nhu
    #    'meo mo han'. De note thu ngan (va de lech) con sang hon la bi eo thanh tieng.
    _avail = max(1, core_end - c0)
    if vowel_target > _avail * 1.6:
        vowel_target = max(2, int(round(_avail * 1.6)))

    def _stretch_core(core, target, rz):
        # Squashing a whole vowel evenly (long natural take -> short note target, or the
        # reverse) blurs exactly the onset/release transitions that make a syllable
        # recognizable, which is what made dense passages come out unclear/mumbled.
        # Keep the attack and release close to their natural length either way, and
        # only stretch or squeeze the steady middle of the vowel.
        L = len(core)
        if L < 8:
            return rz(core, target)
        on = min(int(L * 0.3), 12)
        tl = min(int(L * 0.2), 8)
        mid = core[on:L - tl]
        if target >= L:
            if len(mid) < 1 or target - on - tl < 1:
                return rz(core, target)
            return np.concatenate([core[:on], rz(mid, target - on - tl), core[L - tl:]])
        # shortening: shrink attack/release a little too, but keep them proportionally
        # much longer than the middle so the transition survives
        on_t = max(1, min(on, int(round(target * 0.35))))
        tl_t = max(1, min(tl, int(round(target * 0.25))))
        mid_t = target - on_t - tl_t
        if mid_t < 1 or len(mid) < 1:
            return rz(core, target)
        return np.concatenate([rz(core[:on], on_t), rz(mid, mid_t), rz(core[L - tl:], tl_t)])


    def _splice(parts, blend=3):
        # Plain concatenation of independently-resized WORLD parameter segments can leave
        # a frame-to-frame jump at each seam (F0/envelope from one segment not matching
        # the next) - WORLD's synthesizer assumes smooth frame evolution, so a jump there
        # comes out as an audible click/tsk. Linearly cross-fade a couple of frames at
        # each internal seam instead of a hard cut.
        parts = [p for p in parts if len(p) > 0]
        if not parts:
            return parts[0] if parts else np.zeros(0)
        out = parts[0]
        for p in parts[1:]:
            b = min(blend, len(out), len(p))
            if b <= 0:
                out = np.concatenate([out, p])
                continue
            w = np.linspace(0.0, 1.0, b)
            shape = (b,) + (1,) * (out.ndim - 1)
            w = w.reshape(shape)
            seam = out[-b:] * (1.0 - w) + p[:b] * w
            out = np.concatenate([out[:-b], seam, p[b:]])
        return out

    def _build(arr, rz):
        parts = []
        if c0 > 0:
            parts.append(rz(arr[:c0], max(1, int(round(c0 * factor)))))
        parts.append(_stretch_core(arr[c0:core_end], vowel_target, rz))
        if tail_frames > 0:
            parts.append(arr[core_end:core_end + tail_frames])
        return _splice(parts)

    factor = 2.0 ** (1.0 - float(velocity) / 100.0)
    new_c = max(1, int(round(c0 * factor))) if c0 > 0 else 0
    f0 = _build(f0, _resize_f0)
    sp = _build(sp, _resize_2d)
    ap = _build(ap, _resize_2d)

    # ---- pitch bend (glide tu note truoc), vibrato, jitter: cong tren cao do note, chi khung voiced.
    # Thoi gian tinh tu BEAT: frame k <-> (k - new_c)*FRAME_MS (phan consonant nam o phia am).
    if notenum is not None and (pitch_bend or humanize > 0):
        pb = pitch_bend or {}
        tt = (np.arange(len(f0)) - new_c) * FRAME_MS
        cents = pitch_cents_curve(tt, pb.get("pbs"), pb.get("pbw"), pb.get("pby"), pb.get("pbm"), pb.get("vbr"),
                                  length_ms=length_ms, humanize=humanize, seed=int(notenum) * 131 + len(f0))
        f0 = np.where(f0 > 0, f0 * 2.0 ** (cents / 1200.0), 0.0)

    try:
        y = pw.synthesize(np.ascontiguousarray(f0.astype(np.float64)),
                          np.ascontiguousarray(sp.astype(np.float64)),
                          np.ascontiguousarray(ap.astype(np.float64)),
                          sr, FRAME_MS)
    except Exception:
        return audio, 0

    cons_samples = int(new_c * FRAME_MS / 1000.0 * sr)
    if clarity > 0 and 0 < cons_samples < len(y):
        # phu am vua du: dua ti le RMS(phu am)/RMS(nguyen am) ve ~ -15dB (0.18), chi ha/nang nhe
        _vow = y[cons_samples:]
        _vr = float(np.sqrt(np.mean(_vow ** 2))) + 1e-9
        _cr = float(np.sqrt(np.mean(y[:cons_samples] ** 2))) + 1e-9
        boost = float(np.clip(0.18 * _vr / _cr, 0.5, 1.25))
        g = np.ones(len(y), dtype=np.float32)
        r_in = min(cons_samples // 2, int(0.005 * sr))
        r_out = min(cons_samples, int(0.015 * sr))
        g[:r_in] = np.linspace(1.0, boost, r_in, dtype=np.float32) if r_in > 0 else boost
        g[r_in:cons_samples - r_out] = boost
        g[cons_samples - r_out:cons_samples] = np.linspace(boost, 1.0, r_out, dtype=np.float32)
        y = y * g
    # chuan hoa do to: moi am tiet TTS ra to/nho khac nhau -> dua RMS phan nguyen am ve 1 muc chung
    # (truoc khi ap intensity cua UST)
    _v = y[cons_samples:] if 0 < cons_samples < len(y) else y
    if len(_v):
        _pk = float(np.abs(_v).max())
        _act = _v[np.abs(_v) > 0.1 * _pk] if _pk > 0 else _v
        _rms = float(np.sqrt(np.mean(_act ** 2))) if len(_act) else 0.0
        if _rms > 1e-6:
            # trần 4.0 de lai am tiet nhat 14dB so voi nhom (do la tieng 'ha' tho) - phai
            # dua ve cung mot muc chung, du phai de note ngan lai.
            y = y * float(np.clip(0.10 / _rms, 0.25, 10.0))
    gain = float(np.clip(intensity / 100.0, 0.0, 2.0))
    y = (y * gain).astype(np.float32)
    return y, cons_samples


# ---------------------------------------------------------------- timeline mix
def mix_timeline(items, sr, gap_ms=20.0, default_overlap_ms=12.0, edge_fade_ms=5.0):
    """
    items: list of dicts
      {"kind": "rest", "length_ms": float}
      {"kind": "note", "audio": np.ndarray, "cons": int(samples), "length_ms": float,
       "overlap_ms": float|None, "pre_ms": float|None}
    Note starts advance by length_ms (the beat). Each note's audio begins
    `preutterance` earlier than its beat so the vowel lands exactly on it.
    """
    edge = max(1, int(sr * edge_fade_ms / 1000.0))
    gap = max(0, int(sr * gap_ms / 1000.0))

    # 1) beat positions
    pos = 0
    for it in items:
        it["start"] = pos
        natural = len(it["audio"]) - it["cons"] if it["kind"] == "note" else 0
        ms = it.get("length_ms")
        pos += int(sr * ms / 1000.0) if ms else max(natural, 0)

    notes = [it for it in items if it["kind"] == "note"]
    if not notes:
        return np.zeros(0, dtype=np.float32)

    # 2) audio start = beat - preutterance
    for it in notes:
        pre = int(sr * it["pre_ms"] / 1000.0) if it.get("pre_ms") is not None else it["cons"]
        it["pre"] = max(0, pre)
        it["astart"] = it["start"] - it["pre"]
    lead = max(0, -min(it["astart"] for it in notes))
    for it in notes:
        it["astart"] += lead

    # 3) trim tails / apply fades / mix
    total = max(it["astart"] + len(it["audio"]) for it in notes) + edge
    out = np.zeros(total, dtype=np.float32)

    for idx, it in enumerate(items):
        if it["kind"] != "note":
            continue
        a = it["audio"].astype(np.float32).copy()
        prev_is_note = idx > 0 and items[idx - 1]["kind"] == "note"
        nxt = items[idx + 1] if idx + 1 < len(items) else None
        next_is_note = nxt is not None and nxt["kind"] == "note"

        ov_ms = it.get("overlap_ms")
        ov = int(sr * (ov_ms if ov_ms is not None else default_overlap_ms) / 1000.0)
        ov = max(0, min(ov, it["pre"] if it["pre"] > 0 else ov))

        # tail
        fade_out = edge
        if next_is_note:
            n_ov_ms = nxt.get("overlap_ms")
            n_ov = int(sr * (n_ov_ms if n_ov_ms is not None else default_overlap_ms) / 1000.0)
            n_ov = max(0, min(n_ov, nxt["pre"] if nxt["pre"] > 0 else n_ov))
            own_end = it["astart"] + len(a)
            end = min(own_end - gap, nxt["astart"] + n_ov)
            keep = max(end - it["astart"], edge * 2)
            a = a[:keep]
            fade_out = max(edge, n_ov)

        # fades
        fi = edge  # keep the consonant's burst intact; the previous note's tail does the fading
        fi = min(fi, len(a) // 2)
        fo = min(fade_out, len(a) // 2)
        if fi > 0:
            a[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
        if fo > 0:
            a[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)

        out[it["astart"]:it["astart"] + len(a)] += a

    return out
