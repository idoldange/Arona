import aiohttp
import asyncio
import re
import base64
from typing import List, Dict, Any, Optional
import hashlib
import json
from console import console


class GithubRepo:
    def __init__(self, token: Optional[str] = None):
        self.base_api = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Arona-Bot-Github-Fetcher"
        }
        if token:
            self.headers["Authorization"] = f"token {token}"

        self._ram_cache = {}
        self._rate_limit_remaining = None
        self._rate_limit_reset = None
        self._session = None

    async def _get_session(self):
        if self._session is None or getattr(self._session, 'closed', True):
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(headers=self.headers, timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _generate_cache_key(
        self,
        action: str,
        url: str = "",
        query: str = "",
        urls_list: List[str] = None,
        line_ranges: List[List[int]] = None,
        tree_offset: int = 0,
        tree_limit: int = 200,
    ) -> str:
        data = {
            "action": action,
            "url": url,
            "query": query,
            "urls": sorted(urls_list) if urls_list else [],
            "line_ranges": line_ranges or [],
            "tree_offset": tree_offset,
            "tree_limit": tree_limit,
        }
        return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _parse_url(self, url: str) -> Dict[str, str]:
        url = url.rstrip('/')
        match = re.search(
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/(blob|tree)/([^/]+)/(.+))?$",
            url
        )
        if not match:
            return {"owner": "", "repo": "", "type": "", "branch": "", "path": ""}
        return {
            "owner":  match.group(1),
            "repo":   match.group(2),
            "type":   match.group(3) or "",
            "branch": match.group(4) or "",
            "path":   match.group(5) or ""
        }

    def _update_rate_limit(self, headers: Dict[str, str]):
        if "X-RateLimit-Remaining" in headers:
            self._rate_limit_remaining = int(headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Reset" in headers:
            self._rate_limit_reset = int(headers["X-RateLimit-Reset"])

    async def _check_rate_limit(self):
        if self._rate_limit_remaining is not None and self._rate_limit_remaining < 10:
            console.log(f"Only {self._rate_limit_remaining} API calls remaining", "WARN")

    def _slice_lines(self, content: str, line_start: int = None, line_end: int = None) -> str:
        """
        Return a slice of file content by 1-based line numbers.
        Prepends a header showing total lines and the slice range so the bot
        always knows where it is in the file.
        """
        lines = content.splitlines(keepends=True)
        total = len(lines)

        if line_start is None and line_end is None:
            text = content[:500_000]
            note = f"[Lines 1-{total} of {total} total]"
            if len(content) > 500_000:
                note += " [TRUNCATED — use line_ranges to read specific sections]"
            return f"{note}\n{text}"

        s = max(1, line_start or 1)
        e = min(total, line_end or total)
        sliced = "".join(lines[s - 1:e])
        return f"[Lines {s}-{e} of {total} total]\n{sliced}"

    async def fetch_github_repo(
        self,
        action: str,
        url: str = "",
        query: str = "",
        urls_list: List[str] = None,
        line_ranges: List[List[int]] = None,
        tree_offset: int = 0,
        tree_limit: int = 200,
    ) -> Any:
        """
        action      : search | info | get_tree | tree | read_files | find_string
        url         : repo / file URL
        query       : search term (search / find_string)
        urls_list   : file URLs (read_files)
        line_ranges : [[start, end], ...] parallel to urls_list — select line slices.
                      Pass null for a file to get full content.
        tree_offset : 0-based start index into file tree (pagination)
        tree_limit  : entries per page, default 200, max 2000
        """
        action = (action or "").strip().lower()
        cache_key = self._generate_cache_key(
            action, url, query, urls_list, line_ranges, tree_offset, tree_limit
        )

        if cache_key in self._ram_cache:
            return self._ram_cache[cache_key]

        session = await self._get_session()
        try:
            result = "Error: No valid action specified."

            if action == "search":
                result = await self._search_repos(session, query)
            elif action == "info":
                result = await self._get_repo_info(session, url)
            elif action == "read_files":
                target_urls = urls_list if urls_list else [url]
                result = await self._read_files(session, target_urls, line_ranges)
            elif action in ("get_tree", "tree"):
                result = await self._get_tree(session, url, tree_offset, tree_limit)
            elif action == "find_string":
                result = await self._find_string(session, url, query)

            self._ram_cache[cache_key] = result
            await self._check_rate_limit()
            return result

        except asyncio.TimeoutError:
            return {"error": "Request timed out."}
        except aiohttp.ClientError as e:
            return {"error": f"Network error: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}

    # search

    async def _search_repos(self, session, query: str) -> List[Dict]:
        api_url = f"{self.base_api}/search/repositories"
        try:
            async with session.get(api_url, params={"q": query, "per_page": 5}) as resp:
                self._update_rate_limit(resp.headers)
                if resp.status == 403:
                    return {"error": "Rate limit exceeded. Use a GitHub token."}
                if resp.status != 200:
                    return {"error": f"GitHub API returned status {resp.status}"}
                data = await resp.json()
                return [
                    {
                        "full_name":   i["full_name"],
                        "html_url":    i["html_url"],
                        "description": i["description"],
                        "stars":       i["stargazers_count"]
                    }
                    for i in data.get("items", [])
                ]
        except Exception as e:
            return {"error": f"Search failed: {str(e)}"}

    # info

    async def _get_repo_info(self, session, url: str) -> Dict:
        p = self._parse_url(url)
        if not p["owner"]:
            return {"error": "Invalid GitHub URL."}

        branches_to_try = [p["branch"]] if p["branch"] else ["main", "master"]
        tree_data = {}
        for branch in branches_to_try:
            tree_url = (
                f"{self.base_api}/repos/{p['owner']}/{p['repo']}"
                f"/git/trees/{branch}?recursive=1"
            )
            try:
                async with session.get(tree_url) as resp:
                    self._update_rate_limit(resp.headers)
                    if resp.status == 200:
                        tree_data = await resp.json()
                        break
                    elif resp.status == 403:
                        return {"error": "Rate limit exceeded or access forbidden."}
            except Exception:
                continue

        if not tree_data:
            return {"error": "Could not fetch repository tree. Repo may be private or not exist."}

        blobs = [i for i in tree_data.get("tree", []) if i["type"] == "blob"]
        readme_content = None
        for blob in blobs:
            if blob["path"].lower() == "readme.md":
                readme_content = blob
                break

        if readme_content:
            try:
                async with session.get(readme_content["url"]) as resp:
                    self._update_rate_limit(resp.headers)
                    if resp.status == 200:
                        readme_content["content"] = await resp.text()
            except Exception:
                pass

        p["readme"] = readme_content
        return {
            "repository": f"{p['owner']}/{p['repo']}",
            "total_files": len(blobs),
            "files_structure_preview": [i["path"] for i in blobs[:100]],
            "readme": readme_content,
            "instruction": (
                "Use 'get_tree' to browse the full file list (paginate with "
                "tree_offset/tree_limit). Use 'read_files' with line_ranges to read "
                "specific line slices."
            )
        }

    # read files  (line-range support)

    async def _read_files(
        self,
        session,
        urls: List[str],
        line_ranges: List[List[int]] = None,
    ) -> Dict[str, Any]:
        results = {}
        ranges = list(line_ranges or []) + [None] * len(urls)

        async def fetch_one(u: str, lr):
            line_start = lr[0] if lr and len(lr) > 0 else None
            line_end   = lr[1] if lr and len(lr) > 1 else None
            try:
                raw_url = (
                    u.replace("github.com", "raw.githubusercontent.com")
                     .replace("/blob/", "/")
                )
                async with session.get(raw_url) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        results[u] = self._slice_lines(content, line_start, line_end)
                    elif resp.status == 404:
                        p = self._parse_url(u)
                        if p["owner"] and self.headers.get("Authorization"):
                            api_url = (
                                f"{self.base_api}/repos/{p['owner']}/{p['repo']}"
                                f"/contents/{p['path']}"
                            )
                            async with session.get(api_url) as api_resp:
                                self._update_rate_limit(api_resp.headers)
                                if api_resp.status == 200:
                                    data = await api_resp.json()
                                    try:
                                        content = base64.b64decode(
                                            data["content"]
                                        ).decode("utf-8")
                                    except UnicodeDecodeError:
                                        content = "Error: File is not UTF-8 encoded."
                                    results[u] = self._slice_lines(
                                        content, line_start, line_end
                                    )
                                else:
                                    results[u] = f"Error: Status {api_resp.status}"
                        else:
                            results[u] = "Error: File not found or private (Status 404)"
                    else:
                        results[u] = f"Error: Status {resp.status}"
            except Exception as e:
                results[u] = f"Error: {str(e)}"

        capped = urls[:10]
        await asyncio.gather(*[fetch_one(u, ranges[i]) for i, u in enumerate(capped)])
        return results

    # get tree  (paginated)

    async def _get_tree(
        self,
        session: aiohttp.ClientSession,
        url: str,
        tree_offset: int = 0,
        tree_limit: int = 200,
    ) -> Dict[str, Any]:
        p = self._parse_url(url)
        if not p["owner"]:
            return {"error": "Invalid GitHub URL provided."}

        tree_limit  = max(1, min(2000, tree_limit))
        tree_offset = max(0, tree_offset)

        branch = p["branch"]
        if not branch:
            try:
                async with session.get(
                    f"{self.base_api}/repos/{p['owner']}/{p['repo']}"
                ) as resp:
                    self._update_rate_limit(resp.headers)
                    if resp.status == 403:
                        return {"error": "Rate limit exceeded or access forbidden."}
                    if resp.status != 200:
                        return {"error": f"Failed to fetch repo metadata. Status: {resp.status}"}
                    branch = (await resp.json()).get("default_branch", "main")
            except Exception as e:
                return {"error": f"Failed to get default branch: {str(e)}"}

        try:
            async with session.get(
                f"{self.base_api}/repos/{p['owner']}/{p['repo']}"
                f"/git/trees/{branch}?recursive=1"
            ) as resp:
                self._update_rate_limit(resp.headers)
                if resp.status == 403:
                    return {"error": "Rate limit exceeded or access forbidden."}
                if resp.status != 200:
                    return {"error": f"Failed to fetch tree. Status: {resp.status}"}

                data   = await resp.json()
                full_tree = [
                    item["path"]
                    for item in data.get("tree", [])
                    if item["type"] == "blob"
                ]

                if p["path"]:
                    full_tree = [f for f in full_tree if f.startswith(p["path"])]

                total      = len(full_tree)
                page       = full_tree[tree_offset: tree_offset + tree_limit]
                has_more   = (tree_offset + tree_limit) < total

                result = {
                    "repository":  f"{p['owner']}/{p['repo']}",
                    "branch":      branch,
                    "total_files": total,
                    "offset":      tree_offset,
                    "limit":       tree_limit,
                    "returned":    len(page),
                    "has_more":    has_more,
                    "tree":        page,
                }
                if has_more:
                    result["next_offset"] = tree_offset + tree_limit
                    result["instruction"] = (
                        f"More files available. Call get_tree again with "
                        f"tree_offset={tree_offset + tree_limit}."
                    )
                return result

        except Exception as e:
            return {"error": f"Failed to fetch tree: {str(e)}"}

    # find string  (line number + ±10 line context)

    async def _find_string(
        self,
        session: aiohttp.ClientSession,
        url: str,
        query: str,
    ) -> Dict[str, Any]:
        """
        Search for a string via GitHub Code Search, then fetch each matched file
        and locate the exact line(s) where the query appears.
        Returns ±10 lines of context around every match, with line numbers and
        '>>>' markers so the bot can orient itself before deciding to read_files.
        """
        if not query:
            return {"error": "Missing 'query' for find_string action."}

        p = self._parse_url(url)
        if not p["owner"]:
            return {"error": "Invalid GitHub URL for find_string."}

        search_q = f"{query} repo:{p['owner']}/{p['repo']}"
        if p["path"]:
            search_q += f" path:{p['path']}"

        try:
            async with session.get(
                f"{self.base_api}/search/code",
                params={"q": search_q, "per_page": 10},
                headers={
                    **self.headers,
                    "Accept": "application/vnd.github.v3.text-match+json"
                }
            ) as resp:
                self._update_rate_limit(resp.headers)
                if resp.status == 403:
                    return {"error": "Rate limit exceeded. Code search requires a GitHub token."}
                if resp.status == 422:
                    return {"error": "Invalid search query. Try a simpler term."}
                if resp.status != 200:
                    return {"error": f"Code search failed. Status: {resp.status}"}
                data  = await resp.json()
                items = data.get("items", [])
        except Exception as e:
            return {"error": f"find_string search failed: {str(e)}"}

        if not items:
            return {
                "repository":    f"{p['owner']}/{p['repo']}",
                "query":         query,
                "total_matches": 0,
                "results":       [],
                "message":       "No files matched the search query."
            }

        async def enrich(item: dict) -> dict:
            file_path = item.get("path", "")
            file_url  = item.get("html_url", "")

            # Try raw first, then API (for private repos with token)
            file_content = None
            for raw_url in [
                f"https://raw.githubusercontent.com/{p['owner']}/{p['repo']}/HEAD/{file_path}",
            ]:
                try:
                    async with session.get(raw_url) as r:
                        if r.status == 200:
                            file_content = await r.text()
                            break
                except Exception:
                    pass

            if file_content is None and self.headers.get("Authorization"):
                try:
                    async with session.get(
                        f"{self.base_api}/repos/{p['owner']}/{p['repo']}/contents/{file_path}"
                    ) as r:
                        self._update_rate_limit(r.headers)
                        if r.status == 200:
                            d = await r.json()
                            file_content = base64.b64decode(
                                d["content"]
                            ).decode("utf-8", errors="replace")
                except Exception:
                    pass

            if file_content is not None:
                lines       = file_content.splitlines()
                total_lines = len(lines)
                q_lower     = query.lower()

                # 1-based line numbers that contain the query
                hit_lines = [
                    i + 1
                    for i, line in enumerate(lines)
                    if q_lower in line.lower()
                ]

                # Merge overlapping ±10 windows
                windows: List[List[int]] = []
                for ln in hit_lines:
                    ws = max(1, ln - 10)
                    we = min(total_lines, ln + 10)
                    if windows and ws <= windows[-1][1] + 1:
                        windows[-1][1] = max(windows[-1][1], we)
                    else:
                        windows.append([ws, we])

                match_blocks = []
                for ws, we in windows:
                    hits_here = [ln for ln in hit_lines if ws <= ln <= we]
                    ctx_lines = []
                    for idx in range(ws - 1, we):
                        ln     = idx + 1
                        marker = ">>>" if ln in hits_here else "   "
                        ctx_lines.append(f"{marker} {ln:>5}: {lines[idx]}")
                    match_blocks.append({
                        "match_lines":   hits_here,
                        "context_range": f"lines {ws}-{we} of {total_lines}",
                        "context":       "\n".join(ctx_lines)
                    })

                return {
                    "path":        file_path,
                    "url":         file_url,
                    "total_lines": total_lines,
                    "match_count": len(hit_lines),
                    "matches":     match_blocks,
                    "instruction": (
                        f"Use 'read_files' with url='{file_url}' and "
                        f"line_ranges to read specific sections."
                    )
                }
            else:
                # Fallback to GitHub's own fragment snippets
                fragments = [
                    {
                        "match_lines":   [],
                        "context_range": "(GitHub fragment — exact lines unknown)",
                        "context":       tm.get("fragment", "").strip()
                    }
                    for tm in item.get("text_matches", [])[:3]
                    if tm.get("fragment", "").strip()
                ]
                return {
                    "path":        file_path,
                    "url":         file_url,
                    "total_lines": None,
                    "match_count": len(fragments),
                    "matches":     fragments,
                    "instruction": (
                        f"Could not fetch file content directly. "
                        f"Use 'read_files' with url='{file_url}'."
                    )
                }

        enriched = await asyncio.gather(*[enrich(i) for i in items])

        return {
            "repository":  f"{p['owner']}/{p['repo']}",
            "query":       query,
            "total_count": data.get("total_count", len(items)),
            "returned":    len(items),
            "results":     list(enriched),
        }