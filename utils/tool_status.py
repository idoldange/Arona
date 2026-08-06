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

    # blue archive
    elif func_name == "schaledb_query":
        query = func_args.get("query", func_args.get("action", ""))
        if query:
            return f"-# <:schaledb:1484030907584090183> Querying Schale database for '{query}'..."
        return "-# <:schaledb:1484030907584090183> Querying Schale database..."

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
        return "-# <:fetch_github_repo:1484030885366988840> Fetching GitHub data..."

    # context hub
    elif func_name == "chub":
        action = func_args.get("action", "")
        doc_id = func_args.get("doc_id", "")
        if action == "search":
            q = func_args.get("query", "")
            return f"-# <:chub:1484030878542729317> Searching Context Hub{f': {q}' if q else ''}..."
        elif action == "get":
            return f"-# <:chub:1484030878542729317> Fetching docs for `{doc_id}`..."
        elif action == "annotate":
            return f"-# <:chub:1484030878542729317> Saving annotation for `{doc_id}`..."
        elif action == "feedback":
            return f"-# <:chub:1484030878542729317> Sending feedback for `{doc_id}`..."
        return "-# <:chub:1484030878542729317> Calling Context Hub..."

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

    # escalation
    elif func_name == "escalate":
        return "-# <:escalate:1484030883764633783> Thinking deeper..."

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