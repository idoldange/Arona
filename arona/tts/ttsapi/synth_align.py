"""
synth_align.py - can chinh am tiet <-> note cho /synth (nhanh phrase cua NotAh trong api_v2.py).

Van de cu: TTS ca cau xong KEO GIAN DEU toan bo audio cho bang tong do dai note. Ranh gioi am tiet
that trong audio khong bao gio trung ranh gioi note -> am tiet nam sai note, pitch (gan theo vi tri
frame) lech theo, phu am bi keo gian ra nghe rach/re.

Cach moi:
  1. segment()/split_units(): tim K am tiet trong audio TTS (DP tren cac "thung lung" nang luong +
      prior do dai mora deu), moi am tiet tach thanh phu am (giu nguyen do dai) + nguyen am.
  2. render_phrase(): dat NGUYEN AM len dung beat cua note, phu am nam TRUOC beat (preutterance, an
      vao duoi note truoc), keo/nen chi phan giua nguyen am (attack/release giu nguyen), pitch = cau
      thang note lam muot theo semitone, roi WORLD synthesize.
  3. count_units(): dem so mora thuc su co trong audio. DP o (1) luon chia du K doan nen khong tu
      bao loi "TTS chi tao ra 4 am tiet ma note lai 5" - caller canh am tieu thi phai kiem tra
      so nay truoc (khong khop thi nen dung duong keo gian deu).
Toan bo don vi thoi gian la frame 10ms (FP).
"""
import numpy as np
import pyworld as pw
import librosa
from scipy.ndimage import gaussian_filter1d

FP = 10.0
CONS_CAP = 18      # frame (180ms) - phu am dai toi da
MIN_SYL = 4        # frame - am tiet ngan nhat DP cho phep


# ------------------------------------------------------------------ note -> nhom am tiet
def build_groups(phrase_notes, fp=FP):
    """
    phrase_notes: [{"lyric": str, "notenum": int|None, "ms": float}, ...] (khong chua rest).
    Note co lyric rong sau khi bo '+'/'-' la noi tiep am tiet truoc (melisma) -> cung 1 nhom.
    Tra ve (groups, note_edges). Bien note tinh bang frame, lam tron TICH LUY (khong troi do dai).
    """
    edges = [0]
    acc = 0.0
    for n in phrase_notes:
        acc += float(n["ms"])
        edges.append(max(int(round(acc / fp)), edges[-1] + 1))
    groups = []
    for i, n in enumerate(phrase_notes):
        text = str(n["lyric"]).replace("+", "").replace("-", "").strip()
        if text or not groups:
            groups.append({"text": text, "notes": [i], "start": edges[i], "end": edges[i + 1]})
        else:
            groups[-1]["notes"].append(i)
            groups[-1]["end"] = edges[i + 1]
    return groups, edges


# ------------------------------------------------------------------ phan tich audio
def _energy_db(x, sr, n, hop):
    rms = librosa.feature.rms(y=x.astype(np.float32), frame_length=hop * 3, hop_length=hop, center=True)[0]
    if len(rms) < n:
        rms = np.concatenate([rms, np.full(n - len(rms), rms[-1] if len(rms) else 1e-5)])
    rms = rms[:n].astype(np.float64)
    return gaussian_filter1d(20.0 * np.log10(rms + 1e-5), 1.0)


def _valley_depth(e, W=10):
    # do sau thung lung: min(dinh ben trai, dinh ben phai trong W frame) - e[t]
    n = len(e)
    d = np.zeros(n)
    for t in range(n):
        lm = e[max(0, t - W):t + 1].max()
        rm = e[t:min(n, t + W + 1)].max()
        d[t] = min(lm, rm) - e[t]
    return d


def _dp_bounds(e, L, R, K, lam=1.2, gamma=0.8):
    """
    Chia [L, R) thanh K doan: thuong cho ranh gioi nam o thung lung nang luong, phat do dai lech
    so voi mora trung binh (log-ratio^2). Tra ve [L, b1, ..., b_{K-1}, R] hoac None.
    """
    span = R - L
    mu = span / float(K)
    dmin = max(MIN_SYL, int(0.4 * mu))
    dmax = max(dmin + 1, int(3.0 * mu))
    n = len(e)
    B = np.zeros(R + 1)
    depth = np.clip(_valley_depth(e) / 10.0, 0.0, 1.5)
    m = min(n, R + 1)
    B[:m] = depth[:m]
    INF = 1e18
    cost = np.full((K + 1, R + 1), INF)
    back = np.zeros((K + 1, R + 1), dtype=np.int32)
    cost[0, L] = 0.0
    for k in range(1, K + 1):
        ck, bk, prev = cost[k], back[k], cost[k - 1]
        for d in range(dmin, dmax + 1):
            lo = L + d
            if lo > R:
                break
            cand = prev[lo - d:R + 1 - d] + lam * np.log(d / mu) ** 2
            seg = ck[lo:R + 1]
            better = cand < seg
            if better.any():
                seg[better] = cand[better]
                bseg = bk[lo:R + 1]
                bseg[better] = d
        if k < K:
            ck -= gamma * B
    if cost[K, R] >= INF / 2:
        return None
    bounds = [R]
    t = R
    for k in range(K, 0, -1):
        t -= int(back[k, t])
        bounds.append(t)
    bounds = bounds[::-1]
    if bounds[0] != L:
        return None
    return bounds


def _snap_start(e, lo, m):
    # ranh gioi DP nam o giua thung lung -> lui ve luc nguyen am truoc roi xuong (= dau phu am)
    if m <= lo:
        return m
    pk = e[lo:m + 1].max()
    thr = pk - 6.0
    if e[m] >= thr:
        return m
    j = m
    while j - 1 > lo and e[j - 1] < thr:
        j -= 1
    return j


def _vowel_onset(e, voiced, s, t):
    # frame dau tien co huu thanh + nang luong gan dinh cua am tiet = bat dau nguyen am
    if t - s < 2:
        return s
    pk = e[s:t].max()
    thr = pk - 7.0
    nv = len(voiced)
    for i in range(s, t - 1):
        if e[i] >= thr and voiced[i] and voiced[min(i + 1, nv - 1)]:
            return i
    return s


def segment(x, sr, f0, K, fp=FP):
    """Tra ve (starts, vstarts, end, used_dp): K moc bat dau phu am, K moc bat dau nguyen am, frame cuoi."""
    n = len(f0)
    hop = int(round(sr * fp / 1000.0))
    voiced = f0 > 1.0
    e = _energy_db(x, sr, n, hop)
    act = np.where(e > e.max() - 42.0)[0]
    if len(act) == 0:
        L, R = 0, n
    else:
        L, R = int(act[0]), int(act[-1]) + 1
    R = min(R, n)
    if R - L < max(2, K):
        L, R = 0, n
    span = R - L
    bounds = None
    if K >= 2 and span >= K * MIN_SYL:
        bounds = _dp_bounds(e, L, R, K)
    used_dp = bounds is not None
    if bounds is None:
        bounds = [L + int(round(i * span / float(K))) for i in range(K)] + [R]
    starts = [L]
    for i in range(1, K):
        s = _snap_start(e, starts[-1], bounds[i]) if used_dp else bounds[i]
        s = max(s, starts[-1] + 2)
        s = min(s, R - 2 * (K - i))
        if s <= starts[-1]:
            s = starts[-1] + 1
        starts.append(s)
    ends = starts[1:] + [R]
    vstarts = []
    for i in range(K):
        s, t = starts[i], ends[i]
        v = _vowel_onset(e, voiced, s, t)
        if v - s > CONS_CAP:          # phu am qua dai (dong am/tho) -> bo phan dau
            starts[i] = v - CONS_CAP
        vstarts.append(v)
    return starts, vstarts, R, used_dp


def _fill_gaps(vm):
    vm = vm.copy()
    idx = np.where(vm)[0]
    if len(idx) >= 2:
        vm[idx[0]:idx[-1] + 1] = True
    return vm


def count_units(x, sr, f0, fp=FP, min_depth_db=2.5):
    """
    Do so don vi mora (nuclei) co that trong audio: dem cac 'thung lung' nang luong du sau.
    Dung de kiem tra TTS co that su tao ra dung so am tiet cua note khong - DP o _dp_bounds luon
    chia du K doan nen khong bao gio bao loi "audio chi co 4 am tiet, note lai 5".
    """
    n = len(f0)
    if n < 3:
        return 1
    hop = int(round(sr * fp / 1000.0))
    e = _energy_db(np.nan_to_num(np.asarray(x, dtype=np.float64)), sr, n, hop)
    act = np.where(e > e.max() - 42.0)[0]
    lo, hi = (int(act[0]), int(act[-1]) + 1) if len(act) else (0, n)
    if hi - lo < 3:
        return 1

    depth = _valley_depth(e, W=10)
    cand = np.where(depth[lo + 1:hi - 1] >= min_depth_db)[0] + lo + 1
    gap = max(2, MIN_SYL // 2)
    count, last = 0, -(10 ** 9)
    for t in cand:                      # mot thung lung nhieu frame -> chi dem mot lan
        if t - last >= gap:
            count += 1
        last = t
    return count + 1


def split_units(x, sr, f0, sp, ap, K, fp=FP):
    """Cat audio da phan tich thanh K don vi {"cons": (sp, ap, voiced), "vow": (...), "dp": bool}."""
    # pw.d4c/cheaptrick hay tra NaN/inf o cac frame khong huu thanh (am phu am, nhieu) - neu
    # de nguyen thi NaN lan theo ca tu -2d va lan sang ca audio cuoi cung. Chuan hoa lai truoc.
    x = np.nan_to_num(np.asarray(x, dtype=np.float64))
    f0 = np.nan_to_num(np.asarray(f0, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    sp = np.maximum(np.nan_to_num(np.asarray(sp, dtype=np.float64), nan=1e-12, posinf=1e-12), 1e-12)
    ap = np.clip(np.nan_to_num(np.asarray(ap, dtype=np.float64), nan=1.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    starts, vstarts, end, used_dp = segment(x, sr, f0, K, fp)
    vm_all = f0 > 1.0
    n = len(f0)
    ends = starts[1:] + [end]
    units = []
    for i in range(K):
        s = min(starts[i], n - 1)
        t = min(max(ends[i], s + 1), n)
        v = min(max(vstarts[i], s), max(s, t - 2))
        units.append({
            "cons": (sp[s:v], ap[s:v], vm_all[s:v]),
            "vow": (sp[v:t], ap[v:t], _fill_gaps(vm_all[v:t])),
            "dp": used_dp,
        })
    return units


# ------------------------------------------------------------------ keo gian / ghep
def _resize_2d(arr, new_len):
    old = arr.shape[0]
    if new_len <= 0:
        return arr[:0]
    if old == new_len:
        return arr
    pos = np.linspace(0, old - 1, new_len)
    i0 = np.floor(pos).astype(int)
    i1 = np.minimum(i0 + 1, old - 1)
    frac = (pos - i0)[:, None]
    return arr[i0] * (1.0 - frac) + arr[i1] * frac


def _resize_mask(vm, new_len):
    return _resize_2d(vm.astype(np.float64)[:, None], new_len)[:, 0] > 0.5


def _stretch_vowel(sp, ap, vm, target):
    """Giu attack/release gan do dai tu nhien, chi keo/nen phan giua (khong lam nhoe chuyen tiep)."""
    L = sp.shape[0]
    target = int(max(1, target))
    if L == target:
        return sp, ap, vm

    def simple():
        return _resize_2d(sp, target), _resize_2d(ap, target), _resize_mask(vm, target)

    on = min(max(1, int(L * 0.3)), 6)
    tl = min(max(1, int(L * 0.2)), 4)
    if L < 6 or L - on - tl < 1:
        return simple()
    if target >= L:
        on_t, tl_t = on, tl
    else:
        on_t = max(1, min(on, int(round(target * 0.35))))
        tl_t = max(1, min(tl, int(round(target * 0.25))))
    mt = target - on_t - tl_t
    if mt < 1:
        return simple()
    cuts = [(0, on, on_t), (on, L - tl, mt), (L - tl, L, tl_t)]
    sps = [_resize_2d(sp[a:b], k) for a, b, k in cuts]
    aps = [_resize_2d(ap[a:b], k) for a, b, k in cuts]
    vms = [_resize_mask(vm[a:b], k) for a, b, k in cuts]
    return np.concatenate(sps), np.concatenate(aps), np.concatenate(vms)


def render_phrase(units, groups, note_edges, midi, pre_frames, sr, fp=FP, up_key=0.0, glide=2.0):
    """
    units: list (theo groups) cac unit tu split_units, hoac None (nhom im lang).
    midi: notenum (hoac None) cua tung note trong phrase. pre_frames: so frame rest ngay truoc phrase
    (phu am cua am tiet dau duoc an vao do). Tra ve (audio float64, lead_samples) - lead_samples la
    phan audio nam TRUOC beat dau phrase (caller phai dat audio som lead_samples).
    """
    G = len(groups)
    n_notes = sum(len(g["notes"]) for g in groups)
    if len(units) != G or len(midi) != n_notes:
        raise ValueError(
            "render_phrase: units/groups/midi khong khop (%d/%d/%d, can %d/%d/%d)"
            % (len(units), G, len(midi), G, G, n_notes)
        )
    ref = next((u for u in units if u is not None), None)
    if ref is None:
        return np.zeros(0), 0
    nb = ref["vow"][0].shape[1]
    beat = [g["start"] for g in groups] + [groups[-1]["end"]]
    N = beat[-1]
    T = [beat[i + 1] - beat[i] for i in range(G)]

    # ---- phu am: dat TRUOC beat, an toi da 1/2 note truoc (am tiet dau: an vao rest hoac vao note chinh no)
    cn = [(u["cons"][0].shape[0] if u is not None else 0) for u in units]
    c = [0] * G
    for g in range(1, G):
        c[g] = min(cn[g], CONS_CAP, int(0.5 * T[g - 1]))
    c0nat = min(cn[0], CONS_CAP)
    pre_use = min(c0nat, max(0, int(pre_frames)))
    inside = min(c0nat - pre_use, int(0.4 * T[0]))
    c[0] = pre_use + inside
    vs = [beat[g] + (inside if g == 0 else 0) for g in range(G)]
    for g in range(G - 1):
        maxc = beat[g + 1] - vs[g] - 1
        if c[g + 1] > maxc:
            c[g + 1] = max(0, maxc)
    ve = [beat[g + 1] - c[g + 1] for g in range(G - 1)] + [N]

    lead = pre_use
    total = lead + N
    sp_o = np.full((total, nb), 1e-12)
    ap_o = np.ones((total, nb))
    vm_o = np.zeros(total, dtype=bool)
    for g, u in enumerate(units):
        if u is None:
            continue
        if c[g] > 0 and cn[g] > 0:
            cs, ca, cv = u["cons"]
            if c[g] != cn[g]:
                cs, ca, cv = _resize_2d(cs, c[g]), _resize_2d(ca, c[g]), _resize_mask(cv, c[g])
            a = lead + vs[g] - c[g]
            sp_o[a:a + c[g]], ap_o[a:a + c[g]], vm_o[a:a + c[g]] = cs, ca, cv
        tgt = ve[g] - vs[g]
        vsp, vap, vvm = u["vow"]
        if tgt >= 1 and len(vsp) > 0:
            vsp, vap, vvm = _stretch_vowel(vsp, vap, vvm, tgt)
            a = lead + vs[g]
            sp_o[a:a + tgt], ap_o[a:a + tgt], vm_o[a:a + tgt] = vsp, vap, vvm

    # ---- pitch: cau thang note, buoc nhay tai (beat - phu am) de phu am huu thanh mang cao do moi
    g_of = {}
    for g, grp in enumerate(groups):
        for k in grp["notes"]:
            g_of[k] = g
    nstart = []
    for k in range(len(midi)):
        g = g_of[k]
        if groups[g]["notes"][0] == k:
            nstart.append(lead + vs[g] - c[g])
        else:
            nstart.append(lead + note_edges[k])
    nstart.append(total)
    nstart = np.clip(np.maximum.accumulate(np.array(nstart, dtype=np.int64)), 0, total)
    mf = np.zeros(total)
    last = 66.0
    for k in range(len(midi)):
        m = last if midi[k] is None else float(midi[k])
        last = m
        mf[nstart[k]:nstart[k + 1]] = m
    if nstart[0] > 0:
        mf[:nstart[0]] = mf[nstart[0]] if nstart[0] < total else last
    if glide > 0:
        mf = gaussian_filter1d(mf, glide, mode="nearest")   # lam muot theo semitone (log-Hz)
    hz = 440.0 * 2.0 ** ((mf + float(up_key) - 69.0) / 12.0)
    f0o = np.where(vm_o, hz, 0.0)

    y = pw.synthesize(np.ascontiguousarray(f0o, dtype=np.float64),
                      np.ascontiguousarray(sp_o, dtype=np.float64),
                      np.ascontiguousarray(ap_o, dtype=np.float64), sr, fp)
    return y, lead * int(round(sr * fp / 1000.0))
