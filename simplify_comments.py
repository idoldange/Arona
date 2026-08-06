#!/usr/bin/env python3
"""
Simplify decorative banner comments in the codebase.

Turns:
    # ── Constants ──────────────────────────────────────────────────────────────
into:
    # constants

Deletes pure separator lines (no title text), e.g.:
    # ══════════════════════════════════════════════════════════════
    # ─────────────────────────────────────────────

Usage:
    python simplify_comments.py            # dry-run, prints a diff-like preview
    python simplify_comments.py --apply    # actually rewrite files

Respects .gitignore (uses `git ls-files` if inside a git repo, else `rg --files`).
Excludes config.py.
"""
import re
import subprocess
import sys
from pathlib import Path

EXCLUDE_FILES = {"config.py"}
EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".lua", ".java", ".c", ".cpp", ".h", ".sh"}

BOX = r'[─━═┄┅╌╍\-=]'
TITLE_RE = re.compile(
    rf'^(?P<indent>\s*)(?P<marker>#|//|--)\s*{BOX}{{2,}}\s+(?P<title>.+?)\s+{BOX}{{2,}}\s*(?:#|//)?\s*$'
)
SEP_RE = re.compile(
    rf'^(?P<indent>\s*)(?P<marker>#|//|--)\s*{BOX}{{5,}}\s*$'
)


def get_tracked_files():
    try:
        out = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        return [Path(p) for p in out]
    except Exception:
        out = subprocess.run(
            ["rg", "--files"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        return [Path(p) for p in out]


def process_file(path: Path, apply: bool):
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return []

    lines = text.splitlines(keepends=True)
    changes = []
    new_lines = []

    for i, line in enumerate(lines, 1):
        stripped = line.rstrip("\r\n")
        eol = line[len(stripped):]

        m_title = TITLE_RE.match(stripped)
        m_sep = SEP_RE.match(stripped)

        if m_title:
            new_stripped = f"{m_title.group('indent')}{m_title.group('marker')} {m_title.group('title').lower()}"
            changes.append((i, stripped, new_stripped))
            new_lines.append(new_stripped + eol)
        elif m_sep:
            changes.append((i, stripped, None))  # deleted
        else:
            new_lines.append(line)

    if changes and apply:
        path.write_text("".join(new_lines), encoding="utf-8")

    return changes


def main():
    apply = "--apply" in sys.argv
    files = get_tracked_files()
    total_changes = 0

    for f in files:
        if f.name in EXCLUDE_FILES:
            continue
        if f.suffix not in EXTENSIONS:
            continue
        if not f.exists():
            continue

        changes = process_file(f, apply)
        if changes:
            print(f"\n{f}")
            for lineno, old, new in changes:
                if new is None:
                    print(f"  L{lineno}: DELETE  | {old.strip()}")
                else:
                    print(f"  L{lineno}: {old.strip()!r} -> {new.strip()!r}")
            total_changes += len(changes)

    print(f"\n{'Applied' if apply else 'Would change'}: {total_changes} lines across the files above.")
    if not apply:
        print("Re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
