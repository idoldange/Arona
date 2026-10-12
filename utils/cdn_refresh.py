"""Renew expired Discord CDN URLs (cdn.discordapp.com / media.discordapp.net).

Discord signs attachment URLs (?ex=&is=&hm=) and they expire (~24h). A URL sitting in an
old message's text, a cached reply reference, or history is often dead by the time the bot
reads it. This module swaps those for fresh ones via Discord's
POST /attachments/refresh-urls endpoint (works with a bot token).

Cache: refreshed URLs are kept in-process (LRU) keyed by URL-without-signature, and stay
valid until their own `ex` timestamp minus a safety margin. URLs that still have a valid
`ex` are never sent to the API at all. Failed refreshes (deleted message, 429, ...) are
negative-cached briefly so a dead link doesn't hammer the API on every turn.

Usage:
    cdn_refresh.init(client)                      # once, after the Bot is created
    text  = await cdn_refresh.renew_text(text)    # replace CDN URLs inside a string
    await cdn_refresh.renew_history(entries)      # in-place on [{"role","parts":[{"text"}]}]
    await cdn_refresh.renew_attachments(atts)     # sets att.url in place
"""
import re
import time
import asyncio
import urllib.parse
from collections import OrderedDict

import aiohttp

from console import console
from utils.http_session import session_manager

_client = None

CDN_URL_RE = re.compile(
    r'https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)/(?:attachments|ephemeral-attachments)/[^\s<>"\'|]+',
    re.IGNORECASE,
)
# Trailing punctuation the regex can swallow from prose / markdown: "(url)", "[url]", "**url**", "url."
_TRAILING_JUNK_RE = re.compile(r'[)\].,*~]+$')

_REFRESH_ENDPOINT = "https://discord.com/api/v10/attachments/refresh-urls"
_BATCH_SIZE = 50          # Discord's per-request limit
_EXPIRY_MARGIN = 300      # seconds — treat a URL as expired this long before its real `ex`
_CACHE_MAX_ITEMS = 500
_FAIL_TTL = 120           # seconds to skip retrying a URL whose refresh failed
_SIG_PARAMS = ("ex", "is", "hm")

# key -> (refreshed_url, ex_unix_ts)
_cache: "OrderedDict[str, tuple[str, int]]" = OrderedDict()
_failed: dict = {}  # key -> time.monotonic() of failure
_lock = asyncio.Lock()


def init(client) -> None:
    """Give the module access to the bot's token (client.http.token)."""
    global _client
    _client = client


def is_cdn_url(url: str) -> bool:
    return bool(url) and CDN_URL_RE.match(url) is not None


def _split_junk(raw: str) -> tuple[str, str]:
    clean = _TRAILING_JUNK_RE.sub("", raw)
    return clean, raw[len(clean):]


def _parse_ex(url: str):
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        ex = qs.get("ex", [None])[0]
        return int(ex, 16) if ex else None
    except Exception:
        return None


def _is_fresh(url: str) -> bool:
    ex = _parse_ex(url)
    return ex is not None and ex - time.time() > _EXPIRY_MARGIN


def _cache_key(url: str) -> str:
    """URL without the rotating signature params (but keeping e.g. width/height/format)."""
    p = urllib.parse.urlparse(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k not in _SIG_PARAMS]
    q.sort()
    return f"{p.netloc.lower()}{p.path}?{urllib.parse.urlencode(q)}"


def _cache_get(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    url, ex = entry
    if ex - time.time() <= _EXPIRY_MARGIN:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return url


def _cache_set(key: str, url: str) -> None:
    ex = _parse_ex(url)
    if ex is None:
        ex = int(time.time()) + 3600  # no ex in the response — keep it a short while
    _cache[key] = (url, ex)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_ITEMS:
        _cache.popitem(last=False)


def _recently_failed(key: str) -> bool:
    t = _failed.get(key)
    if t is None:
        return False
    if time.monotonic() - t > _FAIL_TTL:
        _failed.pop(key, None)
        return False
    return True


async def _call_api(urls: list) -> dict:
    token = getattr(getattr(_client, "http", None), "token", None)
    if not token:
        console.log("[CDN_REFRESH] bot token unavailable (call cdn_refresh.init(client))", "WARN")
        return {}
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "Arona/1.0",
    }
    try:
        session = await session_manager.get_session()
        async with session.post(
            _REFRESH_ENDPOINT,
            json={"attachment_urls": urls},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                console.log(f"[CDN_REFRESH] API returned {resp.status}: {body}", "WARN")
                return {}
            data = await resp.json()
    except Exception as e:
        console.log(f"[CDN_REFRESH] request failed: {e}", "WARN")
        return {}

    items = data.get("refreshed_urls") or []
    sent = set(urls)
    out = {}
    for i, item in enumerate(items):
        orig, new = item.get("original"), item.get("refreshed")
        if orig not in sent and len(items) == len(urls):
            orig = urls[i]  # API didn't echo the exact string — fall back to position
        if orig and new:
            out[orig] = new
    return out


async def renew_urls(urls) -> dict:
    """Map each CDN URL -> a currently-valid URL (itself if still fresh or refresh failed)."""
    result: dict = {}
    need: dict = {}  # original url -> cache key
    for u in set(urls):
        if not is_cdn_url(u):
            continue
        if _is_fresh(u):
            result[u] = u
            continue
        key = _cache_key(u)
        cached = _cache_get(key)
        if cached:
            result[u] = cached
        elif _recently_failed(key):
            result[u] = u
        else:
            need[u] = key

    if need:
        async with _lock:  # serialize so concurrent history/live tasks don't refresh the same URL twice
            pending = []
            for u, key in need.items():
                cached = _cache_get(key)  # may have been filled while we waited for the lock
                if cached:
                    result[u] = cached
                else:
                    pending.append(u)
            for i in range(0, len(pending), _BATCH_SIZE):
                chunk = pending[i:i + _BATCH_SIZE]
                refreshed = await _call_api(chunk)
                for u in chunk:
                    new = refreshed.get(u)
                    key = need[u]
                    if new:
                        _cache_set(key, new)
                        result[u] = new
                    else:
                        _failed[key] = time.monotonic()
                        result[u] = u
            if pending:
                n_ok = sum(1 for u in pending if result.get(u) != u)
                console.log(f"[CDN_REFRESH] refreshed {n_ok}/{len(pending)} expired CDN URL(s)", "INFO")
    return result


def _substitute(text: str, mapping: dict) -> str:
    def _repl(m):
        clean, tail = _split_junk(m.group(0))
        return mapping.get(clean, clean) + tail
    return CDN_URL_RE.sub(_repl, text)


def _find_urls(text: str) -> set:
    return {_split_junk(m)[0] for m in CDN_URL_RE.findall(text)}


async def renew_text(text):
    """Replace every Discord CDN URL inside `text` with a fresh one."""
    if not text or not isinstance(text, str) or "discordapp" not in text:
        return text
    urls = _find_urls(text)
    if not urls:
        return text
    try:
        mapping = await renew_urls(urls)
        return _substitute(text, mapping)
    except Exception as e:
        console.log(f"[CDN_REFRESH] renew_text failed: {e}", "WARN")
        return text


async def renew_history(entries) -> None:
    """In-place: renew CDN URLs in the "text" parts of Gemini-format history entries."""
    if not entries:
        return
    try:
        text_parts = []
        urls: set = set()
        for entry in entries:
            for part in (entry.get("parts") or []) if isinstance(entry, dict) else []:
                t = part.get("text") if isinstance(part, dict) else None
                if isinstance(t, str) and "discordapp" in t:
                    found = _find_urls(t)
                    if found:
                        urls |= found
                        text_parts.append(part)
        if not urls:
            return
        mapping = await renew_urls(urls)
        for part in text_parts:
            part["text"] = _substitute(part["text"], mapping)
    except Exception as e:
        console.log(f"[CDN_REFRESH] renew_history failed: {e}", "WARN")


async def renew_attachments(atts) -> None:
    """In-place: refresh .url on Attachment-like objects so .read() and tags use a live URL."""
    try:
        by_url = {}
        for att in atts or []:
            u = getattr(att, "url", None)
            if u and is_cdn_url(u):
                by_url.setdefault(u, []).append(att)
        if not by_url:
            return
        mapping = await renew_urls(by_url.keys())
        for old, group in by_url.items():
            new = mapping.get(old)
            if new and new != old:
                for att in group:
                    try:
                        att.url = new
                    except Exception:
                        pass
    except Exception as e:
        console.log(f"[CDN_REFRESH] renew_attachments failed: {e}", "WARN")
