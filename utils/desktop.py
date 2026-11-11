"""Virtual desktop per workspace for the Gemini Live voice window: Xvfb + openbox + tint2 (taskbar) inside the sandbox
container, running as the workspace's own uid (own xauth cookie, -nolisten tcp), so other workspaces cannot see it.

- open/close/adopt: one session per workspace (max N), recovered after a bot restart while the container lives.
- env_for(ws): DISPLAY/XAUTHORITY/DBUS for run_code / run_shell / daemons of that workspace (GUI windows appear).
- grab(ws): one JPEG frame (ffmpeg x11grab). start_stream(): ~1 fps MJPEG stream while something is watching.
- reaper_loop(): closes desktops that nothing has used for a while (frees RAM/CPU, not a run-time limit).
"""
import asyncio
import os
import re
import shlex
import time

from console import console

W, H = 1920, 1080
SOI, EOI = b"\xff\xd8", b"\xff\xd9"

_TINT2RC = """rounded = 0
border_width = 0
background_color = #1f2937 100
border_color = #1f2937 100

rounded = 4
border_width = 0
background_color = #374151 100
border_color = #374151 100

rounded = 4
border_width = 0
background_color = #2563eb 100
border_color = #2563eb 100

panel_monitor = all
panel_position = bottom center horizontal
panel_size = 100% 36
panel_margin = 0 0
panel_padding = 6 3 6
panel_dock = 0
wm_menu = 0
panel_layer = top
panel_background_id = 1
panel_items = TC
taskbar_mode = single_desktop
taskbar_padding = 0 0 4
task_text = 1
task_icon = 1
task_centered = 0
task_maximum_size = 240 28
task_padding = 8 3 4
task_font = sans 10
task_font_color = #ffffff 100
task_active_font_color = #ffffff 100
task_background_id = 2
task_active_background_id = 3
task_urgent_background_id = 3
task_iconified_background_id = 2
time1_format = %H:%M
time1_font = sans bold 11
clock_font_color = #ffffff 100
clock_padding = 8 0
clock_background_id = 0
"""
_KEEP_ENV = ("DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "NO_AT_BRIDGE", "GIO_USE_VFS", "GTK_A11Y")


class DesktopManager:
    def __init__(self, runner, max_desktops=2, idle_minutes=15):
        self.r, self.c = runner, runner.container
        self.max, self.idle = max(1, int(max_desktops)), max(1, int(idle_minutes)) * 60
        self.d = {}            # ws -> {"n","uid","run","dw","env","pid","last","opened"}
        self._streams = {}     # ws -> asyncio.Task
        self._locks = {}

    # ── plumbing ──
    def _ws(self, channel_id):
        return self.r._sanitize_msg_id(str(channel_id))

    def _geo(self, ws):
        uid = self.r._get_sandbox_uid(ws)
        return {"uid": uid, "n": 100 + uid % 800, "run": f"/tmp/desk/{ws}",
                "dw": f"{self.r.docker_workdir_base}/{ws}"}

    async def _dx(self, args, user=None, env=None, detach=False, timeout=20):
        cmd = ["docker", "exec"] + (["-d"] if detach else []) + (["-u", str(user)] if user is not None else [])
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        p = await asyncio.create_subprocess_exec(*cmd, self.c, *args, stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            try:
                p.kill()
            except Exception:
                pass
            return 124, b"timeout"
        return p.returncode, out

    @staticmethod
    def _parse_env(text):
        env = {}
        for line in text.splitlines():
            k, _, v = line.partition("=")
            if k in _KEEP_ENV and v:
                env[k] = v
        return env

    def env_for(self, ws):
        """Sync; cached. Env for GUI programs of this workspace, {} when no desktop is open."""
        info = self.d.get(ws)
        return dict(info["env"]) if info else {}

    def touch(self, channel_id):
        info = self.d.get(self._ws(channel_id))
        if info:
            info["last"] = time.time()

    async def _alive(self, info):
        rc, _ = await self._dx(["sh", "-c", self._live(info["run"])], user=info["uid"], timeout=8)
        return rc == 0

    @staticmethod
    def _live(run):
        """Shell snippet: exit 0 only if the session leader exists and is not a zombie."""
        return (f'P=$(cat {run}/pid 2>/dev/null) || exit 1; S=$(ps -o stat= -p "$P" 2>/dev/null); '
                'case "$S" in ""|Z*) exit 1;; esac')

    async def _adopt(self, ws):
        g = self._geo(ws)
        rc, out = await self._dx(["sh", "-c", f"test -f {g['run']}/ready && ({self._live(g['run'])}) && cat {g['run']}/env"],
                                 user=g["uid"], timeout=8)
        env = self._parse_env(out.decode("utf-8", "replace")) if rc == 0 else {}
        if not env.get("DISPLAY"):
            return False
        self.d[ws] = {**g, "env": env, "last": time.time(), "opened": time.time()}
        return True

    @staticmethod
    def _script(ws, g):
        return f"""#!/bin/sh
R={g['run']}
D={g['n']}
export HOME={g['dw']}/.desktop
export XDG_CONFIG_HOME=$HOME/.config XDG_CACHE_HOME=$HOME/.cache XDG_DATA_HOME=$HOME/.local/share XDG_RUNTIME_DIR=$R/run
mkdir -p $XDG_CONFIG_HOME $XDG_CACHE_HOME $XDG_DATA_HOME $XDG_RUNTIME_DIR
chmod 700 $XDG_RUNTIME_DIR
echo $$ > $R/pid
export XAUTHORITY=$R/xauth
: > $XAUTHORITY
xauth -f $XAUTHORITY add :$D . $(mcookie)
rm -f /tmp/.X11-unix/X$D /tmp/.X$D-lock
Xvfb :$D -screen 0 {W}x{H}x24 -nolisten tcp -auth $XAUTHORITY -noreset > $R/xvfb.log 2>&1 &
XP=$!
i=0
while [ $i -lt 100 ]; do
  [ -S /tmp/.X11-unix/X$D ] && xdpyinfo -display :$D >/dev/null 2>&1 && break
  sleep 0.1; i=$((i+1))
done
if [ $i -ge 100 ]; then echo "Xvfb did not come up" >> $R/xvfb.log; kill $XP 2>/dev/null; exit 1; fi
export DISPLAY=:$D
export NO_AT_BRIDGE=1 GTK_A11Y=none GIO_USE_VFS=local DBUS_SESSION_BUS_ADDRESS=disabled:
xsetroot -solid '#2b3a55'
openbox > $R/wm.log 2>&1 &
sleep 0.5
tint2 -c $HOME/.config/tint2/tint2rc > $R/panel.log 2>&1 &
sleep 0.5
{{ echo "DISPLAY=$DISPLAY"; echo "XAUTHORITY=$XAUTHORITY"; echo "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"; echo "NO_AT_BRIDGE=1"; echo "GIO_USE_VFS=local"; echo "GTK_A11Y=none"; }} > $R/env
echo ready > $R/ready
wait $XP
"""

    # ── lifecycle ──
    async def open(self, channel_id):
        """-> (ok, message)."""
        ws = self._ws(channel_id)
        async with self._locks.setdefault(ws, asyncio.Lock()):
            info = self.d.get(ws)
            if info and await self._alive(info):
                info["last"] = time.time()
                return True, "Desktop already open."
            self.d.pop(ws, None)
            if await self._adopt(ws):
                return True, "Desktop reopened."
            for k, v in list(self.d.items()):
                if not await self._alive(v):
                    self.d.pop(k, None)
            if len(self.d) >= self.max:
                return False, f"Too many desktops open ({len(self.d)}/{self.max}). Close one first."
            g = self._geo(ws)
            host_home = os.path.join(self.r.host_workdir_base, ws, ".desktop")
            try:
                os.makedirs(host_home, exist_ok=True)
                with open(os.path.join(host_home, "desk.sh"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(self._script(ws, g))
                os.makedirs(os.path.join(host_home, ".config", "tint2"), exist_ok=True)
                with open(os.path.join(host_home, ".config", "tint2", "tint2rc"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(_TINT2RC)
            except OSError as e:
                return False, f"Could not prepare the desktop: {e}"
            await self.r._prepare_sandbox_workdir(os.path.join(self.r.host_workdir_base, ws), g["dw"], g["uid"])
            await self._dx(["sh", "-c", f"rm -rf {g['run']}"], user=g["uid"], timeout=15)      # stale state of an older session
            await self._dx(["sh", "-c", f"mkdir -p /tmp/.X11-unix /tmp/desk; chmod 1777 /tmp/.X11-unix /tmp/desk; "
                            f"mkdir -p {g['run']}; chmod 700 {g['run']}; chown {g['uid']}:1000 {g['run']}; "
                            f"chown -R {g['uid']}:1000 {g['dw']}/.desktop"], user=0)
            rc, out = await self._dx(["sh", "-c", f"setsid sh {g['dw']}/.desktop/desk.sh >/dev/null 2>&1 &"],
                                     user=g["uid"], detach=True)     # backgrounded: tini (PID 1) adopts and reaps it
            if rc != 0:
                return False, f"Could not start the desktop: {out.decode('utf-8', 'replace')[:200]}"
            for _ in range(40):
                rc, out = await self._dx(["sh", "-c", f"test -f {g['run']}/ready && cat {g['run']}/env"], user=g["uid"], timeout=8)
                env = self._parse_env(out.decode("utf-8", "replace")) if rc == 0 else {}
                if env.get("DISPLAY"):
                    self.d[ws] = {**g, "env": env, "last": time.time(), "opened": time.time()}
                    console.log(f"[desktop] opened {ws} on {env['DISPLAY']}", "INFO")
                    return True, "Desktop opened."
                await asyncio.sleep(0.5)
            _, lg = await self._dx(["sh", "-c", f"tail -n 5 {g['run']}/*.log 2>&1"], user=g["uid"], timeout=8)
            await self._kill(ws, g)
            return False, "Desktop failed to start: " + lg.decode("utf-8", "replace")[-300:]

    async def _kill(self, ws, g):
        await self._dx(["sh", "-c", f"P=$(cat {g['run']}/pid 2>/dev/null); [ -n \"$P\" ] && kill -s KILL -- -$P 2>/dev/null; "
                        "pkill -f '[f]fmpeg.*x11grab' 2>/dev/null; "
                        f"rm -rf {g['run']}; true"], user=g["uid"], timeout=15)

    async def close(self, channel_id):
        return await self.close_ws(self._ws(channel_id))

    async def close_ws(self, ws):
        await self.stop_stream_ws(ws)
        info = self.d.pop(ws, None)
        if not info:
            return False
        await self._kill(ws, info)
        console.log(f"[desktop] closed {ws}", "INFO")
        return True

    async def close_all(self):
        for ws in list(self.d):
            try:
                await self.close_ws(ws)
            except Exception as e:
                console.log(f"[desktop] close {ws} failed: {e}", "WARN")

    def is_open(self, channel_id):
        return self._ws(channel_id) in self.d

    async def reaper_loop(self):
        while True:
            await asyncio.sleep(30)
            try:
                now = time.time()
                for ws, info in list(self.d.items()):
                    st = self._streams.get(ws)
                    if (st is None or st.done()) and now - info["last"] > self.idle:
                        console.log(f"[desktop] {ws} idle, closing", "INFO")
                        await self.close_ws(ws)
            except Exception as e:
                console.log(f"[desktop] reaper error: {e}", "ERROR")

    # ── capture ──
    def _ffmpeg(self, info, extra):
        env = []
        for k, v in info["env"].items():
            env += ["-e", f"{k}={v}"]
        return (["docker", "exec", "-u", str(info["uid"])] + env + [self.c, "ffmpeg", "-loglevel", "error", "-f", "x11grab",
                "-draw_mouse", "1", "-video_size", f"{W}x{H}"] + extra[0] + ["-i", info["env"]["DISPLAY"]] + extra[1])

    async def grab(self, channel_id):
        """One JPEG frame of the desktop, or None."""
        info = self.d.get(self._ws(channel_id))
        if not info:
            return None
        cmd = self._ffmpeg(info, ([], ["-frames:v", "1", "-q:v", "3", "-f", "mjpeg", "-"]))
        p = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), 15)
        except asyncio.TimeoutError:
            p.kill()
            return None
        return out if out[:2] == SOI else None

    async def start_stream(self, channel_id, on_frame, fps=1):
        ws = self._ws(channel_id)
        t = self._streams.get(ws)
        if t and not t.done():
            return
        if ws in self.d:
            self._streams[ws] = asyncio.create_task(self._stream(ws, on_frame, fps))

    async def stop_stream(self, channel_id):
        await self.stop_stream_ws(self._ws(channel_id))

    async def stop_stream_ws(self, ws):
        t = self._streams.pop(ws, None)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except BaseException:
                pass

    async def _stream(self, ws, on_frame, fps):
        fails = 0
        while ws in self.d and fails < 4:
            info = self.d[ws]
            cmd = self._ffmpeg(info, (["-framerate", str(fps)], ["-q:v", "3", "-f", "image2pipe", "-c:v", "mjpeg", "-"]))
            proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            got, buf = False, b""
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        a = buf.find(SOI)
                        if a < 0:
                            buf = b""
                            break
                        b = buf.find(EOI, a + 2)
                        if b < 0:
                            buf = buf[a:]
                            break
                        frame, buf = buf[a:b + 2], buf[b + 2:]
                        got = True
                        r = on_frame(frame)
                        if asyncio.iscoroutine(r):
                            await r
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass
                try:    # killing the docker client does not stop ffmpeg inside the container
                    await self._dx(["sh", "-c", "pkill -f '[f]fmpeg.*x11grab' 2>/dev/null; true"], user=info["uid"], timeout=8)
                except BaseException:
                    pass
            fails = 0 if got else fails + 1
            await asyncio.sleep(min(2 ** fails, 8))
        console.log(f"[desktop] stream for {ws} ended", "WARN")
