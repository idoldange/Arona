import asyncio
import os
import base64
import mimetypes
import shutil
import re
import json
import tempfile
import subprocess
from console import console
from config import DOCKER_DESKTOP_PATH

# Audit logger for execution events
import logging
import hashlib
from datetime import datetime, timedelta

audit_logger = logging.getLogger('audit')
if not audit_logger.handlers:
    audit_logger.setLevel(logging.INFO)
    handler = logging.FileHandler('audit.log')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    audit_logger.addHandler(handler)


class RateLimiter:
    def __init__(self, max_requests=10, window_seconds=60):
        self.requests = {}
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)

    def is_allowed(self, user_id: str) -> bool:
        now = datetime.now()
        lst = self.requests.get(user_id, [])
        # keep only recent
        lst = [t for t in lst if now - t < self.window]
        if len(lst) >= self.max_requests:
            self.requests[user_id] = lst
            return False
        lst.append(now)
        self.requests[user_id] = lst
        return True


rate_limiter = RateLimiter(max_requests=10, window_seconds=60)


_proxy_ready: bool | None = None  # None=unchecked, True=ready, False=down

async def _check_proxy_once(proxy_url: str = "http://127.0.0.1:8888") -> bool:
    """Check proxy connectivity once and cache the result. Called at first run_code invocation."""
    global _proxy_ready
    if _proxy_ready is not None:
        return _proxy_ready
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://example.com", proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                _proxy_ready = resp.status < 600
    except Exception:
        _proxy_ready = False
    if not _proxy_ready:
        console.log("[docker] Proxy not available — network calls inside sandbox may fail", "WARN")
    return _proxy_ready

class AronaDocker:
    """Manage execution of arbitrary code/commands inside a Docker container.

    Features:
    - Saves Discord attachments into a per-message workspace
    - Writes a small runner file and executes it inside the container
    - Collects outputs from a dedicated output directory and returns them as base64

    Security notes:
    - Filenames and message ids are sanitized to avoid path traversal
    - Shell command length is limited to avoid accidental DOS via very large commands
    """
    def __init__(self, container_name="arona_worker", timeout=120):
        self.container = container_name
        self.timeout = timeout
        
        # Base working directory on the host that will contain per-message workspaces
        self.host_workdir_base = os.path.abspath("./docker/workdir")
        # Container-side base mirrors the host workdir
        self.docker_workdir_base = "/app/workdir"

        os.makedirs(self.host_workdir_base, exist_ok=True)

        # Check if container is running
        try:
            result = subprocess.run(["docker", "ps", "--filter", f"name={self.container}", "--format", "{{.Names}}"], capture_output=True, text=True)
            if result.returncode == 0:
                if self.container in result.stdout.strip():
                    console.log(f"Container {self.container} is already running.", "INFO")
                else:
                    console.log(f"Container {self.container} is not running. Starting it.", "WARN")
                    start_result = subprocess.run(["docker", "start", self.container], capture_output=True, text=True)
                    if start_result.returncode == 0:
                        console.log(f"Container {self.container} started successfully.", "INFO")
                    else:
                        console.log(f"Failed to start container {self.container}: {start_result.stderr}", "ERROR")
            else:
                # Check if daemon not running
                if "docker api" in result.stderr.lower() or "daemon" in result.stderr.lower():
                    console.log("Docker daemon is not running. Attempting to start Docker Desktop.", "WARN")
                    subprocess.run([DOCKER_DESKTOP_PATH])
                else:
                    console.log(f"Error checking container status: {result.stderr}", "ERROR")
        except Exception as e:
            console.log(f"Error checking/starting container: {e}", "ERROR")

    def _sanitize_msg_id(self, msg_id: str) -> str:
        """Allow only a safe subset of characters for message IDs used as folder names.
        
        Blocks path traversal attempts like .. or absolute paths.
        """
        if not msg_id:
            return "unknown"
        # Only allow letters, digits and underscore. Disallow dots and dashes
        # to reduce traversal/hidden-file risks. Reject IDs that start with
        # an underscore so attacker cannot craft ambiguous names.
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', str(msg_id))
        if not safe or safe.startswith('_'):
            return "unknown"
        return safe[:64]

    def _sanitize_filename(self, filename):
        """Bảo mật: Chỉ cho phép chữ, số, dấu chấm, gạch ngang/dưới.

        Tránh lỗi nếu filename là None và luôn trả về tên hợp lệ.
        """
        if not filename:
            return "unknown_file"
        filename = os.path.basename(str(filename))
        # Prevent hidden filenames (starting with dot)
        clean_name = re.sub(r'[^a-zA-Z0-9._-]', '', filename)
        if clean_name.startswith('.'):
            clean_name = f"_{clean_name.lstrip('.') }"
        return clean_name if clean_name else "unknown_file"

    async def cleanup_files(self, file_ids: list[str] | None = None, channel_id: str | None = None) -> dict:
        """
        Model-callable cleanup. Deletes specific workdir sub-folders (by sanitized ID).

        Parameters
        file_ids   : list of msg_id / channel_id strings whose workdirs to delete.
                     Pass empty list or None to delete the entire channel workspace if channel_id given.
        channel_id : if provided and file_ids is empty, deletes the whole channel workspace.

        Returns dict with 'deleted' and 'errors' lists.
        """
        deleted = []
        errors = []

        targets = []
        if file_ids:
            targets = [self._sanitize_msg_id(str(fid)) for fid in file_ids]
        elif channel_id:
            targets = [self._sanitize_msg_id(str(channel_id))]

        for safe_id in targets:
            host_path = os.path.join(self.host_workdir_base, safe_id)
            docker_path = f"{self.docker_workdir_base}/{safe_id}"
            if os.path.exists(host_path):
                try:
                    await self._docker_rm_workdir(docker_path)
                    shutil.rmtree(host_path, ignore_errors=True)
                    deleted.append(safe_id)
                    console.log(f"[docker] cleanup_files: deleted workdir {safe_id}", "INFO")
                except Exception as e:
                    errors.append({"id": safe_id, "error": str(e)})
            else:
                errors.append({"id": safe_id, "error": "Workdir not found"})

        return {"deleted": deleted, "errors": errors}

    async def cleanup_stale_workdirs(self, max_age_days: int = 30) -> dict:
        """Remove temp=False workdirs not modified in max_age_days days (based on folder mtime)."""
        deleted = []
        errors = []
        cutoff = datetime.now() - timedelta(days=max_age_days)
        try:
            for entry in os.scandir(self.host_workdir_base):
                if not entry.is_dir():
                    continue
                try:
                    mtime = datetime.fromtimestamp(entry.stat().st_mtime)
                    if mtime < cutoff:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        deleted.append(entry.name)
                        console.log(f"[docker] Stale workdir removed: {entry.name} (last modified: {mtime.date()})", "INFO")
                except Exception as e:
                    errors.append({"id": entry.name, "error": str(e)})
        except Exception as e:
            console.log(f"[docker] cleanup_stale_workdirs error: {e}", "ERROR")
        return {"deleted": deleted, "errors": errors}

    def move_workdir_file(self, channel_id: str, filename: str, direction: str) -> dict:
        """
        Move a file between the channel workdir and database/files/persistent/.

        Parameters
        channel_id : sanitized channel workspace ID.
        filename   : file name inside the workdir (or persistent dir).
        direction  : "persist" — workdir/outputs/<filename> → database/files/persistent/<filename>
                     "stage"   — database/files/persistent/<filename> → workdir/<channel_id>/<filename>

        Returns dict with 'new_path' or 'error'.
        """
        PERSISTENT_DIR = os.path.abspath("database/files/persistent")
        os.makedirs(PERSISTENT_DIR, exist_ok=True)

        safe_channel = self._sanitize_msg_id(str(channel_id))
        channel_workdir = os.path.join(self.host_workdir_base, safe_channel)
        safe_filename = self._sanitize_filename(filename)

        if direction == "persist":
            src = os.path.join(channel_workdir, "outputs", safe_filename)
            if not os.path.exists(src):
                src = os.path.join(channel_workdir, safe_filename)
            if not os.path.exists(src):
                return {"error": f"File '{safe_filename}' not found in workdir for channel {channel_id}"}
            # Realpath check: prevent traversal escaping the channel workdir
            resolved_src = os.path.realpath(src)
            resolved_base = os.path.realpath(channel_workdir)
            if not resolved_src.startswith(resolved_base + os.sep):
                console.log(f"[docker] move_workdir_file: path traversal blocked for {filename}", "WARN")
                return {"error": "Invalid filename: path traversal detected"}
            dest = os.path.join(PERSISTENT_DIR, safe_filename)
            try:
                os.makedirs(PERSISTENT_DIR, exist_ok=True)
                shutil.move(src, dest)
                console.log(f"[docker] Persisted {filename} → {dest}", "INFO")
                return {"new_path": os.path.abspath(dest)}
            except Exception as e:
                return {"error": f"Failed to persist: {e}"}

        elif direction == "stage":
            src = os.path.join(PERSISTENT_DIR, safe_filename)
            if not os.path.exists(src):
                return {"error": f"Persistent file '{safe_filename}' not found"}
            os.makedirs(channel_workdir, exist_ok=True)
            dest = os.path.join(channel_workdir, safe_filename)
            try:
                shutil.move(src, dest)
                console.log(f"[docker] Staged {filename} → {dest}", "INFO")
                return {"new_path": os.path.abspath(dest)}
            except Exception as e:
                return {"error": f"Failed to stage: {e}"}
        else:
            return {"error": f"Unknown direction '{direction}'. Use 'persist' or 'stage'."}

    async def cleanup_by_msg_id(self, msg_id: str):
        """
        Dọn dẹp thư mục dựa trên Message ID.
        Không gây lỗi, chỉ log kết quả ra console.
        """
        msg_id = self._sanitize_msg_id(str(msg_id))
        host_path = os.path.join(self.host_workdir_base, msg_id)
        docker_path = f"{self.docker_workdir_base}/{msg_id}"

        if os.path.exists(host_path):
            # Dir may be mode 700 owned by sandbox UID — remove via container root
            await self._docker_rm_workdir(docker_path)
            # Fallback host-side cleanup for any remaining host-owned remnants
            shutil.rmtree(host_path, ignore_errors=True)
            console.log(f"Successfully cleaned up resources for message ID: {msg_id}", "INFO")
        else:
            console.log(f"Directory not found for cleanup: {host_path}", "WARN")

    async def _process_discord_attachments(self, message, target_dir) -> list:
        """Download files from a Discord message and save into target_dir.

        Returns a list of saved filenames.
        Ensures target_dir exists and avoids overwriting existing files by adding a numeric suffix.
        """
        saved = []
        if not message or not getattr(message, 'attachments', None):
            return saved

        # Ensure target directory exists
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            console.log(f"Failed to ensure target dir {target_dir}: {e}", "WARN")

        # Allowed extensions whitelist to avoid saving executables/shared objects
        ALLOWED_EXTENSIONS = {'.txt', '.py', '.json', '.csv', '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.md', '.log', '.zip', '.rar', '.csv', '.xlsx', '.pptx', '.docx', '.mp4', '.mp3', '.wav', '.ogg', '.flac', '.avi', '.mkv', '.mov', '.webm', '.mid', '.iso', '.bin', '.exe', '.dll', '.so', ''} #bro this is ubuntu not windows, we can allow .exe and .dll as data files without risk of execution on the host
        for att in message.attachments:
            # Skip very large attachments
            if getattr(att, 'size', 0) > 25 * 1024 * 1024:
                console.log(f"Skipping large attachment {getattr(att,'filename', 'unknown')} (>25MB)", "WARN")
                continue

            base_name = self._sanitize_filename(getattr(att, 'filename', None))
            name, ext = os.path.splitext(base_name)
            ext = ext.lower()
            if ext not in ALLOWED_EXTENSIONS:
                console.log(f"Rejected dangerous extension: {ext} for {base_name}", "WARN")
                continue

            try:
                # Create a unique temp file in the target dir atomically to avoid TOCTOU
                fd, temp_path = tempfile.mkstemp(prefix=name + '_', suffix=ext, dir=target_dir)
                os.close(fd)

                # Ensure temp_path is inside target_dir
                resolved_save = os.path.realpath(temp_path)
                resolved_target = os.path.realpath(target_dir)
                if not resolved_save.startswith(resolved_target + os.sep) and resolved_save != resolved_target:
                    console.log(f"Rejected: {base_name} would escape target dir", "DEBUG")
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                    continue

                # Save attachment into the temp path (discord.py Attachment.save is awaitable)
                await att.save(temp_path)
                saved_name = os.path.basename(temp_path)
                saved.append(saved_name)
                console.log(f"Saved attachment to {temp_path}", "INFO")
            except Exception as e:
                console.log(f"Failed to save attachment {base_name}: {e}", "DEBUG")
        return saved
    async def _get_outputs(self, target_dir):
        """Thu thập file output từ container."""
        MAX_OUTPUT_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file
        results = []
        if not os.path.exists(target_dir): return results
        
        for filename in os.listdir(target_dir):
            path = os.path.join(target_dir, filename)
            if os.path.isfile(path):
                try:
                    file_size = os.path.getsize(path)
                    if file_size > MAX_OUTPUT_FILE_SIZE:
                        console.log(f"[docker] Skipping oversized output: {filename} ({file_size >> 20} MB > 50 MB limit)", "WARN")
                        continue
                    with open(path, "rb") as f:
                        content = f.read()
                        results.append({
                            "filename": filename,
                            "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
                            "b64": base64.b64encode(content).decode('utf-8')
                        })
                except Exception:
                    pass
        return results



    async def _kill_container_workdir_procs(self, docker_cwd: str):
        """Kill any processes inside the container still running in docker_cwd.

        Called after a timeout so orphaned git-clone / npm-install / etc. don't
        keep the workdir busy and prevent cleanup on the host side.
        Uses `fuser -k` on the directory and a pkill fallback.
        """
        try:
            kill_cmd = [
                "docker", "exec", self.container,
                "sh", "-c",
                # fuser -k sends SIGKILL to every process with an open file in
                # that directory tree; pkill -9 catches any process whose cwd
                # starts with the path (belt-and-suspenders).
                f"fuser -k {docker_cwd} 2>/dev/null; "
                f"pkill -9 -f {docker_cwd} 2>/dev/null; true"
            ]
            proc = await asyncio.create_subprocess_exec(
                *kill_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception as e:
            console.log(f"_kill_container_workdir_procs failed (non-fatal): {e}", "WARN")

    # Workdir isolation helpers
    SANDBOX_UID_MIN = 2000
    SANDBOX_UID_MAX = 9999  # pool of 8000 UIDs — no /etc/passwd entries needed

    # -
    # VIEW_DIR: a sibling of OUTPUT_DIR. Anything the model copies/writes here
    # (image/audio/video only) gets base64-encoded and handed back as Gemini
    # inline_data parts so the model can actually *see/hear* the file instead
    # of just trusting a filename. Files are consumed (deleted) once read so
    # they don't get re-attached on the next run_code call in the same workspace.
    # -
    VIEWABLE_LIMITS = {
        "image/": 8 * 1024 * 1024,   # 8 MB per image
        "audio/": 15 * 1024 * 1024,  # 15 MB per audio file
        "video/": 15 * 1024 * 1024,  # 15 MB per video file
    }
    MAX_VIEW_FILES_PER_CALL = 4
    MAX_VIEW_TOTAL_BYTES = 20 * 1024 * 1024  # stay under Gemini's ~20MB inline-data request cap

    async def _get_view_files(self, target_dir: str) -> list:
        """Scan VIEW_DIR for image/audio/video files, base64-encode them, then delete
        the originals from the sandbox so they aren't picked up again on the next call.

        Non-viewable files (wrong mime, too large) are left untouched in place — the
        model can still retrieve them normally via OUTPUT_DIR/send_output instead.
        """
        results = []
        if not os.path.exists(target_dir):
            return results

        total_bytes = 0
        for filename in sorted(os.listdir(target_dir)):
            if len(results) >= self.MAX_VIEW_FILES_PER_CALL:
                console.log(f"[docker] VIEW_DIR: per-call file limit ({self.MAX_VIEW_FILES_PER_CALL}) reached, leaving the rest", "WARN")
                break

            path = os.path.join(target_dir, filename)
            if not os.path.isfile(path):
                continue

            mime = mimetypes.guess_type(path)[0] or ""
            prefix = next((p for p in self.VIEWABLE_LIMITS if mime.startswith(p)), None)
            if not prefix:
                continue  # not an image/audio/video — not for VIEW_DIR, skip silently

            try:
                size = os.path.getsize(path)
                if size > self.VIEWABLE_LIMITS[prefix]:
                    console.log(f"[docker] VIEW_DIR: {filename} too large ({size >> 20}MB), skipped", "WARN")
                    continue
                if total_bytes + size > self.MAX_VIEW_TOTAL_BYTES:
                    console.log(f"[docker] VIEW_DIR: total size budget exceeded, stopping at {len(results)} file(s)", "WARN")
                    break

                with open(path, "rb") as f:
                    content = f.read()
                results.append({
                    "filename": filename,
                    "mime": mime,
                    "b64": base64.b64encode(content).decode("utf-8"),
                    "size_bytes": size,
                })
                total_bytes += size
            except Exception as e:
                console.log(f"[docker] VIEW_DIR: failed to read {filename}: {e}", "WARN")
            finally:
                # Consume regardless of success/failure so a bad file doesn't loop forever
                try:
                    os.remove(path)
                except Exception:
                    pass

        return results

    async def view_workspace_file(self, channel_id: str, filename: str) -> dict:
        """On-demand peek at an image/audio/video file that already sits in a
        *persistent* (temp=false) run_code workspace — no code execution needed.

        Unlike VIEW_DIR (which is a write-then-consume staging folder for files
        the model *just produced*), this looks directly at files already saved
        from earlier turns: workspace root, outputs/, and view/ subfolders, in
        that order. Files are left in place (not deleted) since this is a
        read-only peek, not a one-shot hand-off.

        Returns: {"status": "ok", "view_files": [ {filename, mime, b64, size_bytes} ]}
              or {"status": "error", "log": "..."}
        """
        if not channel_id:
            return {"status": "error", "log": "No channel_id — view_workspace_file only works against persistent (temp=false) workspaces.", "view_files": []}

        workspace_key = self._sanitize_msg_id(str(channel_id))
        host_w = os.path.join(self.host_workdir_base, workspace_key)
        safe_filename = self._sanitize_filename(filename)

        search_dirs = [host_w, os.path.join(host_w, "outputs"), os.path.join(host_w, "view")]
        path = next((p for p in (os.path.join(d, safe_filename) for d in search_dirs) if os.path.isfile(p)), None)

        if not path:
            return {"status": "error", "log": f"'{filename}' not found in this channel's persistent workspace (checked root/outputs/view).", "view_files": []}

        mime = mimetypes.guess_type(path)[0] or ""
        prefix = next((p for p in self.VIEWABLE_LIMITS if mime.startswith(p)), None)
        if not prefix:
            return {"status": "error", "log": f"'{filename}' has mime type '{mime or 'unknown'}' — only image/audio/video files can be viewed. Use OUTPUT_DIR/send_files for other file types.", "view_files": []}

        try:
            size = os.path.getsize(path)
            if size > self.VIEWABLE_LIMITS[prefix]:
                return {"status": "error", "log": f"'{filename}' is {size >> 20}MB, over the viewable limit for {prefix.rstrip('/')} files.", "view_files": []}

            with open(path, "rb") as f:
                content = f.read()

            return {
                "status": "ok",
                "view_files": [{
                    "filename": safe_filename,
                    "mime": mime,
                    "b64": base64.b64encode(content).decode("utf-8"),
                    "size_bytes": size,
                }],
            }
        except Exception as e:
            console.log(f"[docker] view_workspace_file failed for {filename}: {e}", "WARN")
            return {"status": "error", "log": f"Failed to read '{filename}': {e}", "view_files": []}

    def _get_sandbox_uid(self, workspace_key: str) -> int:
        """Return a UID for this workspace_key, guaranteed unique (not just
        low-collision-probability) and stable across restarts.

        Uses database/files/uid_map.json as a persistent workspace_key -> uid
        table. First call for a given key reserves the next free slot in
        [SANDBOX_UID_MIN, SANDBOX_UID_MAX]; later calls reuse it. This avoids
        the birthday-paradox collision risk of the old `hash(key) % pool`
        scheme, where two unrelated channels could end up sharing a UID and
        therefore able to read/write each other's chmod-700 workdir inside
        the container.
        """
        import threading
        if not hasattr(self, "_uid_lock"):
            self._uid_lock = threading.Lock()

        uid_map_path = os.path.abspath("database/files/uid_map.json")
        os.makedirs(os.path.dirname(uid_map_path), exist_ok=True)

        with self._uid_lock:
            try:
                with open(uid_map_path, "r", encoding="utf-8") as f:
                    uid_map = json.load(f)
            except Exception:
                uid_map = {}

            if workspace_key in uid_map:
                return uid_map[workspace_key]

            used = set(uid_map.values())
            for candidate in range(self.SANDBOX_UID_MIN, self.SANDBOX_UID_MAX + 1):
                if candidate not in used:
                    uid_map[workspace_key] = candidate
                    tmp_path = uid_map_path + ".tmp"
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(uid_map, f)
                    os.replace(tmp_path, uid_map_path)
                    return candidate

            # Pool exhausted (>8000 concurrent workspaces) - fail loudly
            # instead of silently falling back to a colliding UID.
            raise RuntimeError("Sandbox UID pool exhausted - increase SANDBOX_UID_MAX")

    async def _prepare_sandbox_workdir(self, host_w: str, docker_w: str, uid: int) -> None:
        """Lock down a workdir so only `uid` can enter it inside the container.

        MUST be called AFTER all host-side file writes (code files, attachments)
        because chmod 700 prevents further writes from non-root host processes.

        Resulting layout:
          workdir/         → chown uid:1000, chmod 700  (only uid can enter/read)
          workdir/outputs/ → chown uid:1000, chmod 755  (uid writes; host can read)
        """
        os.makedirs(os.path.join(host_w, "outputs"), exist_ok=True)
        os.makedirs(os.path.join(host_w, "view"), exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-u", "0", self.container,
                "sh", "-c",
                f"chown {uid}:1000 {docker_w} {docker_w}/outputs {docker_w}/view "
                f"&& chmod 700 {docker_w} "
                f"&& chmod 755 {docker_w}/outputs {docker_w}/view",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception as e:
            console.log(f"[docker] _prepare_sandbox_workdir uid={uid}: {e}", "WARN")

    async def _docker_rm_workdir(self, docker_w: str) -> None:
        """Remove a workdir inside the container running as root.

        Safer than shutil.rmtree on the host because the dir may be mode 700
        owned by a sandbox UID that the host process has no permission to delete.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-u", "0", self.container,
                "rm", "-rf", "--", docker_w,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception as e:
            console.log(f"[docker] _docker_rm_workdir {docker_w}: {e}", "WARN")

    async def run_from_message(self, message, code_content=None) -> dict:
        """Execute code embedded in a Discord message and return a standardized result dict.

        The returned dict contains: {status, log, files, code, msg_id}
        """
        msg_id = self._sanitize_msg_id(str(getattr(message, 'id', 'unknown')))
        
        # Setup directories
        host_w = os.path.join(self.host_workdir_base, msg_id)
        host_o = os.path.join(host_w, "outputs")
        uid = self._get_sandbox_uid(msg_id)
        os.makedirs(host_w, exist_ok=True)

        docker_cwd = f"{self.docker_workdir_base}/{msg_id}"
        docker_out = f"{docker_cwd}/outputs"
        try:
            # Rate limiting per user (if message provided)
            try:
                author_id = str(message.author.id)
            except Exception:
                author_id = None
            if author_id and not rate_limiter.is_allowed(author_id):
                return {"status": "error", "log": "Rate limit exceeded. Try again later.", "files": [], "code": code_content or "", "msg_id": msg_id}
            # 1. Save attachments into the working directory and log if any
            saved_attachments = await self._process_discord_attachments(message, host_w)
            if saved_attachments:
                console.log(f"Saved attachments for msg {msg_id}: {saved_attachments}", "INFO")
            
            # 2. Prepare code
            if not code_content:
                code_content = getattr(message, 'content', '')
            code_content = re.sub(r'^```(python)?|```$', '', code_content, flags=re.MULTILINE).strip()
            setup_code = (
                f"import os\n"
                f"OUTPUT_DIR = {json.dumps(docker_out)}\n"
                f"if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)\n"
                f"def save_file(name, data):\n"
                f"    with open(os.path.join(OUTPUT_DIR, name), 'wb') as f: f.write(data)\n"
            )
            final_code = setup_code + "\n" + code_content
            
            # Write code to file safely
            exec_path = os.path.join(host_w, "exec_code.py")
            with open(exec_path, "w", encoding="utf-8") as f:
                f.write(final_code)

            # Lock workdir to sandbox UID (must be after all host-side writes)
            await self._prepare_sandbox_workdir(host_w, docker_cwd, uid)
            
            # 3. Execute inside container
            cmd = [
                "docker", "exec",
                "-u", str(uid),
                "-e", f"OUTPUT_DIR={docker_out}",
                "-e", "HOME=/home/sandboxuser",
                self.container,
                "sh", "-c",
                f"mkdir -p {docker_out} && cd {docker_cwd} && python3 exec_code.py"
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                # Audit log execution
                try:
                    code_hash = hashlib.sha256((code_content or "").encode('utf-8', errors='ignore')).hexdigest()[:16]
                except Exception:
                    code_hash = "-"
                console.log(f"EXEC | user={author_id or '-'} | msg_id={msg_id} | action=run_from_message | code_hash={code_hash}")

                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
                log = (stdout + stderr).decode('utf-8', errors='ignore').strip()
                status = "success"
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                log = f"Error: Execution exceeded {self.timeout} seconds limit."
                status = "timeout"

            return {
                "status": status,
                "log": log,
                "files": await self._get_outputs(host_o),
                "code": code_content,
                "msg_id": msg_id
            }
            
        except Exception as e:
            console.log(f"Core execution error: {str(e)}", "DEBUG")
            return {"status": "error", "log": str(e), "files": [], "code": code_content if 'code_content' in locals() else '', "msg_id": msg_id}
    
    async def run_code(self, code: str, msg_id: str, filename: str = "exec_code.py", message=None, timeout: int = 120, channel_id: str | None = None) -> dict:
        """Execute a code string in the container.

        Workspace key priority: channel_id (if given) > msg_id.
        Using channel_id makes the workspace persist across messages in the same channel,
        so files created in one run are available to subsequent runs.

        If `message` is provided (a Discord message-like object), attachments will be saved into the workspace.
        Returns a dict: {status, log, files, code, msg_id}
        """
        self.timeout = timeout
        workspace_key = self._sanitize_msg_id(str(channel_id)) if channel_id else self._sanitize_msg_id(str(msg_id))
        msg_id = self._sanitize_msg_id(str(msg_id))
        host_w = os.path.join(self.host_workdir_base, workspace_key)
        host_o = os.path.join(host_w, "outputs")
        host_v = os.path.join(host_w, "view")
        os.makedirs(host_w, exist_ok=True)
        os.makedirs(host_o, exist_ok=True)
        os.makedirs(host_v, exist_ok=True)

        docker_cwd = f"{self.docker_workdir_base}/{workspace_key}"
        docker_out = f"{docker_cwd}/outputs"
        docker_view = f"{docker_cwd}/view"
        try:
            # Rate limiting per user
            try:
                author_id = str(message.author.id)
            except Exception:
                author_id = None
            if author_id and not rate_limiter.is_allowed(author_id):
                return {"status": "error", "log": "Rate limit exceeded. Try again later.", "files": [], "view_files": [], "code": code, "msg_id": msg_id}

            # If a message object with attachments is passed, save them
            if message:
                saved_attachments = await self._process_discord_attachments(message, host_w)
                if saved_attachments:
                    console.log(f"Saved attachments for msg {msg_id}: {saved_attachments}", "INFO")

            await _check_proxy_once()  # one-time check, cached
            setup_code = (
                f"import os\n"
                f"OUTPUT_DIR = {json.dumps(docker_out)}\n"
                f"VIEW_DIR = {json.dumps(docker_view)}\n"
                f"if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)\n"
                f"if not os.path.exists(VIEW_DIR): os.makedirs(VIEW_DIR)\n"
                f"def save_file(name, data):\n"
                f"    with open(os.path.join(OUTPUT_DIR, name), 'wb') as f: f.write(data)\n"
                f"def view_file(name, data):\n"
                f"    # Save image/audio/video bytes here to have them attached as inline_data\n"
                f"    # so you can literally see/hear the content in your next turn.\n"
                f"    with open(os.path.join(VIEW_DIR, name), 'wb') as f: f.write(data)\n"
            )
            final_code = setup_code + "\n" + code
            
            # Sanitize filename
            safe_filename = self._sanitize_filename(filename)

            # Save code to file to run
            # NOTE: same CRLF fix as run_shell — force LF line endings so the
            # file behaves consistently when the container's python3 reads it,
            # regardless of the host OS's default newline translation.
            with open(os.path.join(host_w, safe_filename), "w", encoding="utf-8", newline='\n') as f:
                f.write(final_code)
                
            # Execute via Docker Exec
            cmd = [
                "docker", "exec",
                "-u", "sandboxuser",
                "-e", f"OUTPUT_DIR={docker_out}",
                "-e", f"VIEW_DIR={docker_view}",
                "-e", "HOME=/home/sandboxuser",
                self.container,
                "sh", "-c",
                f"mkdir -p {docker_out} {docker_view} && cd {docker_cwd} && python3 {safe_filename}"
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                # Audit log
                try:
                    code_hash = hashlib.sha256((code or "").encode('utf-8', errors='ignore')).hexdigest()[:16]
                except Exception:
                    code_hash = "-"
                console.log(f"EXEC | user={author_id or '-'} | msg_id={msg_id} | action=run_code | code_hash={code_hash}")

                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
                log = (stdout + stderr).decode('utf-8', errors='ignore').strip()
                status = "success"
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                # BUGFIX: killing docker-exec on the host doesn't stop the process
                # running inside the container (e.g. a git clone). Kill it explicitly.
                await self._kill_container_workdir_procs(docker_cwd)
                log = f"Error: Execution exceeded {self.timeout} seconds limit."
                status = "timeout"

            return {
                "status": status,
                "log": log,
                "files": await self._get_outputs(host_o),
                "view_files": await self._get_view_files(host_v),
                "code": code,
                "msg_id": msg_id
            }
            
        except Exception as e:
            console.log(f"Core execution error: {str(e)}", "ERROR")
            return {"status": "error", "log": str(e), "files": [], "view_files": [], "code": code, "msg_id": msg_id}
    
    async def run_shell(self, shell_cmd: str, msg_id: str, message=None, timeout: int = 120, channel_id: str | None = None) -> dict:
        """Execute a shell command inside the container.

        Workspace key priority: channel_id (if given) > msg_id.
        Using channel_id makes the workspace persist across messages.
        If `message` (Discord message) is provided, its attachments will be saved into the working directory
        prior to executing the command. Returns dict {status, log, files, command, msg_id}.
        """
        # Basic sanitization
        if not shell_cmd or '\x00' in shell_cmd:
            return {"status": "error", "log": "Invalid shell command provided.", "files": [], "view_files": [], "command": shell_cmd, "msg_id": str(msg_id)}

        if len(shell_cmd) > 2000:
            return {"status": "error", "log": "Shell command too long.", "files": [], "view_files": [], "command": shell_cmd, "msg_id": str(msg_id)}

        # Auto-add -c 4 to ping if no count specified, to prevent infinite loop
        shell_cmd = re.sub(r'(?<![\w-])(ping)(?!\s+-\w*c)\s+', r'\1 -c 4 ', shell_cmd)

        self.timeout = timeout
        workspace_key = self._sanitize_msg_id(str(channel_id)) if channel_id else self._sanitize_msg_id(str(msg_id))
        msg_id = self._sanitize_msg_id(str(msg_id))
        uid = self._get_sandbox_uid(workspace_key)
        host_w = os.path.join(self.host_workdir_base, workspace_key)
        host_o = os.path.join(host_w, "outputs")
        host_v = os.path.join(host_w, "view")
        os.makedirs(host_w, exist_ok=True)

        docker_cwd = f"{self.docker_workdir_base}/{workspace_key}"
        docker_out = f"{docker_cwd}/outputs"
        docker_view = f"{docker_cwd}/view"
        try:
            # Rate limiting per user
            try:
                author_id = str(message.author.id)
            except Exception:
                author_id = None
            if author_id and not rate_limiter.is_allowed(author_id):
                return {"status": "error", "log": "Rate limit exceeded. Try again later.", "files": [], "view_files": [], "command": shell_cmd, "msg_id": msg_id}

            # Save any attachments first
            if message:
                saved = await self._process_discord_attachments(message, host_w)
                if saved:
                    console.log(f"Saved attachments for shell run {msg_id}: {saved}", "INFO")

            # Save the command to a file for logging/auditing
            # NOTE: normalize \r\n/\r -> \n first (in case CR sneaks in from the
            # model output or Discord paste), and use newline='\n' on open so
            # Python's text-mode translation doesn't reintroduce \r\n on Windows
            # hosts. A stray \r surviving into the container's sh invocation
            # corrupts arguments (e.g. "tail -n 20" -> "tail -n 20\r" -> error).
            shell_cmd_normalized = shell_cmd.replace('\r\n', '\n').replace('\r', '\n')
            with open(os.path.join(host_w, "command.sh"), "w", encoding="utf-8", newline='\n') as f:
                f.write(shell_cmd_normalized)

            # Lock workdir to sandbox UID (must be after all host-side writes)
            await self._prepare_sandbox_workdir(host_w, docker_cwd, uid)

            # Execute via Docker using exec (no shell wrapper on host).
            # shell_cmd is written to command.sh and executed from there —
            # never interpolated into the command list to prevent injection.
            cmd = [
                "docker", "exec", "-u", str(uid),
                "-e", f"OUTPUT_DIR={docker_out}",
                "-e", f"VIEW_DIR={docker_view}",
                "-e", "HOME=/home/sandboxuser",
                self.container,
                "sh", "-c", f"mkdir -p {docker_out} {docker_view} && cd {docker_cwd} && sh command.sh"
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                # Audit log
                try:
                    cmd_hash = hashlib.sha256((shell_cmd or "").encode('utf-8', errors='ignore')).hexdigest()[:16]
                except Exception:
                    cmd_hash = "-"
                console.log(f"EXEC | user={author_id or '-'} | msg_id={msg_id} | action=run_shell | cmd_hash={cmd_hash}")

                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
                log = (stdout + stderr).decode('utf-8', errors='ignore').strip()
                status = "success"
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                # BUGFIX: same as run_code — kill orphaned container processes
                await self._kill_container_workdir_procs(docker_cwd)
                log = f"Error: Execution exceeded {self.timeout} seconds limit."
                status = "timeout"

            return {
                "status": status,
                "log": log,
                "files": await self._get_outputs(host_o),
                "view_files": await self._get_view_files(host_v),
                "command": shell_cmd,
                "msg_id": msg_id
            }
            
        except Exception as e:
            console.log(f"Core execution error: {str(e)}", "DEBUG")
            return {"status": "error", "log": str(e), "files": [], "view_files": [], "command": shell_cmd, "msg_id": msg_id}
        
    async def run_shell_from_message(self, message, shell_cmd: str) -> dict:
        """Execute a shell command coming from a Discord message, saving attachments first."""
        msg_id = self._sanitize_msg_id(str(getattr(message, 'id', 'unknown')))
        # Save attachments and run the command in the same workspace
        return await self.run_shell(shell_cmd, msg_id, message=message)