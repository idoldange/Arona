"""
utils/youtube.py — YouTube metadata & transcript helper for Arona bot.

Dependencies (pip install if missing):
    yt-dlp                    → full metadata (title, views, duration, etc.)
    youtube-transcript-api    → subtitles / auto-captions

Public API
extract_video_id(url)                          → str | None
get_video_metadata(url)                        → dict
get_transcript(url, languages)                 → dict
get_video_info(url, include_transcript, ...)   → dict   ← main entry point
format_for_gemini(info, max_transcript_chars)  → str
"""

import asyncio
import re
import json
from typing import Optional, Dict, Any, List

from utils.http_session import session_manager

# Helpers

#support youtube.com/watch?v=VIDEOID, youtube.com/embed/VIDEOID, youtube.com/v/VIDEOID, youtube.com/shorts/VIDEOID, youtu.be/VIDEOID, etc.
_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/|live/)|youtu\.be/)"
    r"([a-zA-Z0-9_-]{11})"
)

def extract_video_id(url: str) -> Optional[str]:
    """Extract 11-char YouTube video ID from any standard URL format."""
    if not url:
        return None
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def _fmt_duration(seconds: int) -> str:
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# Metadata

async def get_video_metadata(url: str) -> Dict[str, Any]:
    """
    Fetch rich video metadata via yt-dlp subprocess.
    Falls back to YouTube oEmbed API if yt-dlp is unavailable or times out.

    Returned keys (best-effort):
        video_id, url, title, channel, channel_url,
        duration (str), duration_seconds (int),
        views, likes, upload_date (YYYYMMDD str),
        description (≤500 chars), thumbnail,
        tags (list, max 10), categories (list),
        is_live, age_limit, language
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"error": "Invalid YouTube URL"}

    canonical = f"https://www.youtube.com/watch?v={video_id}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            "--quiet",
            canonical,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return await _oembed_fallback(video_id)

        if proc.returncode != 0 or not stdout.strip():
            return await _oembed_fallback(video_id)

        data = json.loads(stdout.decode("utf-8", errors="replace"))
        dur = int(data.get("duration") or 0)
        return {
            "video_id": video_id,
            "url": canonical,
            "title": data.get("title", "Unknown"),
            "channel": data.get("uploader") or data.get("channel", "Unknown"),
            "channel_url": data.get("channel_url") or data.get("uploader_url"),
            "duration": _fmt_duration(dur) if dur else None,
            "duration_seconds": dur,
            "views": data.get("view_count"),
            "likes": data.get("like_count"),
            "upload_date": data.get("upload_date"),
            "description": (data.get("description") or "")[:500],
            "thumbnail": data.get("thumbnail"),
            "tags": (data.get("tags") or [])[:10],
            "categories": data.get("categories") or [],
            "is_live": bool(data.get("is_live")),
            "age_limit": data.get("age_limit", 0),
            "language": data.get("language"),
        }

    except FileNotFoundError:
        # yt-dlp not in PATH
        return await _oembed_fallback(video_id)
    except Exception as e:
        return {"error": str(e), "video_id": video_id, "url": canonical}


async def _oembed_fallback(video_id: str) -> Dict[str, Any]:
    """Lightweight fallback via YouTube oEmbed (no API key needed)."""
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    try:
        session = await session_manager.get_session()
        oembed_url = (
            f"https://www.youtube.com/oembed"
            f"?url={canonical}&format=json"
        )
        async with session.get(oembed_url, timeout=10) as resp:
            if resp.status == 200:
                d = await resp.json()
                return {
                    "video_id": video_id,
                    "url": canonical,
                    "title": d.get("title", "Unknown"),
                    "channel": d.get("author_name", "Unknown"),
                    "channel_url": d.get("author_url"),
                    "thumbnail": d.get("thumbnail_url"),
                    "duration": None,
                    "duration_seconds": None,
                    "views": None,
                    "likes": None,
                    "upload_date": None,
                    "description": None,
                }
    except Exception:
        pass
    return {"video_id": video_id, "url": canonical, "error": "Could not fetch metadata"}


# Transcript

async def get_transcript(
    url: str,
    languages: Optional[List[str]] = None,
    limit: Optional[int] = None,
    cookie_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch transcript/subtitles for a YouTube video.

    Parameters
    url         : YouTube URL
    languages   : Preferred language codes in priority order.
                  Defaults to ["vi", "en"]; auto-generated captions are tried
                  as a final fallback in any available language.
    limit       : Optional maximum number of transcript characters to return.
    cookie_file : Path to a Netscape-format cookies.txt exported from browser.
                  Needed for videos that return 403 without authentication.

    Returns
    dict with keys:
        video_id, language, is_generated (bool),
        transcript (possibly truncated text str),
        segments (first 20 raw segments),
        total_segments (int), char_count (int),
        is_truncated (bool), full_char_count (int)
    On failure:
        {"error": "...", "video_id": ...}
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"error": "Invalid YouTube URL"}

    if languages is None:
        languages = ["vi", "en"]

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_transcript_sync, video_id, languages, limit, cookie_file),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        return {"error": "Transcript fetch timed out", "video_id": video_id}
    except Exception as e:
        return {"error": str(e), "video_id": video_id}


def _fetch_transcript_sync(
    video_id: str,
    languages: List[str],
    limit: Optional[int] = None,
    cookie_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Blocking implementation — run via asyncio.to_thread."""
    try:
        from youtube_transcript_api import (
            YouTubeTranscriptApi,
            NoTranscriptFound,
        )
    except ImportError:
        return {
            "error": (
                "youtube-transcript-api not installed. "
                "Run: pip install youtube-transcript-api"
            )
        }

    # Build API instance — inject cookies session if provided
    try:
        if cookie_file:
            import requests
            import http.cookiejar
            session = requests.Session()
            jar = http.cookiejar.MozillaCookieJar(cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar
            api = YouTubeTranscriptApi(http_client=session)
        else:
            api = YouTubeTranscriptApi()
    except Exception as e:
        return {"error": f"Failed to init API: {e}", "video_id": video_id}

    try:
        tl = api.list(video_id)
    except Exception as e:
        return {"error": str(e), "video_id": video_id}

    transcript = None
    is_generated = False
    used_lang = None

    # 1. Manual transcript theo preferred languages
    try:
        transcript = tl.find_manually_created_transcript(languages)
        used_lang = transcript.language_code
        is_generated = False
    except (NoTranscriptFound, Exception):
        pass

    # 2. Auto-generated theo preferred languages
    if transcript is None:
        try:
            transcript = tl.find_generated_transcript(languages + ["en", "ja"])
            used_lang = transcript.language_code
            is_generated = True
        except (NoTranscriptFound, Exception):
            pass

    # 3. Last resort — bất kỳ cái nào available
    if transcript is None:
        available = list(tl)
        if available:
            transcript = available[0]
            used_lang = transcript.language_code
            is_generated = transcript.is_generated

    if transcript is None:
        return {"error": "No transcript available for this video", "video_id": video_id}

    try:
        raw_segments = transcript.fetch()
    except Exception as e:
        return {"error": f"Failed to fetch transcript: {e}", "video_id": video_id}

    segments = []
    for seg in raw_segments:
        if isinstance(seg, dict):
            segments.append(seg)
        else:
            segments.append({
                "text": getattr(seg, "text", ""),
                "start": getattr(seg, "start", 0.0),
                "duration": getattr(seg, "duration", 0.0),
            })

    full_text = " ".join(
        seg["text"].strip()
        for seg in segments
        if seg.get("text", "").strip()
    )

    full_char_count = len(full_text)
    is_truncated = False
    
    # If limit is None, return the full transcript as-is
    if limit is not None:
        try:
            limit_val = int(limit)
        except (ValueError, TypeError):
            limit_val = None
        
        # Only truncate if limit is valid and less than full length
        if limit_val is not None and limit_val > 0 and limit_val < full_char_count:
            full_text = full_text[:limit_val]
            is_truncated = True

    return {
        "video_id": video_id,
        "language": used_lang,
        "is_generated": is_generated,
        "transcript": full_text,
        "segments": segments[:20],
        "total_segments": len(segments),
        "char_count": len(full_text),
        "full_char_count": full_char_count,
        "is_truncated": is_truncated,
    }


# Combined entry point

async def get_video_info(
    url: str,
    include_transcript: bool = True,
    transcript_languages: Optional[List[str]] = None,
    transcript_limit: Optional[int] = None,
    cookie_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch metadata (and optionally transcript) in parallel.
    Returns a merged dict; transcript info lives under key 'transcript_data'.
    """
    if include_transcript:
        meta, trans = await asyncio.gather(
            get_video_metadata(url),
            get_transcript(url, transcript_languages, limit=transcript_limit, cookie_file=cookie_file),
        )
    else:
        meta = await get_video_metadata(url)
        trans = {}

    return {**meta, "transcript_data": trans}

# Formatting

def format_for_gemini(
    info: Dict[str, Any],
    max_transcript_chars: int = 8000,
) -> str:
    """
    Serialize video info dict → compact text block ready to paste into
    a Gemini prompt as context.
    """
    if info.get("error") and not info.get("title"):
        return f"[YouTube Error: {info['error']}]"

    lines = ["=== YouTube Video ==="]
    lines.append(f"Title   : {info.get('title', 'N/A')}")
    lines.append(f"Channel : {info.get('channel', 'N/A')}")

    if info.get("duration"):
        lines.append(f"Duration: {info['duration']}")
    if info.get("views") is not None:
        lines.append(f"Views   : {info['views']:,}")
    if info.get("upload_date"):
        d = str(info["upload_date"])
        if len(d) == 8:
            lines.append(f"Uploaded: {d[:4]}-{d[4:6]}-{d[6:]}")
    if info.get("description"):
        desc = info["description"]
        lines.append(f"Desc    : {desc}")
    if info.get("tags"):
        lines.append(f"Tags    : {', '.join(info['tags'][:5])}")

    td = info.get("transcript_data") or {}
    if td.get("transcript"):
        lang = td.get("language", "?")
        gen_tag = " (auto)" if td.get("is_generated") else ""
        lines.append(f"\n--- Transcript [{lang}{gen_tag}] ---")
        text = td["transcript"]
        if len(text) > max_transcript_chars:
            lines.append(text[:max_transcript_chars])
            lines.append(
                f"… [transcript truncated — {len(text):,} chars total, "
                f"{td.get('total_segments', '?')} segments]"
            )
        else:
            lines.append(text)
    elif td.get("error"):
        lines.append(f"\n[Transcript unavailable: {td['error']}]")

    return "\n".join(lines)