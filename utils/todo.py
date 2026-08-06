"""
utils/todo.py

Per-channel TODO list manager for Arona.

Storage: database/todos/<channel_id>.json
Format:  {"items": [{"content": str, "done": bool}, ...]}

The TODO block is injected into every system prompt for a channel
until the model calls todo(action="done") with no remaining items,
or until the list is empty.
"""

from __future__ import annotations

import json
import os
from typing import List

TODO_DIR = "database/todos"
os.makedirs(TODO_DIR, exist_ok=True)


def _path(channel_id: int | str) -> str:
    return os.path.join(TODO_DIR, f"{channel_id}.json")


def _load(channel_id: int | str) -> list[dict]:
    p = _path(channel_id)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("items", [])
    except Exception:
        return []


def _save(channel_id: int | str, items: list[dict]):
    with open(_path(channel_id), "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)


def _delete(channel_id: int | str):
    p = _path(channel_id)
    if os.path.exists(p):
        os.remove(p)


# public api

def todo_create(channel_id: int | str, content: list[str]) -> dict:
    """Create a new TODO list, replacing any existing one."""
    items = [{"content": c.strip(), "done": False} for c in content if c.strip()]
    if not items:
        return {"error": "No content provided."}
    _save(channel_id, items)
    return {"ok": True, "items": items}


def todo_done(channel_id: int | str, content: list[str]) -> dict:
    """
    Mark items as done by matching content strings.
    If content is empty, confirm entire TODO is complete and delete it.
    """
    items = _load(channel_id)
    if not items:
        return {"error": "No active TODO for this channel."}

    if not content:
        # Confirm all done — delete
        _delete(channel_id)
        return {"ok": True, "completed": True, "message": "TODO list cleared."}

    matched = 0
    for target in content:
        target = target.strip()
        for item in items:
            if item["content"].strip() == target or target.lower() in item["content"].lower():
                item["done"] = True
                matched += 1
                break

    _save(channel_id, items)

    # Auto-delete if all done
    all_done = all(i["done"] for i in items)
    if all_done:
        _delete(channel_id)
        return {"ok": True, "matched": matched, "completed": True, "message": "All items done — TODO cleared."}

    return {"ok": True, "matched": matched, "items": items}


def todo_edit(channel_id: int | str, old_content: str, new_content: str) -> dict:
    """Find an item by content and replace its text."""
    items = _load(channel_id)
    if not items:
        return {"error": "No active TODO for this channel."}

    old = old_content.strip()
    new = new_content.strip()
    for item in items:
        if item["content"].strip() == old or old.lower() in item["content"].lower():
            item["content"] = new
            _save(channel_id, items)
            return {"ok": True, "items": items}

    return {"error": f"Item not found: '{old}'"}


def get_todo_block(channel_id: int | str) -> str:
    """
    Return a system-prompt block for active TODO items.
    Empty string if no active TODO.
    """
    items = _load(channel_id)
    if not items:
        return ""

    lines = ["## ACTIVE TODO"]
    lines.append("Complete these tasks. Call todo(action=\"done\", content=[...]) for each finished item.")
    lines.append("⚠️ CRITICAL: NEVER echo TODO item text, counts (e.g. '1/2 done'), or descriptions in your reply text. The embed updates automatically. Respond naturally to Sensei after calling the tool.")
    for item in items:
        tick = "<:arona_circle_check:1484176911918956797>" if item["done"] else "<:arona_circle_dashed:1484176913885954218>"
        lines.append(f"  {tick} {item['content']}")
    lines.append("")
    return "\n".join(lines)


def build_todo_embed(channel_id: int | str) -> dict | None:
    """
    Build a Discord embed dict for the current TODO list.
    Returns None if no active TODO.
    """
    items = _load(channel_id)
    if not items:
        return None

    done_count = sum(1 for i in items if i["done"])
    total = len(items)

    description_lines = []
    for item in items:
        if item["done"]:
            description_lines.append(f"<:arona_square_check:1484176918587772960> ~~{item['content']}~~")
        else:
            description_lines.append(f"<:arona_square:1484176917300121742> {item['content']}")

    return {
        "title": f"<:arona_clipboard:1484176915521605673> TODO ({done_count}/{total} done)",
        "description": "\n".join(description_lines),
        "color": 0x57F287 if done_count == total else 0x5865F2,
    }


# tool schema

TODO_TOOL_SCHEMA = {
    "name": "todo",
    "description": (
        "Manage a persistent TODO/task list for the current channel. "
        "The list is shown as an embed and injected into every prompt until all items are done. "
        "Use this to plan multi-step tasks before starting — create the list, then work through it. "
        "Actions: "
        "'create' — start a new TODO list (replaces existing); "
        "'done' — mark specific items as complete (pass content array); call with empty content to confirm entire list finished; "
        "'edit' — find and replace a single item's text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "done", "edit"],
                "description": "Action to perform."
            },
            "content": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For 'create': list of task strings to add. "
                    "For 'done': list of completed item strings to tick off. Empty = confirm all done. "
                    "Not used for 'edit'."
                )
            },
            "old_content": {
                "type": "string",
                "description": "For 'edit': the item text to find."
            },
            "new_content": {
                "type": "string",
                "description": "For 'edit': replacement text."
            }
        },
        "required": ["action"]
    }
}