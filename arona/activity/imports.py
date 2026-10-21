"""File ustx/mid trong panel `!arona synth` -> JSON cho Activity (dung lai parser san co cua synth.py).

Moi project: {"name", "ppq", "tempos": [[tick, bpm], ...], "tracks": [{"idx", "name", "role", "program", "bank", "sf", "drums", "notes": [...]}]}

Track duoc phan loai nhu luc synth (classify_track) nen nha cu / vocal / drums khop voi ban bot render;
lua chon trong panel (doi nhac cu, mute...) duoc ap qua `overrides` va soundfont thuc te qua `resolve`.
"""
from __future__ import annotations

import copy
import os

MAX_NOTES_PER_TRACK = 20000
MAX_TRACKS = 64


def project_to_json(name: str, proj, overrides: dict | None = None, resolve=None) -> dict:
    """overrides: {track_idx: over} (xem synth.apply_track_override).
    resolve(t) -> (sf_stem, bank, program): soundfont that the bot would use for this track."""
    from arona.tts import synth as S

    tracks = []
    for t in proj.tracks[:MAX_TRACKS]:
        if not t.notes:
            continue
        S.classify_track(t)
        if overrides and t.idx in overrides:
            t = copy.deepcopy(t)                  # khong sua project da cache
            S.apply_track_override(t, overrides[t.idx])
        if t.mute or t.role == "mute" or not t.notes:
            continue
        sf, bank, program = "", 128 if t.drums else 0, int(t.program)
        if resolve is not None:
            try:
                sf, bank, program = resolve(t)
            except Exception:
                pass
        tracks.append({
            "idx": t.idx,
            "name": t.name or f"Track{t.idx + 1}",
            "role": t.role,
            "program": int(program),
            "bank": int(bank),
            "sf": str(sf or ""),
            "drums": bool(t.drums),
            "notes": [{"pos": n.pos, "dur": n.dur, "tone": n.tone, "lyric": n.lyric, "vel": n.vel}
                      for n in t.notes[:MAX_NOTES_PER_TRACK]],
        })
    return {"name": os.path.splitext(os.path.basename(name))[0] or "project",
            "ppq": proj.resolution,
            "tempos": [[int(t), float(b)] for t, b in proj.tempos],
            "tracks": tracks}


def _parse(name: str, data: bytes):
    from arona.tts import synth as S
    kind = S.detect_format(data)
    if kind == "ustx":
        return S.parse_ustx(data)
    if kind == "midi":
        return S.parse_midi(data)
    return None


def projects_to_json(projects, overrides: dict | None = None, resolve=None) -> list[dict]:
    """projects: [(ten_file, UProject)] da parse san (panel cache) -> JSON."""
    out = []
    for name, proj in projects:
        item = project_to_json(name, proj, (overrides or {}).get(name), resolve)
        if item["tracks"]:
            out.append(item)
    return out


def build_imports(files, overrides: dict | None = None, resolve=None) -> list[dict]:
    """files: [(ten_file, bytes)]. Chi nhan ustx va mid; file .ust don le va file khac bi bo qua (khong loi)."""
    projects = []
    for name, data in files:
        try:
            proj = _parse(name, data)
        except Exception:
            continue
        if proj is not None:
            projects.append((name, proj))
    return projects_to_json(projects, overrides, resolve)
