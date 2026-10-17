"""Counters for the voice path, graphed by the debug TensorBoard (Call UI page, tags under voice/).

Imports nothing from the bot, so gemini.py / record.py / window.py / debug_panel.py can all use it without cycles.
Written from the event loop and from the voice-receive thread; plain int/float updates are fine here (GIL) and
nothing in this file blocks.
"""
import logging
import re
import time
from typing import List, Tuple

UNANSWERED_CAP_S = 300.0     # "waiting for the model" gauge stops growing here


class VoiceMetrics:
    def __init__(self) -> None:
        # audio
        self.discord_rx_bytes = 0          # 16 kHz mono PCM16 decoded from Discord, counted BEFORE the silence gate
        # model liveness / latency
        self.gemini_last_rx = 0.0          # monotonic time of the last message of any kind from Gemini
        self.last_user_chunk = 0.0         # monotonic time of the last audio chunk sent to Gemini
        self.awaiting = False              # user audio sent since the model last answered
        self._latencies: List[Tuple[float, float]] = []   # (wall time, seconds) not yet written to TensorBoard
        # events
        self.gemini_ws_closes = 0
        self.gemini_ws_last_close_code = 0
        self.voice_disconnects = 0
        self.voice_reconnects = 0
        self.voice_connect_fails = 0
        self.voice_last_fail_code = 0
        self.dave_drops = 0                # undecryptable frames dropped (DAVE E2EE)
        self.crypto_errors = 0             # "CryptoError decoding packet data"
        self.opus_errors = 0
        self.packets_lost = 0
        # window renderer
        self.frames_sent = 0
        self._render_n = 0
        self._render_ms_sum = 0.0
        self._render_ms_max = 0.0
        self._jpeg_bytes_sum = 0

    # ── audio ──
    def discord_audio(self, nbytes: int) -> None:
        self.discord_rx_bytes += nbytes

    # ── model liveness / latency ──
    def gemini_listening(self) -> None:
        """A fresh Gemini listen loop started: the age gauge restarts from now."""
        self.gemini_last_rx = time.monotonic()
        self.awaiting = False

    def gemini_message(self) -> None:
        self.gemini_last_rx = time.monotonic()

    def user_chunk(self) -> None:
        self.last_user_chunk = time.monotonic()
        self.awaiting = True

    def model_output(self) -> None:
        """First audio/tool call from the model after the user spoke: closes the latency measurement."""
        if self.awaiting:
            self._latencies.append((time.time(), max(0.0, time.monotonic() - self.last_user_chunk)))
            self.awaiting = False

    def pop_latencies(self) -> List[Tuple[float, float]]:
        out, self._latencies = self._latencies, []
        return out

    def msg_age_s(self) -> float:
        return max(0.0, time.monotonic() - self.gemini_last_rx) if self.gemini_last_rx else 0.0

    def unanswered_s(self) -> float:
        """Seconds the model has owed an answer since the user stopped talking (0 when answered / nobody spoke).
        Background noise that passes the gate but gets no answer also shows up here, so read it together with
        voice/gemini_last_msg_age_s: both climbing = the model is stuck."""
        if not self.awaiting:
            return 0.0
        return min(UNANSWERED_CAP_S, max(0.0, time.monotonic() - self.last_user_chunk))

    def clear_turn(self) -> None:
        self.awaiting = False

    # ── events ──
    def gemini_closed(self, code) -> None:
        self.gemini_ws_closes += 1
        if isinstance(code, int):
            self.gemini_ws_last_close_code = code

    def voice_connect_failed(self, code) -> None:
        self.voice_connect_fails += 1
        if isinstance(code, int):
            self.voice_last_fail_code = code

    # ── window renderer ──
    def render_done(self, ms: float, jpeg_bytes: int) -> None:
        self._render_n += 1
        self._render_ms_sum += ms
        self._render_ms_max = max(self._render_ms_max, ms)
        self._jpeg_bytes_sum += jpeg_bytes

    def drain_render(self) -> Tuple[int, float, float, float]:
        """(frames rendered, avg ms, max ms, avg jpeg KB) since the last call."""
        n = self._render_n
        out = (n, self._render_ms_sum / n if n else 0.0, self._render_ms_max, self._jpeg_bytes_sum / n / 1024 if n else 0.0)
        self._render_n, self._render_ms_sum, self._render_ms_max, self._jpeg_bytes_sum = 0, 0.0, 0.0, 0
        return out


metrics = VoiceMetrics()

_RE_DAVE = re.compile(r"DAVE: dropped (\d+) undecryptable")
_RE_LOST = re.compile(r"(\d+) packets were lost")


class VoiceLogCounter(logging.Handler):
    """Counts the voice-receive problems that otherwise only show up in logs/voice_recv.log."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if "CryptoError" in msg:
                metrics.crypto_errors += 1
                return
            m = _RE_DAVE.search(msg)
            if m:
                metrics.dave_drops += int(m.group(1))
                return
            if "Opus decode error" in msg:
                metrics.opus_errors += 1
                return
            m = _RE_LOST.search(msg)
            if m:
                metrics.packets_lost += int(m.group(1))
        except Exception:
            pass


_handler = None


def install_log_counter() -> None:
    global _handler
    if _handler is not None:
        return
    _handler = VoiceLogCounter(level=logging.WARNING)
    logging.getLogger("discord.ext.voice_recv").addHandler(_handler)


def remove_log_counter() -> None:
    global _handler
    if _handler is not None:
        logging.getLogger("discord.ext.voice_recv").removeHandler(_handler)
        _handler = None
