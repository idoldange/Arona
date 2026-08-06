"""
utils/text_utils.py
──────────────────────────────────────────────────────────────────────────────
Pure text / string helper functions.  No I/O, no Discord, no side-effects.
"""

import re
from datetime import datetime, timezone, timedelta


def split_message(text: str, max_length: int = 2000) -> list[str]:
    """
    Split *text* into Discord-safe chunks of at most *max_length* characters.

    Tries to break on newline → period → comma → space before hard-cutting.
    """
    parts = []
    while len(text) > max_length:
        split_index = text.rfind("\n", 0, max_length)
        if split_index == -1:
            split_index = text.rfind(".", 0, max_length)
        if split_index == -1:
            split_index = text.rfind(",", 0, max_length)
        if split_index == -1:
            split_index = text.rfind(" ", 0, max_length)
        if split_index == -1:
            split_index = max_length
        parts.append(text[:split_index].strip())
        text = text[split_index:].strip()
    if text:
        parts.append(text)
    return parts


def time_utc() -> str:
    """Return the current UTC time as 'YYYY-MM-DD HH:MM:SS'."""
    utc = timezone(timedelta(hours=0))
    return datetime.now(utc).strftime("%Y-%m-%d %H:%M:%S")


def is_japanese(text: str) -> bool:
    """Return True if *text* contains any Japanese/CJK characters."""
    return bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]", text))


async def convert_md_to_grid_table(md_text: str) -> str:
    """
    Convert a Markdown pipe-table to a fixed-width grid table.

    Returns *md_text* unchanged if it contains no valid table rows.
    """
    lines = [line.strip() for line in md_text.strip().split("\n") if line.strip()]
    data = []

    for line in lines:
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells.pop(0)
        if cells and cells[-1] == "":
            cells.pop()
        # Skip separator rows (e.g. |---|:---:|)
        if all(re.match(r"^[:\s-]+$", c) for c in cells):
            continue
        data.append(cells)

    if not data:
        return md_text

    num_cols = max(len(row) for row in data)
    col_widths = [0] * num_cols
    for row in data:
        for i, cell in enumerate(row):
            if i < num_cols:
                col_widths[i] = max(col_widths[i], len(str(cell)))

    border = "+" + "+".join(["-" * (w + 2) for w in col_widths]) + "+"

    output = [border]
    for i, row in enumerate(data):
        line_content = "|"
        for j in range(num_cols):
            val = row[j] if j < len(row) else ""
            line_content += f" {val:<{col_widths[j]}} |"
        output.append(line_content)
        if i == 0 or i == len(data) - 1:
            output.append(border)

    return "\n".join(output)
