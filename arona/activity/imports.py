"""File ustx/mid trong panel `!arona synth` -> JSON cho Activity (dung lai parser san co cua synth.py).

Moi project: {"name", "ppq", "tempos": [[tick, bpm], ...], "tracks": [{"name", "role", "program", "drums", "notes": [{"pos","dur","tone","lyric","vel"}]}]}
"""
from __future__ import annotations

import os

MAX_NOTES_PER_TRACK = 20000
MAX_TRACKS = 64


def project_to_json(name: str, proj) -> dict:
    tracks = []
    for t in proj.tracks[:MAX_TRACKS]:
        if not t.notes:
            continue
        tracks.append({
            "name": t.name or f"Track{t.idx + 1}",
            "role": t.role,
            "program": t.program,
            "drums": bool(t.drums),
            "notes": [{"pos": n.pos, "dur": n.dur, "tone": n.tone, "lyric": n.lyric, "vel": n.vel}
                      for n in t.notes[:MAX_NOTES_PER_TRACK]],
        })
    return {"name": os.path.splitext(os.path.basename(name))[0] or "project",
            "ppq": proj.resolution,
            "tempos": [[int(t), float(b)] for t, b in proj.tempos],
            "tracks": tracks}


def build_imports(files) -> list[dict]:
    """files: [(ten_file, bytes)]. Chi nhan ustx va mid; file .ust don le va file khac bi bo qua (khong loi)."""
    from arona.tts import synth as S
    out = []
    for name, data in files:
        try:
            kind = S.detect_format(data)
            if kind == "ustx":
                proj = S.parse_ustx(data)
            elif kind == "midi":
                proj = S.parse_midi(data)
            else:
                continue
        except Exception:
            continue
        item = project_to_json(name, proj)
        if item["tracks"]:
            out.append(item)
    return out
