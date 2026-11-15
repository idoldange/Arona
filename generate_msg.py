import argparse
import os
import re
import sys
import json
import time
import subprocess
import urllib.error
import urllib.request

# ---- cau hinh ---------------------------------------------------------------
MODEL = "gemini-3.1-flash-lite"
RECENT_COMMITS = 15          # so commit gan nhat dua san vao prompt (de bat chuoc style, tranh lap lai)
MAX_TOOL_TURNS = 8           # toi da so luot model goi tool truoc khi ep no viet commit message
REQUEST_TIMEOUT = 30         # giay cho moi request (loop co tool lau hon 1 request don)
READ_DEFAULT_LINES = 400     # read_file: so dong mac dinh neu khong chi dinh end_line
READ_MAX_CHARS = 20000       # read_file: cat output vuot muc nay
LOG_MAX_COUNT = 100          # git_log: toi da so commit tra ve 1 lan
SHOW_MAX_CHARS = 25000       # show_commit: so ky tu toi da tra ve 1 lan (doc tiep bang offset)
SHOW_MAX_FILES = 300         # show_commit: so file toi da dua vao pathspec
DIFF_MAX_CHARS = 100000
MAX_API_RETRIES = 3
RETRY_BASE_SECONDS = 10

OUT_FILE = ".commit_msg.txt"
# PLAIN_RETRIES = 2           # temporarily disabled with the announcement jargon filter
DEFAULT_LANG = "vi"          # ngon ngu cua dong dau (dong cho nguoi khong biet code); doi bang --lang

# Ten ngon ngu dua cho model khi truyen ma (en-US, ja, pt-BR...). Ma la khac thi dung nguyen chuoi nguoi dung truyen vao.
LANG_NAMES = {
    "vi": "Vietnamese", "en": "English", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "fr": "French",
    "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "ru": "Russian", "id": "Indonesian",
    "ms": "Malay", "th": "Thai", "tl": "Filipino", "fil": "Filipino", "tr": "Turkish", "hi": "Hindi",
    "ar": "Arabic", "pl": "Polish", "nl": "Dutch", "uk": "Ukrainian", "sv": "Swedish",
}


def normalize_lang(raw):
    """-> (key, ten). key: 'vi' | 'en' | 'other'. Ten la thu se noi cho model ('Vietnamese', 'Portuguese (pt-BR)'...)."""
    s = (raw or DEFAULT_LANG).strip() or DEFAULT_LANG
    k = s.lower().replace("_", "-")
    if k in ("vi", "vn", "vie", "vietnamese", "tieng viet", "tiếng việt") or k.startswith("vi-"):
        return "vi", "Vietnamese"
    if k in ("en", "eng", "english") or k.startswith("en-"):
        return "en", "English"
    base = k.split("-")[0]
    name = LANG_NAMES.get(base)
    if name:
        return "other", name + (f" ({s})" if "-" in k else "")
    return "other", s


# Temporarily disabled: these patterns used to block technical terms and file-like names
# in user-facing announcements. Keep them commented for easy restoration if needed.
# _JARGON_EN = (r"commit|api|prompt|token|config|database|script|refactor|async|quota|function|tool|tools|repo|"
#               r"git|github|code|bug|debug|merge|diff|backend|frontend|daemon|scheduler|regex|json|sql|sqlite|"
#               r"python|module|class|file")
# _JARGON_VI = r"hàm|mã nguồn|lập trình|thuật toán|tham số|thư viện"
# _JARGON_ANY = r"commit|api|git|github|json|sql|sqlite|python|regex"
# _PATHLIKE = r"[._/\\]\w+\.(?:py|js|md|json)|[^\W_]+_[^\W_]+"
# _JARGON_RE = {
#     "vi": re.compile(rf"(?<!\w)(?:{_JARGON_EN}|{_JARGON_VI})(?!\w)|{_PATHLIKE}", re.IGNORECASE),
#     "en": re.compile(rf"(?<!\w)(?:{_JARGON_EN})(?!\w)|{_PATHLIKE}", re.IGNORECASE),
#     "other": re.compile(rf"(?<!\w)(?:{_JARGON_ANY})(?!\w)|{_PATHLIKE}", re.IGNORECASE),
# }
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{4,40}|HEAD(?:[~^]\d{1,4})?")


def _git(args, **kw):
    return subprocess.check_output(["git"] + args, text=True, encoding="utf-8", errors="ignore", **kw)


class GitError(Exception):
    pass


class TpmError(Exception):
    """429 do vuot tokens-per-minute (khac voi vuot requests-per-minute)."""
    pass


class RetryableAPIError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


def wait_before_retry(retry_count, reason):
    if retry_count >= MAX_API_RETRIES:
        print(f"[Warning] {reason}; exhausted {MAX_API_RETRIES} retries for this key", file=sys.stderr)
        return False

    delay = RETRY_BASE_SECONDS * (2 ** retry_count)
    print(f"[Warning] {reason}; retry {retry_count + 1}/{MAX_API_RETRIES} in {delay}s...", file=sys.stderr)
    time.sleep(delay)
    return True


def _gitq(args, **kw):
    """Dung cho tool: stdout sach (khong lan warning), loi git tra ve qua GitError de bao lai cho model."""
    r = subprocess.run(["git"] + args, capture_output=True, text=True, encoding="utf-8", errors="ignore", **kw)
    if r.returncode != 0:
        raise GitError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def get_repo_root():
    try:
        return os.path.realpath(_git(["rev-parse", "--show-toplevel"]).strip())
    except Exception:
        return os.path.realpath(os.getcwd())


def get_git_diff(limit=DIFF_MAX_CHARS):
    # --function-context (-W): mo rong moi hunk ra toan bo ham/vong lap chua no,
    # nen model biet duoc thay doi nam trong ham/khoi nao, khong chi thay may dong +/-.
    diff_args = ["diff", "--function-context", "--cached"]
    try:
        diff = _git(diff_args)
        if not diff.strip():
            diff = _git(["diff", "--function-context"])
        return diff[:limit], len(diff) > limit
    except Exception:
        return "", False


def get_diff_stat():
    """Danh sach file + so dong doi (staged, neu khong co thi unstaged). Cho model thay file nao bi cat khoi diff."""
    try:
        stat = _git(["diff", "--cached", "--stat=140", "--stat-count=60"])
        if not stat.strip():
            stat = _git(["diff", "--stat=140", "--stat-count=60"])
        return stat.strip()
    except Exception:
        return ""


def get_recent_log():
    try:
        return _git(["log", f"-n{RECENT_COMMITS}", "--date=short", "--pretty=format:%h %ad %s"]).strip()
    except Exception:
        return ""


def get_api_keys():
    env_path = r"C:\arona\.env"
    if not os.path.exists(env_path):
        return []

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    try:
                        keys = json.loads(val)
                        if isinstance(keys, list):
                            return keys
                        return [str(keys)]
                    except json.JSONDecodeError:
                        return [val]
    except Exception as e:
        print(f"[Warning] Could not read .env file: {e}", file=sys.stderr)
    return []


# ---- tools ------------------------------------------------------------------
def get_owner_login():
    """Login chu repo tu remote origin (GitHub); khong co remote thi 'idoldange'."""
    try:
        raw = _gitq(["remote", "get-url", "origin"]).strip()
    except Exception:
        raw = ""
    m = re.search(r"github\.com[:/]+([^/]+)/", raw)
    return m.group(1) if m else "idoldange"


def get_owner_name(login):
    """Ten hien thi cua chu repo tren GitHub; loi hoac khong co thi dung login."""
    try:
        req = urllib.request.Request("https://api.github.com/users/" + login,
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "AronaUpdate"})
        with urllib.request.urlopen(req, timeout=10) as r:
            name = (json.load(r) or {}).get("name")
        return (name or login).strip()
    except Exception:
        return login


def get_agent_md():
    """Doc agent.md o goc repo (index ngan: file nao lam gi, lien quan file nao). Cat o 8000 ky tu."""
    try:
        with open(os.path.join(get_repo_root(), "agent.md"), "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()[:8000]
    except Exception:
        return ""


TOOLS = [{
    "functionDeclarations": [
        {
            "name": "read_file",
            "description": (
                "Read a text file from this git repository (current working tree) to understand the code around a "
                "change. Only files that are NOT git-ignored can be read. Returns numbered lines."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "File path relative to the repo root, e.g. utils/scheduler.py"},
                    "start_line": {"type": "INTEGER", "description": "First line to read (1-based, default 1)"},
                    "end_line": {"type": "INTEGER", "description": f"Last line to read (default start_line + {READ_DEFAULT_LINES - 1})"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "git_log",
            "description": (
                "List older commits (hash, date, subject), newest first. Use skip to go further back than the recent "
                "history already given, path to see the history of one file or folder, grep to search commit "
                "messages by keyword (case-insensitive)."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "count": {"type": "INTEGER", "description": f"How many commits (default 20, max {LOG_MAX_COUNT})"},
                    "skip": {"type": "INTEGER", "description": "Skip this many newest commits first (default 0)"},
                    "path": {"type": "STRING", "description": "Only commits touching this file/folder (repo-relative)"},
                    "grep": {"type": "STRING", "description": "Only commits whose message contains this text"},
                },
            },
        },
        {
            "name": "show_commit",
            "description": (
                "Show what an earlier commit changed: full message, changed files and its diff. Files that are "
                "git-ignored are hidden. Long output is paged: call again with the offset it tells you."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "commit": {"type": "STRING", "description": "Commit hash (4-40 hex chars) or HEAD / HEAD~N"},
                    "path": {"type": "STRING", "description": "Only show the diff of this file/folder"},
                    "offset": {"type": "INTEGER", "description": "Character offset to continue a long output (default 0)"},
                },
                "required": ["commit"],
            },
        },
    ]
}]


def _is_ignored(root, rel):
    """True/False, hoac None neu khong xac dinh duoc (coi nhu bi chan)."""
    rc = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=root,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return True if rc == 0 else False if rc == 1 else None


def read_file_tool(root, path, start_line=None, end_line=None):
    """Doc 1 file trong repo. Tu choi: ngoai repo, .git/, file bi gitignore, file nhi phan, file khong ton tai."""
    try:
        path = str(path or "").strip().replace("\\", "/")
        if not path:
            return {"error": "path is required"}
        full = os.path.realpath(os.path.join(root, path))
        nroot, nfull = os.path.normcase(root), os.path.normcase(full)
        if nfull != nroot and not nfull.startswith(nroot + os.sep):
            return {"error": "path is outside the repository"}
        rel = os.path.relpath(full, root).replace("\\", "/")
        if rel == ".git" or rel.startswith(".git/"):
            return {"error": "access to .git is not allowed"}
        if not os.path.isfile(full):
            return {"error": f"file not found: {rel}"}
        ign = _is_ignored(root, rel)
        if ign is None:
            return {"error": "could not verify gitignore status; refusing to read"}
        if ign:
            return {"error": f"{rel} is git-ignored and cannot be read"}
        with open(full, "rb") as f:
            raw = f.read()
        if b"\0" in raw[:8000]:
            return {"error": f"{rel} looks like a binary file"}
        lines = raw.decode("utf-8", errors="ignore").splitlines()
        total = len(lines)
        start = max(1, int(start_line)) if start_line else 1
        end = int(end_line) if end_line else start + READ_DEFAULT_LINES - 1
        end = min(max(end, start), total)
        out, size = [], 0
        for i in range(start, end + 1):
            row = f"{i}: {lines[i - 1]}"
            size += len(row) + 1
            if size > READ_MAX_CHARS:
                end = i - 1
                break
            out.append(row)
        note = f"{rel} lines {start}-{end} of {total}"
        if end < total:
            note += f" (more lines available: call again with start_line={end + 1})"
        return {"file": rel, "info": note, "content": "\n".join(out)}
    except Exception as e:
        return {"error": f"read failed: {e}"}


def _clean_rel_path(path):
    """Chuan hoa path loc theo repo; None neu khong hop le (rong, tuyet doi, chua ..)."""
    p = str(path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p:
        return None
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
        return ""
    return p.rstrip("/")


def git_log_tool(root, count=None, skip=None, path=None, grep=None):
    try:
        n = max(1, min(int(count or 20), LOG_MAX_COUNT))
        sk = max(0, int(skip or 0))
        args = ["--literal-pathspecs", "log", f"-n{n}", f"--skip={sk}", "--date=short", "--pretty=format:%h %ad %s"]
        if grep:
            args += ["-i", f"--grep={str(grep)[:200]}"]
        rp = _clean_rel_path(path)
        if rp == "":
            return {"error": "path must be a repo-relative path without '..'"}
        if rp:
            args += ["--", rp]
        out = _gitq(args, cwd=root).strip()
        if not out:
            return {"info": "no commits found", "content": ""}
        got = len(out.splitlines())
        info = f"{got} commit(s), skipped {sk}"
        if got == n:
            info += f" (may be more: call again with skip={sk + n})"
        return {"info": info, "content": out}
    except GitError as e:
        return {"error": f"git log failed: {e}"}
    except Exception as e:
        return {"error": f"git_log failed: {e}"}


def show_commit_tool(root, commit, path=None, offset=None):
    """Header + stat + diff cua 1 commit cu. File hien dang bi gitignore bi giau (tranh lo noi dung cu cua .env...)."""
    try:
        c = str(commit or "").strip()
        if not _COMMIT_RE.fullmatch(c):
            return {"error": "commit must be a hex hash (4-40 chars) or HEAD / HEAD~N"}
        names = [n for n in _gitq(["show", "--name-only", "--format=", "-z", c], cwd=root).split("\0") if n]
        if names:
            r = subprocess.run(["git", "check-ignore", "--no-index", "-z", "--stdin"], input="\0".join(names),
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if r.returncode not in (0, 1):
                return {"error": "could not verify gitignore status; refusing to show"}
            ignored = set(x for x in r.stdout.split("\0") if x)
        else:
            ignored = set()
        allowed = [n for n in names if n not in ignored]
        hidden = len(names) - len(allowed)
        rp = _clean_rel_path(path)
        if rp == "":
            return {"error": "path must be a repo-relative path without '..'"}
        if rp:
            allowed = [n for n in allowed if n == rp or n.startswith(rp + "/")]
        if not allowed:
            return {"error": "no readable changed files in this commit" + (" for that path" if rp else "")
                             + (f" ({hidden} git-ignored file(s) hidden)" if hidden else "")}
        capped = len(allowed) > SHOW_MAX_FILES
        allowed = allowed[:SHOW_MAX_FILES]
        header = _gitq(["show", "--no-patch", "--date=iso", "--format=commit %H%nAuthor: %an%nDate: %ad%n%n%B", c], cwd=root).strip()
        base = ["--literal-pathspecs", "show", "--format=", c]
        stat = _gitq(base[:2] + ["--stat=140"] + base[2:] + ["--"] + allowed, cwd=root).strip()
        diff = _gitq(base[:2] + ["-U5"] + base[2:] + ["--"] + allowed, cwd=root)
        text = f"{header}\n\n--- files ---\n{stat}\n\n--- diff ---\n{diff}"
        off = max(0, int(offset or 0))
        chunk = text[off:off + SHOW_MAX_CHARS]
        notes = []
        if hidden:
            notes.append(f"{hidden} git-ignored file(s) hidden")
        if capped:
            notes.append(f"only first {SHOW_MAX_FILES} files included; use path to narrow")
        if off + SHOW_MAX_CHARS < len(text):
            notes.append(f"output continues: call again with offset={off + SHOW_MAX_CHARS} (or use path to narrow)")
        return {"info": f"commit {c}, chars {off}-{off + len(chunk)} of {len(text)}" + (f" ({'; '.join(notes)})" if notes else ""),
                "content": chunk}
    except GitError as e:
        return {"error": f"git show failed: {e}"}
    except Exception as e:
        return {"error": f"show_commit failed: {e}"}


def run_tool(root, name, args):
    if name == "read_file":
        return read_file_tool(root, args.get("path"), args.get("start_line"), args.get("end_line"))
    if name == "git_log":
        return git_log_tool(root, args.get("count"), args.get("skip"), args.get("path"), args.get("grep"))
    if name == "show_commit":
        return show_commit_tool(root, args.get("commit"), args.get("path"), args.get("offset"))
    return {"error": f"unknown tool {name}"}


# ---- goi Gemini (co function calling) ---------------------------------------
def _post(key, payload):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        if e.code == 429:
            norm = body.lower().replace("_", "").replace(" ", "")
            if "tokensperminute" in norm:
                raise TpmError("TPM quota exceeded, shrink the diff and retry")   # giam token thi het
        if e.code in (429, 503):
            raise RetryableAPIError(e.code, body) from e
        raise


def _text_of(content):
    return "".join(p.get("text", "") for p in content.get("parts", []) if p.get("text") and not p.get("thought")).strip()


OPEN_TAG, CLOSE_TAG = "<message>", "</message>"
MSG_FILE = ".discord_msg.txt"
TAG_RETRIES = 3

_FORBIDDEN = ("Forbidden: file or function names and tech words such as commit, code, function, tool, API, prompt, "
              "token, config, database, script, refactor, async, quota")
_STYLE = {
    "vi": "casual Gen Z Vietnamese, like texting a friend (short forms t/m/ko/đc/r/j/vs/nha/luôn are fine; no forced slang, no emoji)",
    "en": "casual, friendly English, like texting a friend (light slang ok, no forced memes or emoji)",
}
_EX = {
    "vi": "Sửa vụ hẹn giờ nhắc việc bị lặp hoài ko dừng\nGiờ lỗi vài lần là bot tự bỏ luôn nha, ko spam nữa",
    "en": "Fixed the reminder thing that kept retrying forever\nIt now gives up after a few fails, no more spam",
}


def style_for(key, name):
    return _STYLE.get(key) or f"casual, friendly spoken {name}, as younger native speakers text a friend (slang only where natural; no emoji)"


def example_for(key, name):
    ann = _EX.get(key) or f"<casual plain announcement in {name}>"
    return ("fix: stop scheduler retrying a failed task forever\n\n"
            "- count a failed run in scheduler.py:scheduler_heartbeat() except-branch, delete the task after max retries\n"
            "- log the final failure with the task id\n\n" + OPEN_TAG + "\n" + ann + "\n" + CLOSE_TAG)


def message_spec(key, name, owner="idoldange", call="anh"):
    return (f"At the very end, after a blank line, add the Discord announcement wrapped EXACTLY as {OPEN_TAG} ... "
            f"{CLOSE_TAG} with nothing after the closing tag. The bot owner is {owner} (dev): in Vietnamese address them "
            f"as '{call} {owner}', in other languages however a younger person would naturally call them. Every change in "
            "the diff was written by this dev, not by you: present the update as their work, never as yours or as "
            "the bot's doing. ONLY this text is posted to Discord (the title and bullets "
            f"are not), so write it in {style_for(key, name)} for readers who do NOT code: what they will notice (new "
            "ability, fix, nicer behaviour). Write it as plain prose like a person typing to friends: normal sentences "
            "with natural line breaks (a blank line between paragraphs is fine), no bullet lists and no markdown "
            "formatting; no hard limit, long texts are split automatically). Stay accurate, never claim an effect the diff does not "
            f"support. {_FORBIDDEN}. Never use @mentions. If the change is only behind the scenes, say so simply and "
            "honestly.\n")


def split_announcement(text):
    """-> (commit_msg, announcement, problem). Khoi <message> phai o CUOI; nhac toi the trong bullet ky thuat thi bo qua."""
    t = text.strip()
    if OPEN_TAG not in t and CLOSE_TAG not in t:
        return t, None, f"there is no {OPEN_TAG}...{CLOSE_TAG} block"
    o = t.rfind(OPEN_TAG)
    if not t.endswith(CLOSE_TAG):
        return t, None, (f"{OPEN_TAG} is opened but never closed" if o > t.rfind(CLOSE_TAG)
                         else f"the response must END with {CLOSE_TAG}")
    if o == -1:
        return t, None, f"{CLOSE_TAG} has no opening {OPEN_TAG}"
    ann = t[o + len(OPEN_TAG):-len(CLOSE_TAG)].strip()
    if OPEN_TAG in ann or CLOSE_TAG in ann:
        return t, None, "the block contains nested or duplicate tags"
    if not ann:
        return t, None, "the block is empty"
    commit = t[:o].rstrip()
    if not commit:
        return t, None, "the technical commit message before the block is missing"
    return commit, ann, None


def strip_tags(t):
    """Khi tag hong sau khi het luot sua: bo phan tag khoi commit message."""
    o = t.rfind(OPEN_TAG)
    t = t[:o] if o != -1 else t
    return t.replace(CLOSE_TAG, "").strip()


# def plain_problems(text, lang_key="vi"):
#     rx = _JARGON_RE.get(lang_key, _JARGON_RE["other"])
#     return sorted({m.group(0).strip() for m in rx.finditer(text)})


def tag_feedback(problem, key, name):
    return (f"Invalid answer: {problem}. Rewrite the WHOLE answer: the technical commit message first (English title, "
            f"blank line, bullets), then at the very end ONE {OPEN_TAG}...{CLOSE_TAG} block with the casual announcement "
            f"in {name} for non-coders, nothing after {CLOSE_TAG}. Output only that.")


# def jargon_feedback(bad, key, name):
#     return ("The announcement is not plain enough: it contains " + ", ".join(f'"{b}"' for b in bad) + ". Rewrite the "
#             f"WHOLE answer, keep the commit message unchanged, but write the {OPEN_TAG} block in {style_for(key, name)} "
#             "so someone who has never coded understands it at once, with none of those words and no names of files, "
#             "functions or tools. Output only the answer.")


def generate_with_key(key, prompt, root, lang_key="vi", lang_name="Vietnamese"):
    """-> (commit_message, announcement|None)."""
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    tool_turns = tag_n = 0
    force_text = False
    while True:
        payload = {"contents": contents, "tools": TOOLS}
        if force_text or tool_turns >= MAX_TOOL_TURNS:   # het luot tool / dang sua loi: ep tra loi bang text
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        content = _post(key, payload)["candidates"][0]["content"]
        calls = [p["functionCall"] for p in content.get("parts", []) if p.get("functionCall")]
        if calls:
            tool_turns += 1
            contents.append(content)   # giu nguyen (gom ca thoughtSignature)
            responses = []
            for fc in calls:
                args = fc.get("args") or {}
                result = run_tool(root, fc.get("name"), args)
                print(f"[tool] {fc.get('name')}({args}) -> {'error: ' + result['error'] if 'error' in result else result.get('info', 'ok')}",
                      file=sys.stderr)
                responses.append({"functionResponse": {"name": fc.get("name"), "response": result}})
            contents.append({"role": "user", "parts": responses})
            continue
        text = _text_of(content).replace("```git", "").replace("```", "").strip()
        commit, ann, problem = split_announcement(text)
        feedback = None
        if problem:
            print(f"[announce] {problem}", file=sys.stderr)
            if tag_n < TAG_RETRIES:
                tag_n += 1
                feedback = tag_feedback(problem, lang_key, lang_name)
        # Jargon-regex validation is temporarily disabled; technical wording is allowed in announcements.
        if feedback is None:
            return (strip_tags(text), None) if problem else (commit, ann)
        contents.append(content)
        contents.append({"role": "user", "parts": [{"text": feedback}]})
        force_text = True


def build_prompt(diff, truncated, stat, log, lang_key="vi", lang_name="Vietnamese", owner="idoldange", call="anh", limit=DIFF_MAX_CHARS, agent_md=""):
    parts = [
        "You are writing the commit message for the PENDING commit whose diff is at the end of this prompt. "
        "The commits in the history list below are ALREADY committed; do not describe them as part of this change.\n",

        "HOW TO WORK (do this BEFORE writing the message):\n"
        "1. Read the file list and the diff. For every changed file/function make sure you understand what it does "
        "and what changed. The diff has full function context, but not the rest of the file.\n"
        "2. If anything is unclear (a new function whose purpose you cannot tell, a config value, who calls a changed "
        "function, a rename, a file cut from the diff), call read_file to look at the code around it.\n"
        "3. If this commit continues, fixes, tunes or reverts earlier work, call git_log (path= for the history of one "
        "file, grep= to search by keyword, skip= to go further back than the history below) and show_commit to read "
        "what the earlier commit really changed. Never guess what earlier commits did.\n"
        "4. Write only when you can say, with evidence from the diff or the files you read: what changed, what it "
        "affects, and (if visible) why. Do not describe anything you did not verify, do not invent file or function "
        "names, and do not call a change a \"fix\" unless the diff shows a bug being fixed.\n"
        "5. If something looks half-finished (stub function, TODO, a feature wired up only halfway), treat it "
        "as work in progress: describe what it is meant to do and that it is still being built, never as "
        "finished. If you are not sure, hedge like \'chắc là'\' (or the natural equivalent in the announcement "
        "language).\n"
        "Small obvious commits need 0-2 tool calls; larger or unclear ones need more. Do not call tools just to look "
        "busy, and do not repeat a call with the same arguments.\n",

        "OUTPUT FORMAT (exactly this layout, no code fences, no extra text):\n"
        "Line 1: technical title in English, conventional format (feat:, fix:, refactor:, ...), max 50 characters.\n"
        "Line 2: blank line.\n"
        "Then short bullet points in English, each naming the relevant file and function/loop "
        "(e.g. `- fix buffering in arch.py:read_output() loop`).\n"
        + message_spec(lang_key, lang_name, owner, call) +
        "Example of the layout (the content is made up):\n" + example_for(lang_key, lang_name) + "\n",
    ]
    if agent_md:
        parts.append("PROJECT MAP (agent.md - short index: what each file does and how files relate; use it to tell "
                     "where a change lives and what else it may affect):\n" + agent_md + "\n")
    if log:
        parts.append("Recent commits (newest first). Match their style and do not repeat what they already describe "
                     "unless this diff changes it again:\n" + log + "\n")
    if stat:
        parts.append("Files changed in this commit:\n" + stat + "\n")
    if truncated:
        parts.append(f"NOTE: the diff below was cut at {limit} characters; later files are missing, "
                     "use the file list above and read_file to cover them.\n")
    parts.append(f"Diff:\n{diff}")
    return "\n".join(parts)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Generate a git commit message + Discord announcement with Gemini.")
    ap.add_argument("--lang", default=DEFAULT_LANG,
                    help="language of the <message> announcement: 'vi' (casual Gen Z, default), 'en', or any other "
                         "language code/name (ja, fr, pt-BR, Thai...). The commit message itself is always English.")
    ap.add_argument("--call", default="anh",
                    help="cach goi chu bot trong thong bao: 'anh' (mac dinh) hoac 'chi'")
    ap.add_argument("--name", default="",
                    help="ten chu bot tu chon; truyen thi bo qua viec tra remote + GitHub API")
    return ap.parse_args(argv)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    args = parse_args()
    lang_key, lang_name = normalize_lang(args.lang)
    print(f"[lang] announcement in {lang_name}", file=sys.stderr)
    if args.name:
        owner, login = args.name, ""
    else:
        login = get_owner_login()
        owner = get_owner_name(login)
    print(f"[owner] {owner} ({login or 'custom'}); goi la: {args.call}", file=sys.stderr)
    if os.path.exists(MSG_FILE):
        os.remove(MSG_FILE)   # khong de thong bao cu bi gui lai

    limit = DIFF_MAX_CHARS
    diff, truncated = get_git_diff(limit)
    if not diff:
        return _write(OUT_FILE, "Auto-sync: minor changes")
    keys = get_api_keys()
    if not keys:
        return _write(OUT_FILE, "Auto-sync: update codebase")

    root = get_repo_root()
    agent_md = get_agent_md()
    print(f"[agent.md] {len(agent_md)} chars", file=sys.stderr)
    prompt = build_prompt(diff, truncated, get_diff_stat(), get_recent_log(), lang_key, lang_name, owner=owner, call=args.call, limit=limit, agent_md=agent_md)
    for key in keys:
        retry_count = 0
        while True:
            try:
                commit, ann = generate_with_key(key, prompt, root, lang_key, lang_name)
                if not commit:
                    raise ValueError("empty response")
                _write(OUT_FILE, commit)
                if ann:
                    _write(MSG_FILE, ann)
                    print(f"[announce] {len(ann)} chars -> {MSG_FILE}", file=sys.stderr)
                else:
                    print("[announce] no valid <message> block; nothing will be posted to Discord", file=sys.stderr)
                return
            except TpmError:
                if not wait_before_retry(retry_count, "TPM quota exceeded"):
                    break
                retry_count += 1
                smaller = max(20000, limit // 2)
                if smaller < limit:
                    limit = smaller
                    diff, truncated = get_git_diff(limit)
                    prompt = build_prompt(diff, truncated, get_diff_stat(), get_recent_log(), lang_key, lang_name, owner=owner, call=args.call, limit=limit, agent_md=agent_md)
                continue
            except RetryableAPIError as e:
                if wait_before_retry(retry_count, str(e)):
                    retry_count += 1
                    continue
                break
            except Exception as e:
                print(f"[Warning] Key failed or API error, trying next key... ({e})", file=sys.stderr)
                break
    _write(OUT_FILE, "Auto-sync: update codebase (API failover)")


if __name__ == "__main__":
    main()
