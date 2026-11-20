"""
Generates the small Discord status line shown while Arona executes a tool
(e.g. "-# <:web_search:…> Searching for 'cats'…").

Add a new elif block here whenever a new tool is registered in tool_schemas.py.
"""


def get_function_execution_message(func_name: str, func_args: dict) -> str:
    """Return a human-readable Discord status line for a pending tool call."""

    # web
    if func_name == "web_search":
        queries = func_args.get("query", [])
        if isinstance(queries, list):
            #queries = queries[:2] #no need
            query_str = ", ".join(f"'{q}'" for q in queries)
        else:
            query_str = f"'{queries}'"
        return f"-# <:web_search:1484030926995587132> Searching for {query_str}..."

    elif func_name == "reverse_image_search":
        image_url = func_args.get("image_url", "")
        filename = image_url.split("/")[-1].split("?")[0] if image_url else "image"
        return f"-# <:web_search:1484030926995587132> Reverse image searching for {filename}..."

    elif func_name == "web_crawl":
        urls = func_args.get("url", [])
        if isinstance(urls, list) and urls:
            url_display = urls[0].split("/")[2] if urls[0].startswith("http") else urls[0]
            return f"-# <:web_crawl:1484030925342904450> Fetching from {url_display}..."
        return "-# <:web_crawl:1484030925342904450> Reading page content..."

    elif func_name == "weather_search":
        location = func_args.get("location", "")
        if location:
            return f"-# <:weather:1484030923669372928> Fetching weather for {location}..."
        return "-# <:weather:1484030923669372928> Fetching weather..."

    elif func_name == "youtube":
        action = func_args.get("action", "full")
        url = func_args.get("url", "")
        video_id = url.split("v=")[-1].split("&")[0].split("/")[-1][:11] if url else ""
        vid_tag = f" `{video_id}`" if video_id else ""
        if action == "info":
            return f"-# <:web_crawl:1484030925342904450> Fetching YouTube metadata{vid_tag}..."
        elif action == "transcript":
            lang = func_args.get("lang", "")
            lang_tag = f" [{lang}]" if lang else ""
            return f"-# <:web_crawl:1484030925342904450> Fetching transcript{lang_tag}{vid_tag}..."
        return f"-# <:web_crawl:1484030925342904450> Fetching YouTube video info{vid_tag}..."

    # code sandbox
    elif func_name == "run_code":
        action = func_args.get("action", "run_code")
        if action == "run_shell":
            return "-# <:run_shell:1484030905503842324> Running shell command..."
        return "-# <:run_code:1484030903297511535> Running Python code..."

    elif func_name == "view_workspace_file":
        fname = func_args.get("filename", "")
        name_tag = f" `{fname}`" if fname else ""
        return f"-# <:run_code:1484030903297511535> Peeking at{name_tag}..."

    elif func_name == "cleanup_sandbox":
        return "-# <:run_code:1484030903297511535> Wiping sandbox workspace..."

    elif func_name == "read_skills":
        skills = func_args.get("skills", [])
        if skills:
            names = ", ".join(skills)
            return f"-# <:read_skills:1484030901020131349> Reading skill docs: {names}..."
        return "-# <:read_skills:1484030901020131349> Reading skill documentation..."

    # file ops
    elif func_name == "read_file":
        ref = func_args.get("file_ref", "")
        start = func_args.get("start_line", 1)
        end = func_args.get("end_line", 2000)
        label = (ref[:8] + "...") if len(ref) > 12 else ref
        return f"-# <:read_file:1484030897463230708> Reading file `{label}` (lines {start}–{end})..."

    elif func_name == "create_files":
        files = func_args.get("files", [])
        if files:
            names = ", ".join(f.get("filename", "file") for f in files[:3])
            suffix = f" and {len(files) - 3} more" if len(files) > 3 else ""
            return f"-# <:create_files:1484030875434881096> Creating {names}{suffix}..."
        return "-# <:create_files:1484030875434881096> Creating files..."

    elif func_name == "edit_file":
        ref = func_args.get("file_ref", "")
        fname = ref.split("/")[-1].split("?")[0] if ref.startswith("http") else ref[:8] + "..."
        new_name = func_args.get("new_filename")
        if new_name:
            return f"-# <:edit_file:1484030881965543536> Editing and renaming to {new_name}..."
        return f"-# <:edit_file:1484030881965543536> Editing {fname}..."

    elif func_name == "find_str":
        queries = func_args.get("queries", [])
        ref = func_args.get("file_ref", "")
        label = (ref[:8] + "...") if len(ref) > 12 else ref
        if queries:
            q_str = ", ".join(f"'{q}'" for q in queries[:2])
            return f"-# <:read_file:1484030897463230708> Searching `{label}` for {q_str}..."
        return f"-# <:read_file:1484030897463230708> Searching `{label}`..."

    elif func_name == "send_files":
        count = len(func_args.get("file_refs", []))
        return f"-# <:send_files:1484030914836037632> Presenting {count} file{'s' if count != 1 else ''}..."

    elif func_name == "cleanup_files":
        ids = func_args.get("file_ids", [])
        if ids:
            return f"-# <:cleanup_files:1484030874147360848> Cleaning up {len(ids)} staged file(s)..."
        return "-# <:cleanup_files:1484030874147360848> Cleaning up all staged files..."

    elif func_name == "move_file":
        direction = func_args.get("direction", "")
        fid = func_args.get("file_id", "")[:8]
        if direction == "persist":
            return f"-# <:move_file_persist:1484030891947724900> Persisting file `{fid}...`..."
        return f"-# <:move_file_stage:1484030893592150066> Staging file `{fid}...`..."

    # memory
    elif func_name == "saved_information":
        action = func_args.get("action", "")
        action_map = {"add": "Adding", "edit": "Updating", "delete": "Deleting"}
        action_text = action_map.get(action, action.capitalize())
        return f"-# <:user_memory:1484030921391870075> {action_text} saved information..."

    elif func_name == "rag_save":
        return "-# <:rag:1484030895441711284> Saving to memory..."

    elif func_name == "rag_query":
        query = func_args.get("query", "")
        if query:
            return f"-# <:rag:1484030895441711284> Querying memory for '{query}'..."
        return "-# <:rag:1484030895441711284> Querying memory..."

    elif func_name == "rag_delete":
        doc_id = func_args.get("doc_id", "")
        label = f" `{doc_id[:8]}...`" if doc_id else ""
        return f"-# <:rag:1484030895441711284> Deleting memory{label}..."

    elif func_name == "channel_memory":
        action = func_args.get("action", "get")
        if action == "get":
            return "-# <:user_memory:1484030921391870075> Reading channel memory..."
        elif action in ("set", "append"):
            return "-# <:user_memory:1484030921391870075> Updating channel memory..."
        elif action == "clear":
            return "-# <:user_memory:1484030921391870075> Clearing channel memory..."
        return "-# <:user_memory:1484030921391870075> Managing channel memory..."

    elif func_name == "guild_memory":
        action = func_args.get("action", "get")
        if action == "get":
            return "-# <:user_memory:1484030921391870075> Reading guild memory..."
        elif action in ("set", "append"):
            return "-# <:user_memory:1484030921391870075> Updating guild memory..."
        elif action == "clear":
            return "-# <:user_memory:1484030921391870075> Clearing guild memory..."
        return "-# <:user_memory:1484030921391870075> Managing guild memory..."

    elif func_name == "fetch_history":
        action = func_args.get("action", "")
        if action == "search":
            return "-# <:fetch_history:1484030886914556065> Searching chat history..."
        return "-# <:fetch_history:1484030886914556065> Fetching recent history..."

    elif func_name == "load_more_context":
        limit = func_args.get("limit", 30)
        return f"-# <:fetch_history:1484030886914556065> Loading {limit} more channel messages..."

    # blue archive
    elif func_name == "schaledb_query":
        query = func_args.get("query", func_args.get("action", ""))
        if query:
            return f"-# <:schaledb:1484030907584090183> Querying Schale database for '{query}'..."
        return "-# <:schaledb:1484030907584090183> Querying Schale database..."

    elif func_name == "student_birthday":
        action = func_args.get("action", "")
        if action == "find":
            name = func_args.get("name", "")
            return f"-# <:schaledb:1484030907584090183> Looking up {name}'s birthday..." if name else "-# <:schaledb:1484030907584090183> Looking up student birthday..."
        elif action == "today":
            return "-# <:schaledb:1484030907584090183> Checking today's birthdays..."
        elif action == "date":
            month = func_args.get("month")
            day = func_args.get("day")
            if month and day:
                return f"-# <:schaledb:1484030907584090183> Checking birthdays on {month}/{day}..."
            return "-# <:schaledb:1484030907584090183> Checking birthdays by date..."
        return "-# <:schaledb:1484030907584090183> Checking student birthdays..."

    elif func_name == "gacha_tracker":
        action = func_args.get("action", "status")
        banner = func_args.get("banner", "current")
        if action == "status":
            return f"-# <:schaledb:1484030907584090183> Checking gacha status for `{banner}`..."
        elif action == "add":
            count = func_args.get("count", 1)
            return f"-# <:schaledb:1484030907584090183> Logging {count} pull(s)..."
        elif action == "add_shards":
            return "-# <:schaledb:1484030907584090183> Adding shards..."
        elif action == "reset":
            return f"-# <:schaledb:1484030907584090183> Resetting banner `{banner}`..."
        return "-# <:schaledb:1484030907584090183> Gacha tracker..."

    # github
    elif func_name == "fetch_github_repo":
        action = func_args.get("action", "")
        query = func_args.get("query", "")
        if action == "search" and query:
            return f"-# <:fetch_github_repo:1484030885366988840> Searching GitHub for '{query}'..."
        elif action == "info":
            url = func_args.get("url", "")
            repo_name = url.split("/")[-1] if url else "repo"
            return f"-# <:fetch_github_repo:1484030885366988840> Fetching GitHub repo info for {repo_name}..."
        elif action in ("get_tree", "tree"):
            return "-# <:fetch_github_repo:1484030885366988840> Fetching repository file tree..."
        elif action == "read_files":
            return "-# <:fetch_github_repo:1484030885366988840> Reading GitHub files..."
        elif action == "find_string":
            return "-# <:fetch_github_repo:1484030885366988840> Searching GitHub code..."
        elif action == "commits":
            return "-# <:fetch_github_repo:1484030885366988840> Fetching commit history..."
        return "-# <:fetch_github_repo:1484030885366988840> Fetching GitHub data..."

    # chess
    elif func_name == "get_chess_board":
        return "-# <:chess:1484030877318250567> Getting chess board..."

    elif func_name == "make_chess_move":
        move = func_args.get("move", "")
        if move:
            return f"-# <:chess:1484030877318250567> Making chess move: {move}..."
        return "-# <:chess:1484030877318250567> Making chess move..."

    elif func_name == "promote_pawn":
        return "-# <:chess:1484030877318250567> Promoting pawn..."

    elif func_name == "reset_chess_game":
        return "-# <:chess:1484030877318250567> Resetting chess game..."

    elif func_name == "send_chess_board_image":
        return "-# <:chess:1484030877318250567> Sending chess board..."

    # scheduler
    elif func_name == "schedule_message":
        return "-# <:schedule:1484030910960767137> Scheduling a message..."

    elif func_name == "schedule_task":
        return "-# <:schedule:1484030910960767137> Scheduling a task..."

    elif func_name == "schedule_loop":
        loop_type = func_args.get("loop_type", "")
        action = func_args.get("action", "")
        kind = "recurring task" if action == "task" else "recurring message"
        type_tag = f" [{loop_type}]" if loop_type else ""
        return f"-# <:schedule:1484030910960767137> Scheduling {kind}{type_tag}..."

    elif func_name == "list_user_tasks":
        return "-# <:schedule:1484030910960767137> Checking your scheduled tasks..."

    elif func_name == "delete_user_task":
        return f"-# <:schedule:1484030910960767137> Deleting task ID {func_args.get('task_id')}..."

    elif func_name == "clear_user_tasks":
        return "-# <:schedule:1484030910960767137> Clearing all your scheduled tasks..."

    elif func_name == "get_task":
        ids = func_args.get("task_ids", [])
        ids_str = ", ".join(str(i) for i in ids) if ids else "?"
        return f"-# <:schedule:1484030910960767137> Fetching task(s) {ids_str}..."

    elif func_name == "edit_task":
        task = func_args.get("task")
        field = func_args.get("field")
        return f"-# <:schedule:1484030910960767137> Editing task {task} [{field}]..."

    elif func_name == "wait_for_time":
        return f"-# <:schedule:1484030910960767137> Waiting for {func_args.get('wait_time')} seconds..."

    # daemon
    elif func_name == "start_daemon":
        name = func_args.get("name", "")
        cmd = func_args.get("cmd", "")
        name_tag = f" `{name}`" if name else ""
        cmd_preview = cmd[:30] + ("..." if len(cmd) > 30 else "")
        return f"-# <:schedule:1484030910960767137> Starting daemon{name_tag}: {cmd_preview}..."

    elif func_name == "read_daemon_output":
        did = func_args.get("id", "")
        lines = func_args.get("lines", 100)
        return f"-# <:schedule:1484030910960767137> Reading output from daemon `{did}` (last {lines} lines)..."

    elif func_name == "send_daemon_input":
        did = func_args.get("id", "")
        return f"-# <:schedule:1484030910960767137> Sending input to daemon `{did}`..."

    elif func_name == "stop_daemon":
        did = func_args.get("id", "")
        return f"-# <:schedule:1484030910960767137> Stopping daemon `{did}`..."

    elif func_name == "daemon_status":
        did = func_args.get("id", "")
        return f"-# <:schedule:1484030910960767137> Checking status of daemon `{did}`..."

    elif func_name == "list_daemons":
        return "-# <:schedule:1484030910960767137> Listing daemons..."

    # presence
    elif func_name == "fetch_current_presence":
        return "-# <:read_profile:1484030899531026593> Fetching current presence..."

    elif func_name == "fetch_guild_presence_summary":
        return "-# <:read_profile:1484030899531026593> Fetching guild presence summary..."

    elif func_name == "fetch_presence_history":
        limit = func_args.get("limit", 25)
        return f"-# <:read_profile:1484030899531026593> Fetching presence history (last {limit})..."

    elif func_name == "fetch_presence_info":
        app_id = func_args.get("application_id", "")
        return f"-# <:read_profile:1484030899531026593> Fetching presence info for `{app_id}`..."

    elif func_name == "list_presences":
        query = func_args.get("query", "")
        if query:
            return f"-# <:read_profile:1484030899531026593> Searching presences for '{query}'..."
        return "-# <:read_profile:1484030899531026593> Listing known presences..."

    # todo
    elif func_name == "todo":
        action = func_args.get("action", "")
        if action == "create":
            n = len(func_args.get("content", []))
            return f"-# <:todo_add:1484060627264868362> Creating TODO with {n} item(s)..."
        elif action == "done":
            content = func_args.get("content", [])
            if not content:
                return "-# <:todo_done:1484060628787134556> Completing TODO..."
            return f"-# <:todo_done:1484060628787134556> Marking {len(content)} item(s) done..."
        elif action == "edit":
            return "-# <:todo_list:1484060630024458383> Updating TODO item..."
        return "-# <:todo_list:1484060630024458383> Updating TODO..."

    # misc / user interaction
    elif func_name == "ask_user":
        return "-# <:ask_user:1484030872415109262> Waiting for your input..."

    elif func_name == "read_profile":
        return "-# <:read_profile:1484030899531026593> Reading user profile..."

    elif func_name == "search_member":
        query = func_args.get("query", "")
        return f"-# <:read_profile:1484030899531026593> Searching for member '{query}'..." if query else "-# <:read_profile:1484030899531026593> Searching server members..."

    elif func_name == "search_guild":
        query = func_args.get("query", "")
        return f"-# <:read_profile:1484030899531026593> Searching servers for '{query}'..." if query else "-# <:read_profile:1484030899531026593> Searching joined servers..."

    elif func_name == "guild_info":
        guild_id = func_args.get("guild_id", "")
        return f"-# <:read_profile:1484030899531026593> Fetching info for server {guild_id}..." if guild_id else "-# <:read_profile:1484030899531026593> Fetching server info..."

    elif func_name == "get_migration_key":
        return "-# <:user_memory:1484030921391870075> Sending migration key embed..."

    elif func_name == "reset_migration_key":
        return "-# <:user_memory:1484030921391870075> Sending key reset confirmation..."

    elif func_name == "link_account":
        src = func_args.get("source_discord_id", "")
        label = f" to `{src}`" if src else ""
        return f"-# <:user_memory:1484030921391870075> Linking account{label}..."

    elif func_name == "unlink_account":
        return "-# <:user_memory:1484030921391870075> Unlinking account and copying data..."

    elif func_name == "send_feedback":
        fb_type = func_args.get("type", "feedback")
        return f"-# <:ask_user:1484030872415109262> Sending {fb_type}..."

    elif func_name == "song_recognition":
        return "-# <:song_recognition:1484030916283338783> Recognizing song..."

    elif func_name == "summarize_channel":
        limit = func_args.get("limit", 100)
        topic = func_args.get("topic")
        deep = func_args.get("deep", False)
        suffix = f' about "{topic}"' if topic else ""
        deep_tag = " (deep)" if deep else ""
        return f"-# <:rag:1484030895441711284> Summarizing last {limit} messages{suffix}{deep_tag}..."

    # voice
    elif func_name == "join_voice":
        return "-# <:join_voice:1484030888718241885> Joining voice channel..."

    elif func_name == "leave_voice":
        return "-# <:leave_voice:1484030890282713201> Leaving voice channel..."

    elif func_name == "send_text_message":
        return "-# <:default:1484030880438816989> Sending a text message to the channel..."

    # escalation
    elif func_name == "escalate":
        return "-# <:escalate:1484030883764633783> Thinking deeper..."

    elif func_name == "daemon":
        return f"-# <:schedule:1484030910960767137> Daemon: {func_args.get('action', '')}..."

    elif func_name == "desktop":
        return f"-# <:run_shell:1484030905503842324> Desktop: {func_args.get('action', '')}..."

        # tool group loading
    elif func_name == "load_tools":
        groups = func_args.get("groups", [])
        names = ", ".join(groups) if groups else "tools"
        return f"-# <:default:1484030880438816989> Loading tool group(s): {names}..."

    elif func_name == "unload_tools":
        groups = func_args.get("groups", [])
        names = ", ".join(groups) if groups else "tools"
        return f"-# <:default:1484030880438816989> Unloading tool group(s): {names}..."

    # fallback
    else:
        return f"-# <:default:1484030880438816989> Executing {func_name}..."

# ---------------------------------------------------------------------------
# Done-status: once a tool finishes, its "Executing..." notice is edited to
# append a result marker, e.g. "-# ... Searching for 'cats'... Found 5 results".
# Order of decision (see get_function_done_message):
#   1. result looks like a failure           -> "Failed"
#   2. _DONE_OVERRIDES[tool](args, result, data) returned a string
#   3. result looks like "X not found"       -> "Not found"
#   4. _DONE_STATIC[tool]
#   5. "Done"
# `data` is the result parsed as JSON (dict/list) or None.
# Add a tool: fixed text -> _DONE_STATIC; result/args dependent -> _DONE_OVERRIDES.
# ---------------------------------------------------------------------------

import json as _json
import re as _re

# Failure markers seen across execute_function and the utils modules.
_FAIL_PREFIXES = (
    "error", "err:", "failed", "failure", "unknown", "could not",
    "crawl error", "navigation error", "move failed", "promotion failed",
    "transcript error", "query failed", "delete failed", "invalid",
    "[youtube error",
)
# daemon tools sometimes return "<head line>\nError: ..." (error not on first line)
_ERR_LINE = _re.compile(r"^\s*error:", _re.I | _re.M)
_ERR_LINE_TOOLS = {
    "start_daemon", "read_daemon_output", "send_daemon_input", "stop_daemon",
    "list_daemons", "daemon_status", "daemon",
}


def _parse_json(s: str):
    t = s.lstrip()
    if t[:1] in ("{", "["):
        try:
            return _json.loads(t)
        except Exception:
            return None
    return None


def _looks_failed(func_name: str, result: str, data) -> bool:
    low = result.lstrip().lower()
    if low.startswith(_FAIL_PREFIXES):
        return True
    if func_name in _ERR_LINE_TOOLS and _ERR_LINE.search(result[:600]):
        return True
    if isinstance(data, dict) and data.get("error"):
        return True
    return False


def _looks_not_found(result: str) -> bool:
    low = result.lstrip().lower()[:80]
    return (low.startswith("no ") and "found" in low) or "not found" in low


# fixed suffix per tool (tools not listed here and not in overrides -> "Done")
_DONE_STATIC = {
    "cleanup_sandbox": "Sandbox wiped",
    "weather_search": "Fetched",
    "summarize_channel": "Summarized",
    "read_skills": "Skills loaded",
    "rag_save": "Saved",
    "rag_delete": "Deleted",
    "load_more_context": "Loaded",
    "make_chess_move": "Moved",
    "promote_pawn": "Promoted",
    "reset_chess_game": "Reset",
    "send_chess_board_image": "Sent",
    "schedule_message": "Scheduled",
    "schedule_task": "Scheduled",
    "schedule_loop": "Scheduled",
    "delete_user_task": "Deleted",
    "clear_user_tasks": "Cleared",
    "edit_task": "Updated",
    "wait_for_time": "Done waiting",
    "start_daemon": "Started",
    "send_daemon_input": "Sent",
    "stop_daemon": "Stopped",
    "ask_user": "Got a reply",
    "get_migration_key": "Sent",
    "reset_migration_key": "Sent",
    "link_account": "Linked",
    "unlink_account": "Unlinked",
    "send_feedback": "Sent",
    "join_voice": "Joined",
    "leave_voice": "Left",
    "send_text_message": "Sent",
    "load_tools": "Loaded",
    "unload_tools": "Unloaded",
}


def _by_arg(key, mapping, default="Done", arg_default=""):
    """Override factory: pick the suffix from func_args[key]."""
    def _fn(args, result, data):
        return mapping.get(args.get(key, arg_default), default)
    return _fn


def _done_web_search(args, result, data):
    low = result.lstrip().lower()
    if low.startswith("no results found"):
        return "No results"
    if "search service temporarily unavailable" in low[:300]:
        return "Failed"
    q = args.get("query", [])
    nq = len(q) if isinstance(q, list) else 1
    failed = (len(_re.findall(r"^### Search for .*? failed", result, _re.M))
              + len(_re.findall(r"^### Results for .*?:\s*\n\s*ERR:", result, _re.M)))
    if nq and failed >= nq:
        return "Failed"
    n = len(_re.findall(r"^## Result \d+", result, _re.M))
    base = f"Found {n} result{'' if n == 1 else 's'}" if n else "Done"
    return f"{base}, {failed} failed" if failed else base


def _done_web_crawl(args, result, data):
    urls = args.get("url", [])
    n = len(urls) if isinstance(urls, list) else 1
    failed = (len(_re.findall(r"^### Crawl for .*? failed", result, _re.M))
              + len(_re.findall(r"^### Content for .*?:\s*\n\s*(?:ERR:|Crawl Error|Navigation error)", result, _re.M)))
    ok = max(0, n - failed)
    if n and ok == 0:
        return "Failed"
    if failed:
        return f"Read {ok}/{n} pages"
    return f"Read {n} page{'' if n == 1 else 's'}" if n else "Done"


def _done_run_code(args, result, data):
    m = _re.search(r"status:\s*(\w+)", result[:80])
    st = m.group(1).lower() if m else "success"
    if st in ("success", "ok"):
        return "Command finished" if args.get("action", "run_code") == "run_shell" else "Code finished"
    if st == "timeout":
        return "Timed out"
    return "Failed"


def _done_create_files(args, result, data):
    if not isinstance(data, list) or not data:
        return "Created"
    n = len(data)
    ok = sum(1 for r in data if isinstance(r, dict) and not r.get("error"))
    if ok == 0:
        return "Failed"
    if ok < n:
        return f"Created {ok}/{n} files"
    return f"Created {n} files" if n > 1 else "Created"


def _done_edit_file(args, result, data):
    r = data.get("replaced") if isinstance(data, dict) else None
    if isinstance(r, int):
        return f"Edited ({r} replacement{'' if r == 1 else 's'})"
    return "Edited"


def _done_read_file(args, result, data):
    if isinstance(data, dict) and "start_line" in data and "end_line" in data:
        tail = " (more below)" if data.get("has_more") else ""
        return f"Read lines {data['start_line']}-{data['end_line']}{tail}"
    return "Done"


def _done_find_str(args, result, data):
    res = data.get("results") if isinstance(data, dict) else None
    if not isinstance(res, dict):
        return "Done"
    vals = [v for v in res.values() if isinstance(v, dict)]
    if vals and all(v.get("error") for v in vals):
        return "Failed"
    total = sum(int(v.get("total_matches", 0) or 0) for v in vals)
    if total == 0:
        return "No matches"
    return f"Found {total} match{'' if total == 1 else 'es'}"


def _done_send_files(args, result, data):
    if not isinstance(data, dict):
        return "Sent"
    s = len(data.get("sent") or [])
    e = len(data.get("errors") or [])
    if s == 0 and e:
        return "Failed"
    if e:
        return f"Sent {s}, {e} failed"
    return f"Sent {s} files" if s > 1 else "Sent"


def _done_cleanup_files(args, result, data):
    if not isinstance(data, dict):
        return "Cleaned up"
    d = data.get("deleted")
    d = len(d) if isinstance(d, list) else int(d or 0)
    e = len(data.get("errors") or [])
    if d == 0 and e:
        return "Failed"
    if e:
        return f"Cleaned up {d}, {e} failed"
    return f"Cleaned up {d}"


def _done_todo(args, result, data):
    # todo returns result["error"] as a bare string (no "Error" prefix)
    if result.lstrip().lower().startswith(("no content provided", "no active todo", "item not found")):
        return "Failed"
    return {"create": "Created", "done": "Completed", "edit": "Updated"}.get(args.get("action", ""), "Updated")


def _done_rag_query(args, result, data):
    n = len(_re.findall(r"^ID: ", result, _re.M))
    if n == 0:
        return None  # "No relevant information found in memory." -> generic "Not found"
    return f"Found {n} memor{'y' if n == 1 else 'ies'}"


def _done_fetch_history(args, result, data):
    if result.lstrip().lower().startswith("no query provided"):
        return "Skipped"
    n = len(_re.findall(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\]\[", result, _re.M))
    if n == 0:
        return "No messages"
    verb = "Found" if args.get("action") == "search" else "Fetched"
    return f"{verb} {n} message{'' if n == 1 else 's'}"


def _done_schaledb(args, result, data):
    m = _re.search(r"\((\d+) total\)", result[:200])
    if m:
        n = int(m.group(1))
        return f"Found {n} student{'' if n == 1 else 's'}"
    return None


def _done_search_hits(noun):
    def _fn(args, result, data):
        m = _re.search(r"\((\d+) result\(s\)", result[:200])
        if not m:
            return None
        n = int(m.group(1))
        return f"Found {n} {noun}{'' if n == 1 else 's'}"
    return _fn


def _done_youtube(args, result, data):
    if args.get("action", "full") == "transcript":
        m = _re.search(r"\|\s*([\d,]+) chars\]", result[:200])
        return f"Got transcript ({m.group(1)} chars)" if m else "Got transcript"
    if "--- Transcript [" in result:
        return "Got video info + transcript"
    if "[transcript unavailable" in result.lower():
        return "Got video info (no transcript)"
    return "Got video info"


def _done_current_presence(args, result, data):
    res = data.get("results") if isinstance(data, dict) else None
    if isinstance(res, list):
        return f"Checked {len(res)} member{'' if len(res) == 1 else 's'}"
    return None


def _done_presence_summary(args, result, data):
    d = data.get("period_days") if isinstance(data, dict) else None
    return f"Got {d}-day summary" if d else None


def _done_list_presences(args, result, data):
    p = data.get("presences") if isinstance(data, dict) else None
    if isinstance(p, list):
        return f"Found {len(p)} presence{'' if len(p) == 1 else 's'}" if p else "No presences"
    return None


def _done_presence_history(args, result, data):
    h = data.get("history") if isinstance(data, dict) else None
    if isinstance(h, list):
        return f"Fetched {len(h)} entr{'y' if len(h) == 1 else 'ies'}"
    return None


_GITHUB_DONE = {
    "search": "Searched GitHub", "info": "Fetched repo info",
    "get_tree": "Fetched file tree", "tree": "Fetched file tree",
    "read_files": "Read files", "find_string": "Searched code",
    "commits": "Fetched commits",
}


_DONE_OVERRIDES = {
    "web_search": _done_web_search,
    "web_crawl": _done_web_crawl,
    "run_code": _done_run_code,
    "create_files": _done_create_files,
    "edit_file": _done_edit_file,
    "read_file": _done_read_file,
    "find_str": _done_find_str,
    "send_files": _done_send_files,
    "cleanup_files": _done_cleanup_files,
    "todo": _done_todo,
    "rag_query": _done_rag_query,
    "fetch_history": _done_fetch_history,
    "schaledb_query": _done_schaledb,
    "search_member": _done_search_hits("member"),
    "search_guild": _done_search_hits("server"),
    "youtube": _done_youtube,
    "fetch_current_presence": _done_current_presence,
    "fetch_guild_presence_summary": _done_presence_summary,
    "list_presences": _done_list_presences,
    "fetch_presence_history": _done_presence_history,
    "fetch_github_repo": _by_arg("action", _GITHUB_DONE),
    "move_file": _by_arg("direction", {"persist": "Persisted"}, default="Staged"),
    "saved_information": _by_arg("action", {"add": "Added", "edit": "Updated", "delete": "Deleted"}),
    "channel_memory": _by_arg("action", {"set": "Updated", "append": "Updated", "clear": "Cleared"}, arg_default="get"),
    "guild_memory": _by_arg("action", {"set": "Updated", "append": "Updated", "clear": "Cleared"}, arg_default="get"),
    "gacha_tracker": _by_arg("action", {"add": "Logged", "add_shards": "Added", "reset": "Reset"}, arg_default="status"),
}


def get_function_done_message(func_name: str, func_args: dict, func_result, exec_text: str) -> str:
    """Return the notice text with a Done/Failed (or per-tool) marker appended."""
    result_str = func_result if isinstance(func_result, str) else str(func_result or "")
    data = _parse_json(result_str)
    if _looks_failed(func_name, result_str, data):
        suffix = "Failed"
    else:
        suffix = None
        override = _DONE_OVERRIDES.get(func_name)
        if override:
            try:
                suffix = override(func_args or {}, result_str, data)
            except Exception:
                suffix = None
        if not suffix:
            suffix = "Not found" if _looks_not_found(result_str) else _DONE_STATIC.get(func_name, "Done")
    return f"{exec_text} {suffix}"[:2000]
