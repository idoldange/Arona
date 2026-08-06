import aiohttp
import asyncio
from console import console

# Global list of created sessions (for diagnostics & graceful shutdown)
all_sessions: list[aiohttp.ClientSession] = []

# Patch ClientSession.__init__ to register sessions automatically
_original_init = aiohttp.ClientSession.__init__

def _patched_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    try:
        all_sessions.append(self)
    except Exception:
        pass

aiohttp.ClientSession.__init__ = _patched_init

class SessionManager:
    def __init__(self):
        self.sessions: list[aiohttp.ClientSession] = []
        self._lock = asyncio.Lock()
        self._max_sessions = 5

    async def get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            # Cleanup closed sessions
            before_cleanup = len(self.sessions)
            self.sessions = [s for s in self.sessions if not getattr(s, 'closed', True)]
            after_cleanup = len(self.sessions)

            # If no healthy sessions, create one
            if not self.sessions:
                session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=120, sock_read=60),
                    connector=aiohttp.TCPConnector(
                        limit=10,
                        ttl_dns_cache=300,
                        keepalive_timeout=60
                    )
                )
                self.sessions.append(session)
                console.log(f"[http_session] New session created (id={hex(id(session))})", "INFO")
                return session

            # Reuse existing session (round-robin / LRU simple approach)
            session = self.sessions.pop(0)
            self.sessions.append(session)
            return session

    async def close_all(self):
        async with self._lock:
            for session in self.sessions:
                if not getattr(session, 'closed', True):
                    try:
                        await session.close()
                    except Exception as e:
                        console.log(f"Error closing session in SessionManager: {e}", "WARN")
            self.sessions.clear()
            console.log("[http_session] SessionManager: all sessions closed", "INFO")


# Single instance to be imported by other modules
session_manager = SessionManager()


def register_session(s: aiohttp.ClientSession):
    """Manually register a session (keeps list for diagnostics)."""
    try:
        all_sessions.append(s)
    except Exception:
        pass
    return s

async def close_all_sessions():
    """Close both SessionManager-managed sessions and any other registered sessions."""
    await session_manager.close_all()
    closed_count = 0
    for s in list(all_sessions):
        try:
            if not getattr(s, 'closed', True):
                await s.close()
                closed_count += 1
        except Exception as e:
            console.log(f"Error closing session from all_sessions: {e}", "WARN")
    all_sessions.clear()
    console.log(f"[http_session] Closed {closed_count} registered sessions", "INFO")