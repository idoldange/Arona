"""
utils/malformed_recovery.py
────────────────────────────────────────────────────────────────────────────────
Helpers for detecting and recovering from MALFORMED_FUNCTION_CALL responses.

Drop this file into utils/ and import the two public functions in main.py.
"""

import re
import json


# known function → corrective hint

_FUNC_HINTS: dict[str, str] = {
    "run_code": (
        "Call `run_code` with params: "
        "`action` (\"run_code\"|\"run_shell\"), "
        "`code` (Python string). "
        "Do NOT output raw ``` code blocks — put the code inside the `code` parameter."
    ),
    "create_files": (
        "Call the `create_files` function using the NATIVE function-call mechanism. "
        "Parameters: `files` (array of objects, each with `filename` (string) and `content` (string)). "
        "CRITICAL: Do NOT write Python code. Do NOT use `print(...)`, `default_api.create_files(...)`, "
        "or `default_api.CreateFilesFiles(...)`. These are Python SDK patterns and are WRONG here. "
        "You are NOT writing a Python script — you are making a direct function call. "
        "Example correct call structure: {\"name\": \"create_files\", \"args\": {\"files\": [{\"filename\": \"example.txt\", \"content\": \"Hello\"}]}}"
    ),
    "edit_file": (
        "Call `edit_file` with `file_ref` (temp ID or CDN URL), "
        "`old_content` (exact text to replace), and `new_content`. "
        "Strings must be exact — whitespace matters."
    ),
    "web_search": (
        "Call `web_search` with `query` (array of strings). "
        "Do NOT simulate search results inline."
    ),
    "web_crawl": (
        "Call `web_crawl` with `url` (array of strings)."
    ),
    "schedule_loop": (
        "Call `schedule_loop`. `loop_at_time` MUST be exactly \"HH:MM\" (e.g. \"23:00\") — "
        "no timezone suffix, no extra text."
    ),
}

# Regex patterns that suggest which function was being attempted
_FUNC_SIGNATURES: list[tuple[re.Pattern, str]] = [
    # Highest priority: catch the "print(default_api.xxx(...))" Python SDK anti-pattern
    # Model confuses Gemini Python SDK (default_api module) with native function calling
    (re.compile(r'default_api\s*\.\s*(\w+)\s*\(', re.I), None),  # special-handled below
    (re.compile(r'\brun_code\b|\brun_shell\b|```python|```bash|```sh\b', re.I), "run_code"),
    (re.compile(r'\bcreate_files\b|\"filename\"\s*:', re.I), "create_files"),
    (re.compile(r'\bedit_file\b|\"old_content\"\s*:|\"new_content\"\s*:', re.I), "edit_file"),
    (re.compile(r'\bweb_search\b|\"query\"\s*:\s*\[', re.I), "web_search"),
    (re.compile(r'\bweb_crawl\b|\"url\"\s*:\s*\[', re.I), "web_crawl"),
    (re.compile(r'\bschedule_loop\b|\"loop_at_time\"\s*:', re.I), "schedule_loop"),
    (re.compile(r'\bchub\b|\"doc_id\"\s*:', re.I), "chub"),
    (re.compile(r'\bsend_files\b|\"file_refs\"\s*:', re.I), "send_files"),
    (re.compile(r'\bask_user\b|\"question\"\s*:', re.I), "ask_user"),
    (re.compile(r'\bsaved_information\b|\"action\"\s*:\s*\"(add|edit|delete)\"', re.I), "saved_information"),
    (re.compile(r'\bschaledb_query\b', re.I), "schaledb_query"),
    (re.compile(r'\bfetch_github_repo\b', re.I), "fetch_github_repo"),
]


def detect_malformed_function(finish_message: str, parts: list[dict]) -> str | None:
    """
    Best-effort detection of which function the model was trying to call.
    Prioritizes specific function signatures over generic text matching.
    """
    corpus = finish_message or ""
    
    for part in parts or []:
        corpus += " " + (part.get("text") or "")

    # 0. Special case: catch "print(default_api.function_name(...))" — model confused
    #    the Gemini Python SDK (default_api module) with native function calling.
    #    Extract the actual function name from default_api.FUNC_NAME(...)
    m_api = re.search(r'default_api\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(', corpus)
    if m_api:
        raw_name = m_api.group(1)
        # Map known SDK class/function aliases back to real function names
        _SDK_ALIAS_MAP = {
            "CreateFilesFiles": "create_files",
            "create_files": "create_files",
            "RunCodeCode": "run_code",
            "run_code": "run_code",
            "EditFileFile": "edit_file",
            "edit_file": "edit_file",
            "SendFilesFiles": "send_files",
            "send_files": "send_files",
            "WebSearchSearch": "web_search",
            "web_search": "web_search",
        }
        if raw_name in _SDK_ALIAS_MAP:
            return _SDK_ALIAS_MAP[raw_name]
        # Fallback: snake_case the CamelCase name and check if it matches a known function
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', raw_name).lower()
        return snake  # best guess

    # 1. Regex fingerprints on the full corpus (Most reliable for raw code leaks)
    for pattern, func_name in _FUNC_SIGNATURES:
        if func_name is None:
            continue  # skip the special-handled sentinel
        if pattern.search(corpus):
            return func_name

    # 2. JSON-like fragment — try to extract "name" key
    try:
        json_frag = re.search(r'\{[^}]{0,300}\}', corpus)
        if json_frag:
            obj = json.loads(json_frag.group())
            if "name" in obj:
                return obj["name"]
    except Exception:
        pass

    # 3. Direct name in finish_message (Fallback)
    # Uses negative lookahead (?!call\b) to avoid matching the word "call"
    if finish_message:
        m = re.search(r'function(?:[\s:]+|[\'"])(?!call\b)(\w+)', finish_message, re.I)
        if m:
            return m.group(1)

    return None


def build_malformed_retry_message(
    finish_message: str,
    detected_func: str | None,
    attempt: int,
) -> str:
    """
    Build a system message to inject into history before retrying.

    Parameters
    finish_message : raw finishMessage from the API
    detected_func  : function name detected by detect_malformed_function()
    attempt        : current retry number (1-based), used to escalate the urgency
    """
    urgency = "CRITICAL CORRECTION REQUIRED" if attempt >= 3 else "CORRECTION REQUIRED"
    lines = [f"[SYSTEM — {urgency}]"]
    lines.append("The previous response triggered a MALFORMED_FUNCTION_CALL error.")

    # Always warn about the Python SDK anti-pattern since it's the most common cause
    if finish_message and "default_api" in finish_message:
        lines.append(
            "ROOT CAUSE: You wrote Python code using `default_api.function_name(...)` or "
            "`print(default_api.function_name(...))`. "
            "This is the Gemini Python SDK pattern and is COMPLETELY WRONG here. "
            "You are NOT writing a Python script. You are making a NATIVE FUNCTION CALL. "
            "Do NOT use `print()`, `default_api`, or any Python wrapper."
        )

    if finish_message:
        # Truncate to avoid bloating context
        snippet = finish_message[:400].strip()
        lines.append(f"API error details: {snippet}")

    if detected_func:
        hint = _FUNC_HINTS.get(detected_func, f"Use the `{detected_func}` tool correctly.")
        lines.append(f"Suspected function: `{detected_func}`")
        lines.append(f"Fix: {hint}")
    else:
        lines.append(
            "Could not identify the specific function. "
            "Review the tool list and call the appropriate function using the correct schema. "
            "Do NOT output text that mimics a function call — use the actual tool call mechanism."
        )

    lines.append(
        "Retry NOW: call the correct function with valid parameters. "
        "Do NOT output any prose explanation — just the function call."
    )

    if attempt >= 4:
        lines.append(
            "⚠️ This is retry attempt " + str(attempt) + ". "
            "If the correct tool is unavailable, say so in plain text instead of guessing."
        )

    return "\n".join(lines)


def get_retry_temperature(base_temperature: float, attempt: int) -> float:
    """
    Reduce temperature on each malformed retry to make the model less 'creative'.
    Floor at 0.0; never let it go back up.
    """
    reduction = 0.15 * attempt
    return max(0.0, base_temperature - reduction)