"""
utils/edit_text_file.py

Tool functions for Gemini to create, edit, and send text files via Discord.

Temp storage
All in-progress files live in `database/files/` as:
  database/files/<uuid4>.<ext>          ← file content
  database/files/<uuid4>.meta.json      ← {"original_filename": "foo.html"}

A file can be referenced either by:
  - its temp ID  (just the uuid4 string, e.g. "a1b2c3d4-...")
  - a Discord CDN URL (https://cdn.discordapp.com/...)

Cleanup
- send_files() deletes every temp file it successfully uploads.
- Callers can also call cleanup_temp_file(file_id) manually.
"""

from __future__ import annotations

import os
import re
import json
import asyncio
import urllib.parse
from uuid import uuid4
from typing import Union

import aiohttp
import discord

from console import console

# config

TEMP_DIR = "database/files"
DISCORD_CDN_PREFIX = "https://cdn.discordapp.com/"

PREVIEW_EXTS = {
    ".html", ".htm", ".jsx", ".tsx", ".md", ".mermaid", ".mmd",
    ".svg", ".json", ".csv", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".txt", ".log", ".py", ".sh", ".cpp", ".js", ".ts",
    ".go", ".rs", ".java", ".cs", ".bash", ".bat", ".ps1",
    ".yaml", ".yml", ".xml", ".sql", ".markdown",
}

ARTIFACT_BASE = "https://arona.hangdongwibu.io/artifact/?url="

os.makedirs(TEMP_DIR, exist_ok=True)

# per-channel file registry
# Maps channel_id (int) → {file_id: filename} for files staged but not yet sent.
# Injected into the system prompt each turn so the model always knows pending IDs.

_channel_registry: dict[int, dict[str, str]] = {}


def register_file(channel_id: int, file_id: str, filename: str):
    """Register a staged file against a channel. Persists channel_id to meta for restart recovery."""
    _channel_registry.setdefault(channel_id, {})[file_id] = filename
    # Persist channel_id into meta so we can rebuild the registry after a restart
    meta = _read_meta(file_id)
    if meta.get("channel_id") != channel_id:
        meta["channel_id"] = channel_id
        meta.setdefault("original_filename", filename)
        _write_meta(file_id, meta)


def unregister_files(channel_id: int, file_ids: list[str]):
    """Remove sent/deleted file IDs from the registry."""
    reg = _channel_registry.get(channel_id, {})
    for fid in file_ids:
        reg.pop(fid, None)
    if not reg:
        _channel_registry.pop(channel_id, None)


def _rebuild_registry_from_disk():
    """
    Scan TEMP_DIR on startup and rebuild _channel_registry from .meta.json files.
    Skips entries with no channel_id (files created before this feature).
    """
    if not os.path.isdir(TEMP_DIR):
        return
    try:
        entries = os.listdir(TEMP_DIR)
    except Exception:
        return
    for fname in entries:
        if not fname.endswith(".meta.json"):
            continue
        file_id = fname[: -len(".meta.json")]
        try:
            meta = _read_meta(file_id)
            channel_id = meta.get("channel_id")
            filename = meta.get("original_filename", "file")
            if not channel_id:
                continue
            # Only register if the actual data file still exists on disk
            data_exists = any(
                f.startswith(file_id) and not f.endswith(".meta.json")
                for f in entries
            )
            if data_exists:
                _channel_registry.setdefault(channel_id, {})[file_id] = filename
        except Exception:
            pass


def get_registry_block(channel_id: int) -> str:
    """
    Return a system-prompt block listing pending staged files for this channel.
    Empty string if nothing is staged.
    """
    reg = _channel_registry.get(channel_id, {})
    if not reg:
        return ""
    lines = ["## STAGED FILES (pending send_files)"]
    lines.append("These file_ids are staged in temp storage and ready to use with edit_file or send_files:")
    for fid, fname in reg.items():
        lines.append(f"  • file_id: `{fid}`  →  `{fname}`")
    lines.append("Call send_files with one or more of these IDs when ready to upload to Discord.")
    return "\n".join(lines) + "\n"


# Rebuild registry on import (survives bot restarts as long as files remain on disk)
_rebuild_registry_from_disk()

# internal helpers

def _is_cdn_url(value: str) -> bool:
    return value.startswith(DISCORD_CDN_PREFIX)


def _temp_path(file_id: str, ext: str = "") -> str:
    return os.path.join(TEMP_DIR, f"{file_id}{ext}")


def _meta_path(file_id: str) -> str:
    return os.path.join(TEMP_DIR, f"{file_id}.meta.json")


def _ext_from_filename(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower()


def _read_meta(file_id: str) -> dict:
    path = _meta_path(file_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_meta(file_id: str, meta: dict):
    with open(_meta_path(file_id), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def _find_temp_file(file_id: str) -> tuple[str | None, str]:
    """Return (full_path, original_filename) for a temp file ID, or (None, '')."""
    meta = _read_meta(file_id)
    original_filename = meta.get("original_filename", "")
    ext = _ext_from_filename(original_filename) if original_filename else ""

    # Try with known extension first
    if ext:
        p = _temp_path(file_id, ext)
        if os.path.exists(p):
            return p, original_filename

    # Fallback: scan temp dir for any file starting with the id
    for fname in os.listdir(TEMP_DIR):
        if fname.startswith(file_id) and not fname.endswith(".meta.json"):
            return os.path.join(TEMP_DIR, fname), original_filename

    return None, original_filename


async def _download_cdn(url: str) -> bytes:
    """Download a Discord CDN file and return raw bytes."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CDN returned HTTP {resp.status} for {url}")
            return await resp.read()


def _cdn_filename(url: str) -> str:
    """Best-effort extract filename from a Discord CDN URL."""
    path = urllib.parse.urlparse(url).path
    return os.path.basename(path) or "file"


# public api

def create_files(files: list[dict]) -> list[dict]:
    """
    Create one or more text files and save them to the temp directory.

    Parameters
    files : list of dicts, each with:
        - "filename" (str)  : desired filename, e.g. "index.html"
        - "content"  (str)  : text content of the file

    Returns
    list of dicts, each with:
        - "file_id"  : temp ID (pass to edit_file / send_files)
        - "filename" : original filename
        - "path"     : absolute path in temp dir
    """
    results = []
    for entry in files:
        filename = entry.get("filename", "file.txt").strip()
        content  = entry.get("content", "")
        ext      = _ext_from_filename(filename)
        file_id  = str(uuid4())

        file_path = _temp_path(file_id, ext)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            _write_meta(file_id, {"original_filename": filename})
            console.log(f"[edit_text_file] Created temp file: {filename} → {file_id}", "INFO")
            results.append({
                "file_id":  file_id,
                "filename": filename,
                "path":     os.path.abspath(file_path),
            })
        except Exception as e:
            console.log(f"[edit_text_file] Failed to create {filename}: {e}", "ERROR")
            results.append({
                "file_id":  None,
                "filename": filename,
                "error":    str(e),
            })

    return results


async def edit_file(
    file_ref: str,
    old_content: str,
    new_content: str,
    replace_multiple: bool = False,
    new_filename: str | None = None,
) -> dict:
    """
    Edit a text file (from temp ID or Discord CDN URL) by replacing a text segment.

    Parameters
    file_ref        : temp file ID  OR  Discord CDN URL.
    old_content     : exact text to find and replace.
    new_content     : replacement text.
    replace_multiple: if False (default) and old_content appears more than once,
                      returns an error asking Gemini to set this to True or
                      provide a more unique old_content string.
    new_filename    : optional new filename; if omitted, keeps the original name.

    Returns
    dict with:
        "file_id"   : temp ID of the edited file
        "filename"  : (possibly renamed) filename
        "path"      : absolute path
        "replaced"  : number of replacements made
    or on error:
        "error"     : error message
        "occurrences": count (when replace_multiple guard triggers)
    """
    # resolve source
    if _is_cdn_url(file_ref):
        # Download from CDN → write to temp
        cdn_url = file_ref
        original_filename = _cdn_filename(cdn_url)
        try:
            raw = await _download_cdn(cdn_url)
            text = raw.decode("utf-8")
        except Exception as e:
            return {"error": f"Failed to download from CDN: {e}"}

        file_id = str(uuid4())
        ext = _ext_from_filename(original_filename)
        file_path = _temp_path(file_id, ext)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text)
        _write_meta(file_id, {"original_filename": original_filename})

    else:
        # Local temp file
        file_path, original_filename = _find_temp_file(file_ref)
        if file_path is None:
            return {"error": f"Temp file not found for ID: {file_ref}"}
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        file_id = file_ref

    # apply replacement
    count = text.count(old_content)

    if count == 0:
        return {
            "error": (
                f"old_content not found in the file. "
                f"Make sure you copy it exactly (whitespace, indentation, line endings). "
                f"File ID: {file_id}, filename: {original_filename}"
            )
        }

    if count > 1 and not replace_multiple:
        return {
            "error": (
                f"old_content appears {count} times. "
                f"Set replace_multiple=true to replace all occurrences, "
                f"or provide a more unique old_content that appears exactly once."
            ),
            "occurrences": count,
        }

    new_text = text.replace(old_content, new_content)
    replaced = count if replace_multiple else 1

    # handle rename
    if new_filename and new_filename.strip():
        new_filename = new_filename.strip()
        new_ext = _ext_from_filename(new_filename)
        new_path = _temp_path(file_id, new_ext)

        # Remove old file if extension changed
        if file_path and file_path != new_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        file_path = new_path
        _write_meta(file_id, {"original_filename": new_filename})
        original_filename = new_filename
    else:
        ext = _ext_from_filename(original_filename)
        file_path = _temp_path(file_id, ext)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_text)

    console.log(
        f"[edit_text_file] Edited {original_filename} ({replaced} replacement(s)) → {file_id}",
        "INFO"
    )
    return {
        "file_id":  file_id,
        "filename": original_filename,
        "path":     os.path.abspath(file_path),
        "replaced": replaced,
    }


async def send_files(
    channel: discord.abc.Messageable,
    file_refs: list[str],
    filenames: list[str] | None = None,
) -> dict:
    """
    Send one or more files to a Discord channel, then delete their temp copies.

    Parameters
    channel   : Discord channel/thread to send to.
    file_refs : list of temp file IDs or Discord CDN URLs.
    filenames : optional list of override filenames (same length as file_refs).
                If shorter or None, original names are kept for the remainder.

    Returns
    dict with:
        "sent"   : list of {"filename", "cdn_url", "preview_url" (if applicable)}
        "errors" : list of {"ref", "error"}
    """
    if filenames is None:
        filenames = []

    sent_results  = []
    error_results = []
    temp_to_clean = []   # (file_id, file_path) to remove after send

    # build discord.file objects
    discord_files: list[tuple[discord.File, str]] = []  # (File, desired_filename)

    for idx, ref in enumerate(file_refs):
        override_name = filenames[idx] if idx < len(filenames) else None

        if _is_cdn_url(ref):
            # Download CDN file to a temp buffer
            cdn_filename = override_name or _cdn_filename(ref)
            try:
                raw = await _download_cdn(ref)
            except Exception as e:
                error_results.append({"ref": ref, "error": str(e)})
                continue

            # Write to temp so we can reuse the discord.File path
            file_id   = str(uuid4())
            ext       = _ext_from_filename(cdn_filename)
            file_path = _temp_path(file_id, ext)
            with open(file_path, "wb") as f:
                f.write(raw)
            _write_meta(file_id, {"original_filename": cdn_filename})
            temp_to_clean.append((file_id, file_path))

            try:
                discord_files.append((
                    discord.File(file_path, filename=cdn_filename),
                    cdn_filename,
                ))
            except Exception as e:
                error_results.append({"ref": ref, "error": str(e)})

        else:
            # Local temp file
            file_path, original_filename = _find_temp_file(ref)
            if file_path is None:
                error_results.append({"ref": ref, "error": f"Temp file not found for ID: {ref}"})
                continue

            send_name = override_name or original_filename or os.path.basename(file_path)
            temp_to_clean.append((ref, file_path))

            try:
                discord_files.append((
                    discord.File(file_path, filename=send_name),
                    send_name,
                ))
            except Exception as e:
                error_results.append({"ref": ref, "error": str(e)})

    if not discord_files:
        _cleanup_list(temp_to_clean)
        return {"sent": sent_results, "errors": error_results}

    # send in batches of 10 (discord limit)
    for batch_start in range(0, len(discord_files), 10):
        batch = discord_files[batch_start : batch_start + 10]
        batch_files  = [df for df, _ in batch]
        batch_names  = [name for _, name in batch]
        part_label   = f" (Part {batch_start // 10 + 1})" if len(discord_files) > 10 else ""

        try:
            sent_msg = await channel.send(
                content=f"-# File(s){part_label}",
                files=batch_files,
            )
        except Exception as e:
            for name in batch_names:
                error_results.append({"ref": name, "error": f"Discord send failed: {e}"})
            continue

        # Collect CDN URLs and build preview links
        preview_lines = []
        for att in sent_msg.attachments:
            ext_lower = os.path.splitext(att.filename)[1].lower()
            preview_url = None
            if ext_lower in PREVIEW_EXTS:
                encoded = urllib.parse.quote(att.url, safe="")
                preview_url = f"{ARTIFACT_BASE}{encoded}"
                preview_lines.append(
                    f"[{att.filename} — Preview]({preview_url})"
                )
            sent_results.append({
                "filename":    att.filename,
                "cdn_url":     att.url,
                "preview_url": preview_url,
            })

        # Edit message to append preview links
        if preview_lines:
            try:
                await sent_msg.edit(
                    content=sent_msg.content + "\n" + "\n".join(preview_lines)
                )
            except Exception as e:
                console.log(f"[edit_text_file] Failed to append preview links: {e}", "WARN")

    # cleanup temp files
    _cleanup_list(temp_to_clean)

    return {"sent": sent_results, "errors": error_results}


async def read_file(file_ref: str, start_line: int = 1, end_line: int = 2000) -> dict:
    """
    Read a slice of a file by line range. Read-only — nothing is saved to disk.

    Parameters
    file_ref   : temp file ID  OR  Discord CDN URL.
    start_line : 1-based first line to return (default 1).
    end_line   : 1-based last line to return inclusive (default 2000).
                 Pass -1 to read to end of file.

    Returns
    dict with:
        "filename"    : original filename
        "content"     : requested slice as a string
        "start_line"  : actual first line returned
        "end_line"    : actual last line returned
        "total_lines" : total number of lines in the file
        "has_more"    : True if lines remain after end_line
    or on error:
        "error" : error message
    """
    # resolve source
    if _is_cdn_url(file_ref):
        try:
            raw = await _download_cdn(file_ref)
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Failed to download from CDN: {e}"}
        original_filename = _cdn_filename(file_ref)
    else:
        file_path, original_filename = _find_temp_file(file_ref)
        if file_path is None:
            return {"error": f"Temp file not found for ID: {file_ref}"}
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

    # slice lines
    all_lines = text.splitlines(keepends=True)
    total = len(all_lines)
    s = max(1, start_line)
    e = total if end_line == -1 else min(end_line, total)
    content = "".join(all_lines[s - 1:e])

    return {
        "filename":    original_filename,
        "content":     content,
        "start_line":  s,
        "end_line":    e,
        "total_lines": total,
        "has_more":    e < total,
    }


def cleanup_files(channel_id: int, file_ids: list[str] | None = None) -> dict:
    """
    Delete staged temp files manually (model-callable).

    Parameters
    channel_id : the Discord channel the files belong to.
    file_ids   : list of temp IDs to delete.
                 Pass an empty list (or omit) to delete EVERY staged file in this channel.

    Returns
    dict with:
        "deleted" : list of file_ids that were successfully removed
        "errors"  : list of {"file_id", "error"} for any failures
    """
    reg = _channel_registry.get(channel_id, {})

    if not file_ids:
        # delete all staged files for this channel
        targets = list(reg.keys())
    else:
        targets = [fid for fid in file_ids if fid in reg or _find_temp_file(fid)[0]]

    deleted = []
    errors = []
    for fid in targets:
        ok = cleanup_temp_file(fid)
        if ok:
            deleted.append(fid)
        else:
            errors.append({"file_id": fid, "error": "File not found or already deleted"})

    # Unregister cleaned files
    unregister_files(channel_id, deleted)
    console.log(f"[edit_text_file] cleanup_files: deleted {len(deleted)}, errors {len(errors)}", "INFO")
    return {"deleted": deleted, "errors": errors}


def move_file(file_id: str, direction: str, channel_id: int | None = None) -> dict:
    """
    Move a file between the temp staging area (database/files/) and the
    persistent database directory (database/files/persistent/).

    Parameters
    file_id    : temp file ID to move.
    direction  : "persist"  — move from temp staging → database/files/persistent/
                              (file survives send_files cleanup, stays on disk long-term)
                 "stage"    — move from persistent storage → temp staging
                              (makes the file available again for edit_file / send_files)
    channel_id : optional, used to re-register the file after staging.

    Returns
    dict with:
        "file_id"  : the same file_id
        "filename" : original filename
        "new_path" : absolute path after move
    or on error:
        "error" : error message
    """
    PERSISTENT_DIR = os.path.join(TEMP_DIR, "persistent")
    os.makedirs(PERSISTENT_DIR, exist_ok=True)

    if direction == "persist":
        file_path, original_filename = _find_temp_file(file_id)
        if file_path is None:
            return {"error": f"Temp file not found for ID: {file_id}"}
        ext = _ext_from_filename(original_filename)
        new_path = os.path.join(PERSISTENT_DIR, f"{file_id}{ext}")
        try:
            os.replace(file_path, new_path)
        except Exception as e:
            return {"error": f"Failed to move to persistent: {e}"}
        # Update meta to reflect new location
        meta = _read_meta(file_id)
        meta["persistent"] = True
        meta["original_filename"] = original_filename
        _write_meta(file_id, meta)
        console.log(f"[edit_text_file] Persisted {original_filename} ({file_id})", "INFO")
        return {"file_id": file_id, "filename": original_filename, "new_path": os.path.abspath(new_path)}

    elif direction == "stage":
        # Look for the file in persistent dir
        meta = _read_meta(file_id)
        original_filename = meta.get("original_filename", "")
        ext = _ext_from_filename(original_filename)
        src = os.path.join(PERSISTENT_DIR, f"{file_id}{ext}")
        if not os.path.exists(src):
            # Fallback: scan persistent dir
            for fname in os.listdir(PERSISTENT_DIR):
                if fname.startswith(file_id) and not fname.endswith(".meta.json"):
                    src = os.path.join(PERSISTENT_DIR, fname)
                    break
            else:
                return {"error": f"Persistent file not found for ID: {file_id}"}
        dest = _temp_path(file_id, os.path.splitext(src)[1])
        try:
            os.replace(src, dest)
        except Exception as e:
            return {"error": f"Failed to move to staging: {e}"}
        meta["persistent"] = False
        _write_meta(file_id, meta)
        if channel_id is not None:
            register_file(channel_id, file_id, original_filename)
        console.log(f"[edit_text_file] Staged {original_filename} ({file_id})", "INFO")
        return {"file_id": file_id, "filename": original_filename, "new_path": os.path.abspath(dest)}

    else:
        return {"error": f"Unknown direction '{direction}'. Use 'persist' or 'stage'."}


async def find_str(
    file_ref: str,
    queries: list[str],
    context_lines: int = 3,
) -> dict:
    """
    Search a file for one or more regex/plain-text patterns and return matches
    with surrounding context lines.

    Parameters
    file_ref      : temp file ID or Discord CDN URL.
    queries       : list of search patterns; each is treated as a Python regex.
                    Plain strings work as literals. Use re flags inline if needed (e.g. (?i)).
    context_lines : number of lines to show before and after each match (default 3).

    Returns
    dict with:
        "filename"    : original filename
        "total_lines" : total number of lines in the file
        "results"     : dict keyed by query string, each value:
            "total_matches" : int
            "matches"       : list of:
                "match_line" : 1-based line number of the match
                "context"    : list of {"line": int, "text": str, "is_match": bool}
            "error"         : (only present if the pattern is invalid regex)
    or on error:
        "error" : error message
    """
    # resolve source
    if _is_cdn_url(file_ref):
        try:
            raw = await _download_cdn(file_ref)
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Failed to download from CDN: {e}"}
        original_filename = _cdn_filename(file_ref)
    else:
        file_path, original_filename = _find_temp_file(file_ref)
        if file_path is None:
            return {"error": f"Temp file not found for ID: {file_ref}"}
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

    all_lines = text.splitlines()
    total = len(all_lines)
    N = max(0, context_lines)

    results: dict = {}
    for pattern in queries:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            results[pattern] = {
                "error": f"Invalid regex: {exc}",
                "total_matches": 0,
                "matches": [],
            }
            continue

        matches = []
        for idx, line in enumerate(all_lines):
            if compiled.search(line):
                start = max(0, idx - N)
                end   = min(total - 1, idx + N)
                context = [
                    {
                        "line":     i + 1,
                        "text":     all_lines[i],
                        "is_match": i == idx,
                    }
                    for i in range(start, end + 1)
                ]
                matches.append({
                    "match_line": idx + 1,
                    "context":    context,
                })

        results[pattern] = {
            "total_matches": len(matches),
            "matches":       matches,
        }

    return {
        "filename":    original_filename,
        "total_lines": total,
        "results":     results,
    }


def cleanup_temp_file(file_id: str) -> bool:
    """
    Manually delete a temp file and its metadata.
    Returns True if the file was found and deleted.
    """
    file_path, _ = _find_temp_file(file_id)
    meta_path    = _meta_path(file_id)
    cleaned      = False

    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            cleaned = True
        except Exception as e:
            console.log(f"[edit_text_file] Failed to delete temp file {file_path}: {e}", "WARN")

    if os.path.exists(meta_path):
        try:
            os.remove(meta_path)
        except Exception as e:
            console.log(f"[edit_text_file] Failed to delete meta {meta_path}: {e}", "WARN")

    return cleaned


def _cleanup_list(pairs: list[tuple[str, str]]):
    """Delete temp files from a list of (file_id, file_path) pairs."""
    for file_id, file_path in pairs:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            console.log(f"[edit_text_file] Cleanup failed for {file_path}: {e}", "WARN")
        meta = _meta_path(file_id)
        if os.path.exists(meta):
            try:
                os.remove(meta)
            except Exception:
                pass


# tool schemas (paste into get_gemini_tools)

TOOL_SCHEMAS = [
    {
        "name": "create_files",
        "description": (
            "Stage one or more text/code files for later editing or uploading. "
            "Use for: creating HTML, JSX, Markdown, Python, JSON, or any text-based file. "
            "Workflow: create_files → (optionally) edit_file → send_files. "
            "Returns a file_id per file — pass these to edit_file or send_files. "
            "Do NOT use for binary output (images, PDFs generated by code) — use run_code with send_output=true for those. "
            "Call read_skills first for document tasks (docx, pdf, pptx, xlsx, visuals)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": "List of files to create. Each item needs filename and content.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Filename with extension, e.g. 'index.html', 'report.md', 'script.py'."
                            },
                            "content": {
                                "type": "string",
                                "description": "Full text content of the file."
                            }
                        },
                        "required": ["filename", "content"]
                    }
                }
            },
            "required": ["files"]
        }
    },
    {
        "name": "edit_file",
        "description": (
            "Edit a staged or Discord-hosted file by exact string replacement. "
            "file_ref accepts a temp file_id (from create_files) or a Discord CDN URL (https://cdn.discordapp.com/...). "
            "old_content must match the file exactly — including all whitespace, indentation, and newlines. "
            "If old_content is not unique and replace_multiple is false (default), returns an error: "
            "either make old_content more specific or set replace_multiple=true to replace all occurrences. "
            "One file per call — make multiple calls for multiple files. "
            "Returns the updated file_id to pass to send_files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_ref": {
                    "type": "string",
                    "description": "Temp file_id from create_files, or a Discord CDN URL."
                },
                "old_content": {
                    "type": "string",
                    "description": "Exact string to find. Must match whitespace and newlines precisely."
                },
                "new_content": {
                    "type": "string",
                    "description": "Replacement string. Can be empty string to delete old_content."
                },
                "replace_multiple": {
                    "type": "boolean",
                    "description": "Replace ALL occurrences of old_content instead of requiring uniqueness. Default false.",
                    "default": False
                },
                "new_filename": {
                    "type": "string",
                    "description": "Rename the file. Omit to keep the existing filename."
                }
            },
            "required": ["file_ref", "old_content", "new_content"]
        }
    },
    {
        "name": "read_file",
        "description": (
            "Read a line range from a staged or Discord-hosted file. Read-only — does not modify anything. "
            "file_ref accepts a temp file_id or a Discord CDN URL. "
            "ALWAYS check 'has_more' in the response — if true, the file has more content; call again with the next start_line. "
            "Use when a Discord attachment displays [FILE TRUNCATED]: call with the CDN URL to read the rest. "
            "start_line and end_line are 1-based and inclusive. Pass end_line=-1 to read to the end of file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_ref": {
                    "type": "string",
                    "description": "Temp file_id or Discord CDN URL of the file to read."
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read, 1-based (default 1).",
                    "default": 1
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to read inclusive, 1-based (default 2000). Pass -1 to read to end.",
                    "default": 2000
                }
            },
            "required": ["file_ref"]
        }
    },
    {
        "name": "cleanup_files",
        "description": (
            "Delete staged temp files that are no longer needed. "
            "Pass specific file_ids to delete only those files. "
            "Pass an empty list to delete ALL staged files in the channel. "
            "Call after send_files completes or when aborting a file task. "
            "Temp files are auto-deleted by send_files after upload — only call this manually for files that won't be sent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "integer",
                    "description": "Discord channel ID the files belong to."
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File IDs to delete. Empty list deletes ALL staged files in the channel."
                }
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "move_file",
        "description": (
            "Move a file between temp staging and persistent storage. "
            "direction='persist' — moves file from temp (database/files/) to persistent storage (database/files/persistent/). "
            "File survives cleanup and bot restarts. Use for files Sensei wants to keep long-term. "
            "direction='stage' — brings a persisted file back into temp staging so it can be edited or re-sent. "
            "Provide channel_id when staging so the file is re-registered and visible in the staged files block."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Temp file_id to move."
                },
                "direction": {
                    "type": "string",
                    "enum": ["persist", "stage"],
                    "description": "'persist' to save long-term, 'stage' to bring back into temp for editing/sending."
                },
                "channel_id": {
                    "type": "integer",
                    "description": "Discord channel ID — required when direction='stage' to re-register the file."
                }
            },
            "required": ["file_id", "direction"]
        }
    },
    {
        "name": "find_str",
        "description": (
            "Search a staged or Discord-hosted file for one or more patterns and return matching lines with context. "
            "file_ref accepts a temp file_id or Discord CDN URL. "
            "Each query in the queries array is a Python regex — plain strings match literally, "
            "or use full regex syntax (e.g. r'def \\w+', r'\\bTODO\\b', '(?i)error'). "
            "Returns matching line numbers and N surrounding lines before/after each hit (context_lines). "
            "Use before edit_file to locate exact text, verify a string exists, or find all occurrences of a pattern."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_ref": {
                    "type": "string",
                    "description": "Temp file_id or Discord CDN URL of the file to search."
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of search patterns. Each is a Python regex. "
                        "Plain strings work as literals. Multiple queries run in one call."
                    )
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context to show before and after each match. Default 3.",
                    "default": 3
                }
            },
            "required": ["file_ref", "queries"]
        }
    },
    {
        "name": "send_files",
        "description": (
            "Upload one or more staged or CDN-hosted files to the Discord channel. "
            "file_refs accepts temp file_ids (from create_files/edit_file) or Discord CDN URLs. "
            "Temp files are automatically deleted after a successful upload. "
            "For supported formats (html, jsx, md, svg, pdf, images, etc.), a preview link is appended automatically — do NOT write preview links manually. "
            "Up to 10 files per Discord message; larger batches are split automatically. "
            "Use filenames to override the uploaded filename (same order as file_refs, omit entries to keep originals)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of temp file_ids or Discord CDN URLs to upload."
                },
                "filenames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional override filenames in the same order as file_refs. Omit to keep original names."
                }
            },
            "required": ["file_refs"]
        }
    }
]