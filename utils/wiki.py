# utils/wiki.py
"""
Blue Archive Wiki tool via MediaWiki API.
Fandom dùng MediaWiki nên /api.php hoàn toàn accessible, bypass Cloudflare WAF.
"""

import asyncio
import re
import aiohttp
from typing import Optional

BASE_URL = "https://bluearchive.fandom.com/api.php"

# Shared session (lazy init)
_session: Optional[aiohttp.ClientSession] = None

HEADERS = {
    # Dùng browser UA cho chắc, dù API thường không cần
    "User-Agent": "AronaBot/1.0 (Blue Archive Discord Bot; contact via Discord)",
    "Accept": "application/json",
}

def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(headers=HEADERS)
    return _session


async def close_session():
    """Gọi khi bot shutdown."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


def _clean_extract(text: str) -> str:
    """
    Clean up MediaWiki plain text extract.
    explaintext=1 đã strip HTML/wikitext nhưng còn sót một số artifacts.
    """
    # Remove section edit markers [edit]
    text = re.sub(r'\[edit\]', '', text)
    # Collapse 3+ newlines thành 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip leading/trailing whitespace
    return text.strip()


async def wiki_search(query: str, limit: int = 5) -> dict:
    """
    Tìm kiếm trang trên Blue Archive Wiki.
    
    Returns:
        {
            "results": [{"title": str, "url": str}, ...],
            "suggestions": [str, ...]  # raw title list
        }
    """
    params = {
        "action": "opensearch",
        "search": query,
        "limit": limit,
        "namespace": 0,  # Main namespace only
        "format": "json",
        "redirects": "resolve",
    }
    
    session = _get_session()
    try:
        async with session.get(BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        # OpenSearch format: [query, [titles], [descriptions], [urls]]
        titles = data[1]
        urls = data[3]
        
        return {
            "results": [
                {"title": t, "url": u}
                for t, u in zip(titles, urls)
            ],
            "suggestions": titles,
            "query": query,
        }
    except asyncio.TimeoutError:
        return {"error": "timeout", "query": query}
    except Exception as e:
        return {"error": str(e), "query": query}


async def wiki_get_page(
    title: str,
    intro_only: bool = False,
    max_chars: int = 3000,
    section_index: Optional[int] = None,
) -> dict:
    """
    Lấy nội dung trang wiki dưới dạng plain text.
    
    Args:
        title: Tên trang wiki (e.g. "Arona", "Shiroko")
        intro_only: Chỉ lấy intro paragraph (nhanh hơn, nhỏ hơn)
        max_chars: Giới hạn ký tự output
        section_index: Lấy section cụ thể theo index (0 = intro)
    
    Returns:
        {
            "title": str,
            "extract": str,
            "url": str,
            "pageid": int,
            "sections": [str, ...]  # tên các section
        }
    """
    # Bước 1: Lấy text extract
    params: dict = {
        "action": "query",
        "prop": "extracts|info",
        "titles": title,
        "format": "json",
        "redirects": "1",      # Auto-follow redirects
        "inprop": "url",
        "formatversion": "2",  # Cleaner JSON format
        "explaintext": "1",    # Plain text, không phải HTML
        "exsectionformat": "plain",
    }
    
    if intro_only or section_index == 0:
        params["exintro"] = "1"
    
    if section_index is not None and section_index > 0:
        params["exsection"] = section_index
    
    if max_chars:
        params["exchars"] = max_chars

    session = _get_session()
    try:
        async with session.get(BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json()

        pages = data.get("query", {}).get("pages", [])
        if not pages:
            return {"error": "no_pages_returned", "title": title}

        page = pages[0]
        
        if page.get("missing"):
            return {"error": "page_not_found", "title": title}
        
        extract = _clean_extract(page.get("extract", ""))
        
        # Bước 2: Lấy danh sách sections (dùng parse action)
        sections = await _get_sections(title)
        
        return {
            "title": page.get("title", title),
            "pageid": page.get("pageid"),
            "extract": extract,
            "url": page.get("canonicalurl", f"https://bluearchive.fandom.com/wiki/{title.replace(' ', '_')}"),
            "sections": sections,
        }

    except asyncio.TimeoutError:
        return {"error": "timeout", "title": title}
    except Exception as e:
        return {"error": str(e), "title": title}


async def _get_sections(title: str) -> list[str]:
    """Helper: lấy danh sách tên section của trang."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "sections",
        "format": "json",
        "redirects": "1",
        "formatversion": "2",
    }
    session = _get_session()
    try:
        async with session.get(BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            resp.raise_for_status()
            data = await resp.json()
        sections = data.get("parse", {}).get("sections", [])
        return [s["line"] for s in sections]
    except Exception:
        return []


async def wiki_get_section_by_name(title: str, section_name: str, max_chars: int = 2000) -> dict:
    """
    Lấy section theo tên (e.g. "Profile", "Skills", "Story").
    Tự động tìm section index từ tên.
    """
    # Lấy section list trước
    sections = await _get_sections(title)
    
    # Fuzzy match tên section (case-insensitive)
    section_idx = None
    section_name_lower = section_name.lower()
    for i, s in enumerate(sections):
        if section_name_lower in s.lower():
            section_idx = i + 1  # sections trong parse API 1-indexed, nhưng trong extracts thì khác
            break
    
    if section_idx is None:
        return {
            "error": "section_not_found",
            "title": title,
            "available_sections": sections,
            "query": section_name,
        }
    
    return await wiki_get_page(title, section_index=section_idx, max_chars=max_chars)