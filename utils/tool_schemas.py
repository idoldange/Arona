from __future__ import annotations
from typing import Union

import discord

from config import DEFAULT_MODEL
from games.chess import chess_manager
from utils import tool_groups

GROUP_DESCRIPTIONS = {
    "chess": "Play chess against Sensei (includes: get_chess_board, make_chess_move, promote_pawn, reset_chess_game, send_chess_board_image).",
    "scheduler": "Schedule messages, reminders, and recurring tasks (includes: schedule_message, schedule_task, schedule_loop, wait_for_time, list/get/edit/delete/clear_user_tasks).",
    "dev": "Code sandbox and file staging (includes: read_skills, run_code, view_workspace_file, cleanup_sandbox, create_files, edit_file, read_file, find_str, send_files, cleanup_files, move_file).",
    "github": "Search or browse GitHub repositories (includes: fetch_github_repo).",
    "blue_archive": "Blue Archive tools. Includes: gacha_tracker and student_birthday. Use gacha_tracker to record Sensei's pull counts when they share them with you. Note: student_birthday is for looking up Blue Archive student birthdays by date, or check if someone has a birthday today. If you want to look up birthdays by name, use 'schaledb_query' instead.",
    "media": "Look up images, videos, and audio (includes: reverse_image_search, youtube, song_recognition, summarize_channel).",
    "todo": "Per-channel task list (includes: todo).",
    "migration": "Discord account migration and linking (includes: get_migration_key, reset_migration_key, link_account, unlink_account). Note: this is for linking Discord accounts so they can share the same data, such as chat history and saved information in Arona's database. It is not for linking game accounts or other external accounts.",
}


def _build_groups(turn_info: str) -> dict[str, list[dict]]:
    return {
        "chess": [
            {"name": "get_chess_board", "description": (
                f"STEP 1 — call first every turn, no exceptions.{turn_info} Returns: FEN, piece positions, legal moves list, turn."
                " After receiving: (1) verify it's Black's turn, (2) pick a move that EXISTS in the legal moves list — any move not in it is illegal,"
                " (3) confirm geometry from FEN (same rank=horizontal, same file=vertical, diff rank+file=diagonal)."
                " Only then call make_chess_move. If tool fails → ask Sensei to reset, never guess."
            ), "parameters": {"type": "object", "properties": {}}},
            {"name": "make_chess_move", "description": f"STEP 2 — submit Arona's move in UCI or SAN format (e.g. e7e5, Nf6). Sensei=White, Arona=Black.{turn_info} Preconditions: get_chess_board called this turn ✓, Black's turn ✓, move in legal list ✓, geometry verified ✓. If pawn lands on rank 1 → call promote_pawn next.", "parameters": {"type": "object", "properties": {"move": {"type": "string"}}, "required": ["move"]}},
            {"name": "promote_pawn", "description": "STEP 3 (conditional) — only after make_chess_move lands Black pawn on rank 1. Pass exact UCI from that move. choice: q(default)/r/b/n.", "parameters": {"type": "object", "properties": {"last_move_uci": {"type": "string"}, "promotion_choice": {"type": "string", "enum": ["q", "r", "b", "n"]}}, "required": ["last_move_uci", "promotion_choice"]}},
            {"name": "reset_chess_game", "description": "Reset board to start. Use when Sensei requests new game, get_chess_board errors, or game is over.", "parameters": {"type": "object", "properties": {}}},
            {"name": "send_chess_board_image", "description": "Send current board as PNG. No move made. Use when Sensei wants to see the board.", "parameters": {"type": "object", "properties": {}}},
        ],

        "scheduler": [
            {"name": "schedule_message", "description": "Schedule a static message in this channel. Confirm timezone → convert to UTC first. timetype: relative=seconds from now, absolute=Unix UTC timestamp.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "send_time": {"type": "number"}, "timetype": {"type": "string", "enum": ["relative", "absolute"]}}, "required": ["content", "send_time", "timetype"]}},
            {"name": "schedule_task", "description": "Schedule a one-shot AI self-trigger. Prompt MUST be fully self-contained (no shared memory with this turn). timetype: relative/absolute. Use schedule_loop for recurring.", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}, "trigger_time": {"type": "number"}, "timetype": {"type": "string", "enum": ["relative", "absolute"]}}, "required": ["prompt", "trigger_time", "timetype"]}},
            {"name": "schedule_loop", "description": "Recurring task. loop_at_time MUST be HH:MM UTC — convert local time first (e.g. 6AM GMT+7 → '23:00'). loop_type: interval(loop_every=seconds)/daily/weekly(loop_at_day=0-6)/monthly(loop_at_day=1-28). action: message=static text, task=AI self-trigger (prompt must be self-contained).", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["message", "task"]}, "loop_type": {"type": "string", "enum": ["interval", "daily", "weekly", "monthly"]}, "content": {"type": "string"}, "loop_every": {"type": "number"}, "loop_at_time": {"type": "string"}, "loop_at_day": {"type": "integer"}, "first_trigger_time": {"type": "number"}, "first_trigger_delay": {"type": "number"}}, "required": ["action", "loop_type", "content"]}},
            {"name": "wait_for_time", "description": "Pause N seconds inside a self-triggered loop only. Never use in a normal message turn. For long waits use schedule_task.", "parameters": {"type": "object", "properties": {"wait_time": {"type": "number"}}, "required": ["wait_time"]}},
            {"name": "list_user_tasks", "description": "List all pending scheduled tasks/messages. Returns IDs, types, times. Call before get_task/edit_task/delete_user_task/clear_user_tasks — they all need an ID from here.", "parameters": {"type": "object", "properties": {}}},
            {"name": "get_task", "description": "Read full content of tasks by ID. Requires IDs from list_user_tasks.", "parameters": {"type": "object", "properties": {"task_ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["task_ids"]}},
            {"name": "edit_task", "description": "Edit one field of a task. Requires task ID from list_user_tasks. One field per call. Time fields must be UTC. field: type/content/trigger_time/loop_type/loop_every/loop_at_time/loop_at_day.", "parameters": {"type": "object", "properties": {"task": {"type": "integer"}, "field": {"type": "string", "enum": ["type", "content", "trigger_time", "loop_type", "loop_every", "loop_at_time", "loop_at_day"]}, "value": {"type": "string"}}, "required": ["task", "field", "value"]}},
            {"name": "delete_user_task", "description": "Delete one task by ID. Requires ID from list_user_tasks. Permanent — confirm intent.", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
            {"name": "clear_user_tasks", "description": "Delete ALL pending tasks. Irreversible. Call list_user_tasks first so Sensei can see what's deleted, then confirm before calling this.", "parameters": {"type": "object", "properties": {}}},
        ],

        "dev": [
            {"name": "read_skills", "description": "Read SKILL.md before specialized tasks. MANDATORY before: docx, pdf, pptx, xlsx, canvas-design, frontend-design, discord-gif-creator, doc-coauthoring, internal-comms, algorithmic-art, coding, web-artifacts-builder, mcp-builder.", "parameters": {"type": "object", "properties": {"skills": {"type": "array", "items": {"type": "string", "enum": ["docx", "pdf", "pptx", "xlsx", "canvas-design", "frontend-design", "discord-gif-creator", "doc-coauthoring", "internal-comms", "algorithmic-art", "coding", "web-artifacts-builder", "mcp-builder", "mc-datapack"]}}}, "required": ["skills"]}},
            {"name": "run_code", "description": "Execute Python/shell in sandbox. ALL output files MUST go to OUTPUT_DIR env var. send_output=true sends files to Discord. temp=true (default) auto-wipes after message. temp=false = persistent per-channel workspace (call cleanup_sandbox when done). Pre-installed Python: pandas, numpy, scipy, sympy, matplotlib, pillow, python-docx, pypdf, reportlab, pymupdf, pdfplumber, python-pptx, openpyxl, xlsxwriter, imageio, requests, aiohttp, cryptography, rembg, opencv (cv2), beautifulsoup4, lxml, duckdb, pyarrow, pyyaml, python-dotenv, tabulate, qrcode, faker. Pre-installed CLI (via run_shell): objdump/nm/readelf/strings (binutils), gdb, upx, file, strace, ltrace, clang/llvm, cmake, p7zip, jq, cfr (Java class/jar decompiler), jadx (Android APK/DEX decompiler), apktool (APK resource/smali), pycdc/pycdas (Python bytecode decompiler, best-effort on 3.9+). Call read_skills first for doc tasks. **Do NOT use to fetch or re-fetch URLs — use web_crawl or web_search instead**. If you want to send the executed code/logs to user, set send_code/send_logs boolean to true (default) instead of write it in your final response. If you want to send output files, set send_output boolean to true (default). VIEW_DIR env var (Python helper: view_file(name, data)) is separate from OUTPUT_DIR: save an image/audio/video file there (e.g. a chart you just generated, an attachment you downloaded, a frame extracted from a video) and it gets attached back to you as real inline content (not just a filename) so you can actually see/hear it before replying. Limits: image ≤8MB, audio/video ≤15MB, max 4 files per call — not for sending files to the user, that's still OUTPUT_DIR/send_output. The sandbox has internet access.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["run_code", "run_shell"]}, "code": {"type": "string"}, "shell_cmd": {"type": "string"}, "send_output": {"type": "boolean", "default": True}, "send_code": {"type": "boolean", "default": True}, "send_logs": {"type": "boolean", "default": True}, "timeout": {"type": "integer", "default": 60}, "temp": {"type": "boolean", "default": False}}, "required": ["action", "temp"]}},
            {"name": "view_workspace_file", "description": "See/hear an image/audio/video file that already exists in this channel's PERSISTENT (temp=false) run_code workspace, without writing/re-running any code. Looks in the workspace root, outputs/, and view/ subfolders for an exact filename match. Use this when a file from an earlier run_code(temp=false) call — e.g. a downloaded attachment, a previously generated chart, an extracted video frame — is already sitting in the workspace and you just need to look at/listen to it now. For files you just created THIS call, keep using VIEW_DIR inside run_code instead. Limits: image ≤8MB, audio/video ≤15MB. Only works with temp=false workspaces (persistent, keyed by channel).", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "Exact filename to look up (as it appears in the workspace, e.g. 'chart.png')."}}, "required": ["filename"]}},
            {"name": "cleanup_sandbox", "description": "Wipe persistent sandbox workspace (temp=false). Call after multi-step task is complete and outputs sent. Don't call mid-task.", "parameters": {"type": "object", "properties": {}}},
            {"name": "create_files", "description": "Stage text/code files for editing or sending. Returns file_id per file. Chain: create_files → edit_file (refine) → send_files (deliver). Write complete content — no empty stubs.", "parameters": {"type": "object", "properties": {"files": {"type": "array", "items": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}}, "required": ["files"]}},
            {"name": "edit_file", "description": "Replace exact text in a staged file (file_id) or Discord Attachment(CDN URL). old_content must match precisely including whitespace. If not unique set replace_multiple=true or provide a more unique old_content. One file per call. If unsure of content: call read_file first.", "parameters": {"type": "object", "properties": {"file_ref": {"type": "string"}, "old_content": {"type": "string"}, "new_content": {"type": "string"}, "replace_multiple": {"type": "boolean", "default": False}, "new_filename": {"type": "string"}}, "required": ["file_ref", "old_content", "new_content"]}},
            {"name": "read_file", "description": "Read lines from a staged file or CDN URL. Check has_more — if true, call again with updated start_line. Use when attachment says [FILE TRUNCATED] or before editing to verify exact content.", "parameters": {"type": "object", "properties": {"file_ref": {"type": "string"}, "start_line": {"type": "integer", "default": 1}, "end_line": {"type": "integer", "default": 2000}}, "required": ["file_ref"]}},
            {"name": "find_str", "description": "Search a staged file or CDN URL for one or more patterns and return matching lines with context. Each query is a Python regex (plain strings match literally, e.g. 'def foo', r'\\bclass\\b', '(?i)error'). Returns match line numbers and N surrounding lines. Use before edit_file to locate exact text, or to check if a string exists.", "parameters": {"type": "object", "properties": {"file_ref": {"type": "string", "description": "Temp file_id or Discord CDN URL."}, "queries": {"type": "array", "items": {"type": "string"}, "description": "List of Python regex patterns. Multiple queries run in one call."}, "context_lines": {"type": "integer", "default": 3, "description": "Lines of context before and after each match. Model chooses; default 3."}}, "required": ["file_ref", "queries"]}},
            {"name": "send_files", "description": "Upload staged files or CDN URLs to Discord. Max 10 per call. Temp staged files auto-deleted after send. Preview links auto-appended for html/md.", "parameters": {"type": "object", "properties": {"file_refs": {"type": "array", "items": {"type": "string"}}, "filenames": {"type": "array", "items": {"type": "string"}}}, "required": ["file_refs"]}},
            {"name": "cleanup_files", "description": "Delete staged files by file_id. Empty list = delete ALL staged files in channel.", "parameters": {"type": "object", "properties": {"file_ids": {"type": "array", "items": {"type": "string"}}}, "required": []}},
            {"name": "move_file", "description": "Move between staging and persistent storage. direction: persist=save long-term, edit through `edit_file`, read through `read_file`, and send through `send_files`. stage=bring to `run_code` workspace.", "parameters": {"type": "object", "properties": {"file_id": {"type": "string"}, "direction": {"type": "string", "enum": ["persist", "stage"]}}, "required": ["file_id", "direction"]}},
        ],

        "github": [
            {"name": "fetch_github_repo", "description": "Browse GitHub. Workflow: search → info → get_tree (paginated via tree_offset/tree_limit) → find_string (±10 lines) → read_files (max 10, use line_ranges to limit) → commits (list commit history). action: search/info/get_tree/find_string/read_files/commits.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["search", "info", "get_tree", "tree", "read_files", "find_string", "commits"]}, "query": {"type": "string"}, "url": {"type": "string"}, "urls_list": {"type": "array", "items": {"type": "string"}}, "line_ranges": {"type": "array", "items": {"oneOf": [{"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, {"type": "null"}]}}, "tree_offset": {"type": "integer", "default": 0}, "tree_limit": {"type": "integer", "default": 200}}, "required": ["action"]}},
        ],

        "blue_archive": [
            {"name": "gacha_tracker", "description": "Track gacha pulls/pity/sparks. Call status first before logging. action: status=read, add=log pulls (got_pickup=true resets pity), add_shards, reset=new banner, all=all banners.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "add", "add_shards", "reset", "all"]}, "count": {"type": "integer"}, "banner": {"type": "string", "default": "current"}, "got_pickup": {"type": "boolean", "default": False}, "got_3star": {"type": "boolean", "default": False}}, "required": ["action"]}},
            {"name": "student_birthday", "description": "BA student birthday lookup. action: find=named student (needs name), today=today's birthdays (UTC), date=by month+day.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["find", "today", "date"]}, "name": {"type": "string"}, "month": {"type": "integer"}, "day": {"type": "integer"}}, "required": ["action"]}},
        ],

        "media": [
            {"name": "reverse_image_search", "description": "Find image source by URL. `image_url` MUST be the exact URL copied verbatim from the [Attachment: <filename> | URL: <url>] tag in the current message parts — character-for-character. If no such tag exists in the current message, do not call this tool. Never construct, guess, recall from memory, or modify any URL.", "parameters": {"type": "object", "properties": {"image_url": {"type": "string"}, "crawl_per_query": {"type": "integer", "default": 3}, "max_chars_per_page": {"type": "integer", "default": 10000}}, "required": ["image_url"]}},
            {"name": "youtube", "description": "Fetch YouTube info. MUST call before discussing/summarising any YT link. url: full URL, must in format https://www.youtube.com/watch?v=VIDEO_ID or https://youtu.be/VIDEO_ID. action: info=metadata only, transcript=subtitles only, full=both (default for summaries). lang=preferred language code, chars_limit=max transcript chars, 0 for no limit.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "action": {"type": "string", "enum": ["info", "transcript", "full"], "default": "full"}, "lang": {"type": "string"}, "chars_limit": {"type": "integer", "default": 8000}}, "required": ["url"]}},
            {"name": "song_recognition", "description": "Identify a song from an audio/video direct download URL. URL must come from [Attachment: filename | URL: url].", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
            {"name": "summarize_channel", "description": "Read and summarize recent messages from ALL users in this channel. Use for: 'what happened', 'catch me up', 'what was said recently', date-range summaries, or any channel-wide recap. Do NOT use fetch_history for these — fetch_history only has this Sensei's messages. topic=keyword filter, timeline=add timestamps, deep=read attachments too.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 100}, "topic": {"type": "string"}, "timeline": {"type": "boolean", "default": False}, "deep": {"type": "boolean", "default": False}}, "required": []}},
        ],

        "todo": [
            {"name": "todo", "description": "Per-channel task list. NEVER echo content — embed renders automatically. action: create (needs content=[list of items]), done (needs content=[items to mark done]), edit (needs old_content + new_content to rename one item).", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["create", "done", "edit"]}, "content": {"type": "array", "items": {"type": "string"}, "description": "For create: list of task strings. For done: list of task strings to mark complete."}, "old_content": {"type": "string", "description": "For edit: exact text of item to replace."}, "new_content": {"type": "string", "description": "For edit: new text to replace it with."}}, "required": ["action"]}},
        ],

        "migration": [
            {"name": "get_migration_key", "description": "Show Sensei their migration key via a private 'Click to reveal' embed — key never appears in chat. WHEN TO USE: Sensei wants to link a NEW account to THIS account (this is the OLD/source account). Workflow: (1) Call this on the OLD account to get the key. (2) On the NEW account, call link_account with this account's Discord ID + that key. Guide Sensei through both steps if they ask about migration. Never state, guess, or repeat any key or ID yourself.", "parameters": {"type": "object", "properties": {}}},
            {"name": "reset_migration_key", "description": "Send a confirmation embed to reset Sensei's migration key. The old key is invalidated immediately — any pending link_account attempt using it will fail. Use only when Sensei explicitly asks to reset/regenerate their key (e.g. key was leaked or lost). After reset, Sensei must call get_migration_key again to retrieve the new key before linking. Never state the old or new key value yourself.", "parameters": {"type": "object", "properties": {}}},
            {"name": "link_account", "description": "Link THIS account (new) to an OLD account so they share the same data. Prerequisites: Sensei must first call get_migration_key ON THE OLD ACCOUNT to obtain its key. Then call this tool here on the new account with that old account's Discord ID + key. Merge behavior: saved_info from new account merged into root (conflicts skipped), message history merged by recency, impressions merged. After linking, all data reads/writes on this account transparently use the root (oldest) account's data — chained links always resolve to the furthest root. To undo: call unlink_account.", "parameters": {"type": "object", "properties": {"source_discord_id": {"type": "string", "description": "Discord user ID of the old account to link to."}, "key": {"type": "string", "description": "Migration key from the old account. Always ask users for this. Never state, guess, or repeat any key or ID yourself."}}, "required": ["source_discord_id", "key"]}},
            {"name": "unlink_account", "description": "Unlink this account from its root. Copies saved_info + message history FROM root INTO this account (conflicts and system keys skipped), then severs the link. After unlinking: this account has its own independent data copy, root account is untouched, and future writes go only to this account. Use when Sensei wants to fully separate from a previously linked account. Cannot be undone — to re-link, use link_account again.", "parameters": {"type": "object", "properties": {}}},
        ],
    }


# group_name -> list of tool dicts, built once at import time (no message-specific content here, so no need to rebuild per-call). chess tools embed turn_info but that's regenerated separately in get_gemini_tools below for freshness.
TOOL_GROUPS: dict[str, list[dict]] = _build_groups(turn_info="")


def _group_meta_tools() -> list[dict]:
    group_list = ", ".join(f"{name} ({desc})" for name, desc in GROUP_DESCRIPTIONS.items())
    return [
        {
            "name": "load_tools",
            "description": (
                "Load one or more tool groups so their functions become callable. "
                f"Available groups: {group_list}. "
                "A loaded group stays available for the next 5 incoming Sensei messages in this channel "
                "(reloading refreshes the counter), then auto-unloads to keep the tool list lean. "
                "Call this BEFORE attempting to use a tool from a group that isn't already loaded — "
                "calling an unloaded tool will fail. Don't load groups speculatively; only load what this turn actually needs."
            ),
            "parameters": {"type": "object", "properties": {
                "groups": {"type": "array", "items": {"type": "string", "enum": list(GROUP_DESCRIPTIONS.keys())}}
            }, "required": ["groups"]},
        },
        {
            "name": "unload_tools",
            "description": "Immediately unload tool groups before their 5-message TTL expires (e.g. task is done, freeing context). Optional housekeeping — never required.",
            "parameters": {"type": "object", "properties": {
                "groups": {"type": "array", "items": {"type": "string", "enum": list(GROUP_DESCRIPTIONS.keys())}}
            }, "required": ["groups"]},
        },
    ]


def get_gemini_tools(
    message: Union[discord.Message, None] = None,
    model_name: str = DEFAULT_MODEL,
) -> list[dict]:

    turn_info = ""
    if message and hasattr(message, "channel"):
        turn_info = f" Turn: {chess_manager.get_turn(channel_id=message.channel.id)}."

    # core — always present
    core_tools = [
        # web
        {"name": "web_search", "description": "Real-time web search. Pass multiple queries at once for parallel results. Skip for stable facts already known. search_type: text (default, general pages, crawled for full content), news (recent articles w/ date+source, crawled), videos (metadata only: title/publisher/duration/embed_url, no crawl), images (metadata only: direct image url/thumbnail/source page, no crawl — result already includes a ![alt](url) markdown line, no need to search crawled content for it). crawl_per_query/max_chars_per_page only apply to text/news.", "parameters": {"type": "object", "properties": {"query": {"type": "array", "items": {"type": "string"}}, "search_type": {"type": "string", "enum": ["text", "news", "videos", "images"], "default": "text"}, "crawl_per_query": {"type": "integer", "default": 3}, "max_chars_per_page": {"type": "integer", "default": 10000}}, "required": ["query"]}},
        {"name": "web_crawl", "description": "Fetch full page content as Markdown. Parallel supported. Cannot crawl cdn.discordapp.com — use read_file for Discord attachments.", "parameters": {"type": "object", "properties": {"url": {"type": "array", "items": {"type": "string"}}, "max_chars_per_page": {"type": "integer", "default": 15000}}, "required": ["url"]}},

        # memory
        {"name": "saved_information", "description": "Sensei-scoped key-value store. Keys: snake_case. Values: plain string, no newlines. Use proactively when Sensei shares a preference, name, timezone, etc. action: add/edit/delete.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "edit", "delete"]}, "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["action", "key"]}},
        {"name": "rag_save", "description": "Save a fact or summary to long-term semantic memory. Use for multi-sentence context not suited to key-value. Write complete sentences so the content is useful without context.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
        {"name": "rag_query", "description": "Semantic search over long-term memory. Call BEFORE claiming you don't remember something. Returns top matches with doc_ids — pass a doc_id to rag_delete to remove.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "num_results": {"type": "integer", "default": 3}}, "required": ["query"]}},
        {"name": "rag_delete", "description": "Delete a memory doc. Requires doc_id from rag_query. Permanent — confirm intent first.", "parameters": {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]}},
        {"name": "channel_memory", "description": "Channel-scoped freeform memory. Memory is automatically injected into the prompt. Use this tool only to append, overwrite, or clear channel memory.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["set", "append", "clear"]}, "content": {"type": "string"}}, "required": ["action"]}},
        {"name": "guild_memory", "description": "Guild-scoped freeform memory. Memory is automatically injected into the prompt. Use this tool only to append, overwrite, or clear guild memory.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["set", "append", "clear"]}, "content": {"type": "string"}}, "required": ["action"]}},
        {"name": "fetch_history", "description": "Search THIS Sensei's personal message history with Arona (not the whole channel). get_recent=latest N messages from this Sensei (default 50, max 300); search=keyword match in this Sensei's history only (default 10, max 50, searches scope 600). For recent channel activity, catching up on what was said, or date-range summaries → use summarize_channel or load_more_context instead.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["get_recent", "search"]}, "query": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["action"]}},
        {"name": "load_more_context", "description": "Load up to 50 raw channel messages into context as proper conversation history. Use when the current context window is too short to answer accurately. Replaces the initial context window with a larger batch.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}}, "required": []}},

        # profile & misc
        {"name": "read_profile", "description": "Read Sensei's Discord profile: roles, activities, @mention tag. Use before scheduling or role-gated actions.", "parameters": {"type": "object", "properties": {}}},
        {"name": "search_member", "description": "Search for a server member by name or display name. Returns matching members with mention tag, roles, and basic info. Use when asked if someone is in this server or to find/tag a member.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}}, "required": ["query"]}},
        {"name": "search_guild", "description": "Search Arona's joined servers by name or description. Returns matching guilds with their guild_id, member count, and description. Use this first to find a guild_id before calling guild_info.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]}},
        {"name": "guild_info", "description": "Get detailed info about a server: member/bot count, channel & category tree, roles, description. guild_id is OPTIONAL — omit to use the current server. To get another server's guild_id, call search_guild first.", "parameters": {"type": "object", "properties": {"guild_id": {"type": "string", "description": "Target guild ID from search_guild. Omit to use the current server."}}}},
        {"name": "weather_search", "description": "Current weather for a location. Pass 'City, Country' for disambiguation.", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}},
        {"name": "ask_user", "description": "Send Sensei an interactive question and block for their reply. Use only for genuine ambiguity that can't be resolved from context. Don't call more than once per turn. Modes: no choices+allow_text=true → free text; choices+allow_text=false → buttons only; choices+allow_text=true → buttons + text fallback (label: other_label).", "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "choices": {"type": "array", "items": {"type": "string"}}, "allow_text_input": {"type": "boolean", "default": True}, "other_label": {"type": "string"}}, "required": ["question"]}},
        {"name": "send_feedback", "description": "Send a report to the dev channel. Call ask_user for consent first — username is included. type: feedback/bug/suggestion.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "type": {"type": "string", "enum": ["feedback", "bug", "suggestion"], "default": "feedback"}}, "required": ["content"]}},
        {"name": "schaledb_query", "description": "Blue Archive data ONLY — only call for names/content that are clearly BA-related. For unfamiliar names that could be outside BA (VTubers, streamers, other games, real people), use web_search instead. Actions: student, search_students, banners, events, raids, search_items, equipment, find_drop, by_role, by_school, by_attack, raid_team, gear, compare, roster. Region: global(default)/japan. For ambiguous BA names: search_students first, then student.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["banners", "events", "raids", "student", "search_students", "search_items", "equipment", "find_drop", "by_role", "by_school", "by_attack", "raid_team", "gear", "compare", "roster"]}, "query": {"type": "string"}, "query2": {"type": "string"}, "region": {"type": "string", "default": "global"}, "limit": {"type": "integer", "default": 10}}, "required": ["action"]}}
    ]

    # final tool list
    is_voice = message is None
    tools = list(core_tools)

    # Groups that were unconditionally available even in voice sessions before this refactor
    # (they used to live in `shared_tools`). Lazy-loading only applies to text channels, since
    # voice has no per-channel message stream to tick a TTL against.
    VOICE_ALWAYS_ON_GROUPS = ("chess", "scheduler", "blue_archive", "migration")

    if is_voice:
        always_on = _build_groups(turn_info="")  # no live channel turn_info available in voice
        for group_name in VOICE_ALWAYS_ON_GROUPS:
            tools += always_on.get(group_name, [])
    else:
        # load_tools/unload_tools only make sense with a real channel to track TTL against
        tools += _group_meta_tools()
        channel_id = message.channel.id
        loaded = tool_groups.get_loaded_groups(channel_id)
        if loaded:
            # Only chess needs a freshly-built declaration (live turn_info baked into its description);
            # everything else can reuse the static TOOL_GROUPS built at import time.
            for group_name in loaded:
                if group_name == "chess":
                    tools += _build_groups(turn_info)["chess"]
                else:
                    tools += TOOL_GROUPS.get(group_name, [])

    #if message:
    #    if isinstance(getattr(message.author, "voice", None), discord.VoiceState) and message.author.voice.channel:
    #        tools.append({"name": "join_voice", "description": "Join Sensei's voice channel. Only call when explicitly asked.", "parameters": {"type": "object", "properties": {}, "required": []}})
    #    if message.guild and message.guild.voice_client and message.guild.voice_client.is_connected():
    #        tools.append({"name": "leave_voice", "description": "Disconnect from voice channel. farewell_message sent to text channel after leaving.", "parameters": {"type": "object", "properties": {"farewell_message": {"type": "string"}}, "required": []}})
    #else:
    #    # Voice session — append voice-specific tools
    #    tools.append({"name": "leave_voice", "description": "Disconnect from voice channel. farewell_message sent to text channel after leaving.", "parameters": {"type": "object", "properties": {"farewell_message": {"type": "string"}}, "required": []}})
    #    tools.append({"name": "send_text_message", "description": "Send text to the associated text channel during a voice session. Use for links, code, anything awkward spoken aloud.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}})
    if message:
        if isinstance(getattr(message.author, "voice", None), discord.VoiceState) and message.author.voice.channel:
            tools.append({"name": "join_voice", "description": "Join Sensei's voice channel. Currently not working, don't call.", "parameters": {"type": "object", "properties": {}, "required": []}})
        if message.guild and message.guild.voice_client and message.guild.voice_client.is_connected():
            tools.append({"name": "leave_voice", "description": "Disconnect from voice channel. farewell_message sent to text channel after leaving. Currently not working, don't call.", "parameters": {"type": "object", "properties": {"farewell_message": {"type": "string"}}, "required": []}})
    else:
        # Voice session — append voice-specific tools
        tools.append({"name": "leave_voice", "description": "Disconnect from voice channel. farewell_message sent to text channel after leaving. Currently not working, don't call.", "parameters": {"type": "object", "properties": {"farewell_message": {"type": "string"}}, "required": []}})
        tools.append({"name": "send_text_message", "description": "Send text to the associated text channel during a voice session. Use for links, code, anything awkward spoken aloud.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}})
    #wait for lib update
    if model_name == DEFAULT_MODEL:
        # escalate thinking depth
        tools.append({"name": "escalate", "description": "Boost thinking depth for this response. Trigger: complex reasoning, math, chess analysis, code architecture, or when Sensei explicitly says 'full power' / 'think harder'. level: 'medium' = solid boost, 'high' = maximum.", "parameters": {"type": "object", "properties": {"level": {"type": "string", "enum": ["medium", "high"]}}, "required": ["level"]}})
    return [{"function_declarations": tools}]