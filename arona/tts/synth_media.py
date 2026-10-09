"""Ghep media (anh / video / audio) vao ket qua `!arona synth` bang ffmpeg.

- anh   : tao video. 1 anh = phat full thoi luong; nhieu anh = fade lan luot, thoi gian chia deu
          (khi chi co anh: tong thoi luong = do dai audio).
- video : giu nguyen hinh; am thanh cua video (neu co) dua vao 1 track rieng, mix voi synth/audio.
          Nhieu video: het video n phat video n+1. Anh xen giua video moi anh `img` giay.
- audio : them vao nhu 1 track nhac (mix cung synth).
Output: mp4 neu co anh/video, nguoc lai wav.
"""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif")
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".opus", ".aac")

MAX_OUT_BYTES = 9 * 1024 * 1024   # gioi han upload Discord (giong nhanh wav->mp3 o main.py)
MAX_SECONDS = 600
FPS = 30
AUDIO_KBPS = 128
MEDIA_MAX_MB = {"image": 10, "video": 50, "audio": 25}


def media_kind(filename: str):
    """'image' | 'video' | 'audio' | None theo duoi file."""
    fn = (filename or "").lower()
    if fn.endswith(IMAGE_EXTS):
        return "image"
    if fn.endswith(VIDEO_EXTS):
        return "video"
    if fn.endswith(AUDIO_EXTS):
        return "audio"
    return None


def _probe(path):
    """-> (duration_s, has_audio, (w, h) | None) hoac None neu ffprobe khong doc duoc."""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height:stream_side_data=rotation", "-of", "json", path],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            return None
        info = json.loads(r.stdout or "{}")
        try:
            dur = float((info.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
        size = None
        for s in info.get("streams", []):
            if s.get("codec_type") == "video" and s.get("width") and s.get("height"):
                w, h = int(s["width"]), int(s["height"])
                for sd in s.get("side_data_list") or []:   # video quay doc tu dien thoai: ffmpeg tu xoay -> doi w/h
                    if abs(int(float(sd.get("rotation", 0)))) in (90, 270):
                        w, h = h, w
                size = (w, h)
                break
        return dur, has_audio, size
    except Exception:
        return None


def _opt_float(opts, key, default, lo, hi):
    try:
        v = float(opts.get(key, default))
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def _render_sync(synth_wav, media, opts):
    with tempfile.TemporaryDirectory(prefix="arona_synth_") as tmp:
        visuals, audios = [], []   # visuals: image/video theo thu tu upload; audios: track nhac
        if synth_wav:
            p = os.path.join(tmp, "synth.wav")
            with open(p, "wb") as f:
                f.write(synth_wav)
            pr = _probe(p)
            audios.append({"path": p, "dur": pr[0] if pr else 0.0})
        for i, (name, data) in enumerate(media):
            kind = media_kind(name)
            p = os.path.join(tmp, f"m{i}{os.path.splitext(name)[1].lower()}")
            with open(p, "wb") as f:
                f.write(data)
            pr = _probe(p)
            if pr is None:
                return None, "", f"Couldn't read `{name}` (corrupt or unsupported file)."
            dur, has_audio, size = pr
            if kind in ("image", "video") and not size:
                return None, "", f"Couldn't read the size of `{name}`."
            if kind == "image":
                visuals.append({"kind": "image", "path": p, "name": name, "size": size})
            elif kind == "video":
                if dur <= 0:
                    return None, "", f"Couldn't get the duration of `{name}`."
                visuals.append({"kind": "video", "path": p, "name": name, "d": dur, "has_audio": has_audio, "size": size})
            else:
                if dur <= 0:
                    return None, "", f"Couldn't get the duration of `{name}`."
                audios.append({"path": p, "dur": dur})

        ta = max([a["dur"] for a in audios], default=0.0)   # do dai nhac (cac track deu bat dau o 0)
        has_video = any(v["kind"] == "video" for v in visuals)
        fade = _opt_float(opts, "fade", 1.0, 0.0, 5.0)
        vid_vol = _opt_float(opts, "vid_vol", 100.0, 0.0, 300.0) / 100.0

        # ---- len ke hoach thoi gian cho phan hinh ----
        total = ta
        f = 0.0
        if visuals:
            imgs = [v for v in visuals if v["kind"] == "image"]
            if not has_video:
                if ta <= 0:
                    return None, "", "No audio to put the images on (attach a .ust/.ustx/.mid or an audio file)."
                n = len(imgs)
                f = min(fade, ta / n / 3) if n > 1 else 0.0
                d = (ta + (n - 1) * f) / n
                for v in imgs:
                    v["d"] = d
            else:
                d = _opt_float(opts, "img", 3.0, 0.5, 60.0)
                f = min(fade, d / 3)
                for v in imgs:
                    v["d"] = d
            cur, prev = 0.0, None
            for v in visuals:
                if prev and prev["kind"] == "image" and v["kind"] == "image" and f > 0:
                    v["start"], v["join"] = cur - f, "xfade"
                    cur += v["d"] - f
                else:
                    v["start"], v["join"] = cur, ("concat" if prev else None)
                    cur += v["d"]
                prev = v
            tv = cur
            total = max(tv, ta)
        if total <= 0:
            return None, "", "Nothing to render."
        if total > MAX_SECONDS:
            return None, "", f"Result would be {total:.0f}s long (max {MAX_SECONDS}s)."

        out_ext = "mp4" if visuals else "wav"
        out_path = os.path.join(tmp, f"out.{out_ext}")

        def build(vb_kbps):
            args = [FFMPEG, "-nostdin", "-y", "-hide_banner", "-loglevel", "error"]
            idx = 0
            for v in visuals:
                if v["kind"] == "image":
                    args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{v['d']:.3f}", "-i", v["path"]]
                else:
                    args += ["-i", v["path"]]
                v["idx"] = idx
                idx += 1
            for a in audios:
                args += ["-i", a["path"]]
                a["idx"] = idx
                idx += 1

            fc = []
            if visuals:
                # khung hinh theo kich thuoc media dau tien (giu ti le), chi thu nho khi bitrate khong du
                bw, bh = visuals[0]["size"]
                cap = 1280 if vb_kbps >= 1500 else 854 if vb_kbps >= 600 else 640 if vb_kbps >= 100 else 426
                k = min(1.0, cap / max(bw, bh))
                w, h = max(2, int(bw * k) // 2 * 2), max(2, int(bh * k) // 2 * 2)
                for j, v in enumerate(visuals):
                    fc.append(
                        f"[{v['idx']}:v]setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=decrease,"
                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS},format=yuv420p[s{j}]"
                    )
                last = "s0"
                for j in range(1, len(visuals)):
                    v, out = visuals[j], f"c{j}"
                    if v["join"] == "xfade":
                        fc.append(f"[{last}][s{j}]xfade=transition=fade:duration={f:.3f}:offset={v['start']:.3f}[{out}]")
                    else:
                        fc.append(f"[{last}][s{j}]concat=n=2:v=1:a=0,fps={FPS}[{out}]")
                    last = out
                pad = total - tv
                if pad > 0.05:
                    fc.append(f"[{last}]tpad=stop_mode=clone:stop_duration={pad:.3f}[vout]")
                else:
                    fc.append(f"[{last}]null[vout]")

            fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            labels = []
            for k, a in enumerate(audios):
                fc.append(f"[{a['idx']}:a]{fmt}[m{k}]")
                labels.append(f"[m{k}]")
            vlabels = []
            for j, v in enumerate(visuals):
                if v["kind"] == "video" and v["has_audio"]:
                    ms = int(v["start"] * 1000)
                    fc.append(f"[{v['idx']}:a]{fmt},volume={vid_vol:.3f},adelay={ms}|{ms}[va{j}]")
                    vlabels.append(f"[va{j}]")
            if vlabels:   # track rieng cho am thanh cua video
                if len(vlabels) == 1:
                    labels.append(vlabels[0])
                else:
                    fc.append(f"{''.join(vlabels)}amix=inputs={len(vlabels)}:normalize=0:duration=longest[vtrack]")
                    labels.append("[vtrack]")
            tail = f",apad=whole_dur={total:.3f}" if visuals else ""   # apad huu han (apad vo han co the treo ffmpeg)
            if labels:
                if len(labels) == 1:
                    fc.append(f"{labels[0]}alimiter=limit=0.97{tail}[aout]")
                else:
                    fc.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,alimiter=limit=0.97{tail}[aout]")

            args += ["-filter_complex", ";".join(fc)]
            if visuals:
                args += ["-map", "[vout]"]
                if labels:
                    args += ["-map", "[aout]"]
                args += [
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-b:v", f"{vb_kbps}k", "-maxrate", f"{int(vb_kbps * 1.3)}k", "-bufsize", f"{vb_kbps * 2}k",
                    "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-movflags", "+faststart", "-t", f"{total:.3f}",
                ]
            else:
                args += ["-map", "[aout]", "-c:a", "pcm_s16le"]
            args.append(out_path)
            return args

        max_bytes = int(opts.get("max_bytes") or MAX_OUT_BYTES)
        vb = int(max_bytes * 8 * 0.92 / total / 1000) - AUDIO_KBPS if visuals else 0
        if visuals and vb < 30:
            return None, "", f"Result would be {total:.0f}s long, too long to fit Discord's upload limit as a video."
        for _ in range(3):
            r = subprocess.run(build(vb), capture_output=True, text=True, timeout=max(180, int(total * 2)), stdin=subprocess.DEVNULL)
            if r.returncode != 0 or not os.path.exists(out_path):
                return None, "", f"ffmpeg failed: {(r.stderr or '').strip()[-400:]}"
            size = os.path.getsize(out_path)
            if not visuals or size <= max_bytes:
                break
            vb = int(vb * max_bytes / size * 0.85)   # qua nang -> ha bitrate roi encode lai
            if vb < 20:
                return None, "", "Video is too large to upload even at low quality."
        with open(out_path, "rb") as fh:
            return fh.read(), out_ext, None


async def render_media(synth_wav, media, opts=None):
    """synth_wav: bytes wav tu synth (hoac None). media: [(filename, bytes)].
    opts: vid_vol (% am luong am thanh video, 100), fade (giay crossfade giua anh, 1), img (giay/anh khi co video, 3).
    -> (bytes | None, ext, err)"""
    try:
        return await asyncio.to_thread(_render_sync, synth_wav, media, opts or {})
    except subprocess.TimeoutExpired:
        return None, "", "ffmpeg timed out."
    except Exception as e:
        return None, "", f"Media render error: {e}"
