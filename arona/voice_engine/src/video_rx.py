"""EXPERIMENTAL camera receive for the virtual Discord window (config.VIDEO_RX = True to enable).

voice gateway VIDEO op (ssrc -> user) -> RTP (transport-decrypted by voice_recv) -> frame assembly (VP8 / H264)
-> DAVE decrypt (davey, MediaType.video) -> PyAV decode -> window.set_video_frame(uid, 'camera', img)
"""
import os
import queue
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from console import console

try:
    import davey
except Exception:
    davey = None

import config

ENABLED = bool(getattr(config, "VIDEO_RX", False))

# payload types WE declare in SELECT_PROTOCOL (the server uses them for what it forwards to us)
PT_AUDIO = 120
PT_H264, PT_H264_RTX = 101, 102
PT_VP8, PT_VP8_RTX = 103, 104
CODEC_BY_PT = {PT_H264: "h264", PT_VP8: "vp8"}
RTX_PTS = {PT_H264_RTX, PT_VP8_RTX}
CODECS = [
    {"name": "opus", "type": "audio", "priority": 1000, "payload_type": PT_AUDIO},
    {"name": "H264", "type": "video", "priority": 1000, "payload_type": PT_H264, "rtx_payload_type": PT_H264_RTX, "encode": False, "decode": True},
    {"name": "VP8", "type": "video", "priority": 2000, "payload_type": PT_VP8, "rtx_payload_type": PT_VP8_RTX, "encode": False, "decode": True},
]
START3, START4 = b"\x00\x00\x01", b"\x00\x00\x00\x01"


class _Assembler:
    """RTP payloads of one SSRC -> whole encoded frames. Any lost packet damages the frame and everything until the next keyframe."""

    def __init__(self, codec: str) -> None:
        self.codec = codec
        self.ts: Optional[int] = None
        self.parts: List[bytes] = []
        self.key = False
        self.ok = True
        self.last_seq: Optional[int] = None
        self.need_key = True            # True until a clean keyframe has been completed

    def push(self, seq: int, ts: int, marker: bool, data: bytes) -> Optional[Tuple[bytes, bool]]:
        if self.last_seq is not None and ((seq - self.last_seq) & 0xFFFF) != 1:
            self.ok = False
            self.need_key = True
        self.last_seq = seq
        if self.ts is not None and ts != self.ts:
            if self.parts:                               # previous frame never got its marker packet -> it is lost
                self.need_key = True
            self.parts, self.key, self.ok = [], False, True
        self.ts = ts
        try:
            chunk, key = self._vp8(data) if self.codec == "vp8" else self._h264(data)
        except Exception:
            self.ok = False
            self.need_key = True
            return None
        if chunk:
            self.parts.append(chunk)
        self.key = self.key or key
        if not marker:
            return None
        frame, key, ok = b"".join(self.parts), self.key, self.ok
        self.parts, self.key, self.ok, self.ts = [], False, True, None
        if not ok or not frame:
            return None
        if key:
            self.need_key = False
        return (frame, key) if not self.need_key else None

    def _vp8(self, p: bytes) -> Tuple[bytes, bool]:
        i = 1
        start = bool(p[0] & 0x10) and (p[0] & 0x0F) == 0
        if p[0] & 0x80:                                  # X: extended control bits
            x = p[1]
            i = 2
            if x & 0x80:                                 # I: picture id (7 or 15 bit)
                i += 2 if p[i] & 0x80 else 1
            if x & 0x40:                                 # L: TL0PICIDX
                i += 1
            if x & 0x30:                                 # T / K
                i += 1
        body = p[i:]
        return body, bool(start and body and (body[0] & 0x01) == 0)

    def _h264(self, p: bytes) -> Tuple[bytes, bool]:
        t = p[0] & 0x1F
        if t == 24:                                      # STAP-A
            out, i, key = [], 1, False
            while i + 2 <= len(p):
                n = struct.unpack_from(">H", p, i)[0]
                nal = p[i + 2:i + 2 + n]
                out.append(START4 + nal)
                key = key or (nal[0] & 0x1F) in (5, 7)
                i += 2 + n
            return b"".join(out), key
        if t == 28:                                      # FU-A
            hdr = p[1]
            if hdr & 0x80:
                nal_hdr = bytes([(p[0] & 0xE0) | (hdr & 0x1F)])
                return START4 + nal_hdr + p[2:], (hdr & 0x1F) in (5, 7)
            return p[2:], False
        return START4 + p, t in (5, 7)                   # single NAL unit


class _Decoder(threading.Thread):
    """Decodes frames of one stream off the network thread; emits at most ~2 pictures per second."""

    def __init__(self, codec: str, on_image: Callable[[Any], None], on_fail: Callable[[], None]) -> None:
        super().__init__(daemon=True)
        self.codec, self.on_image, self.on_fail = codec, on_image, on_fail
        self.q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=12)
        self._last = 0.0
        self.start()

    def put(self, frame: Optional[bytes]) -> None:
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put_nowait(frame)

    def run(self) -> None:
        import av
        ctx = av.CodecContext.create(self.codec, "r")
        while True:
            frame = self.q.get()
            if frame is None:
                return
            try:
                for pkt in ctx.parse(frame):
                    for fr in ctx.decode(pkt):
                        now = time.monotonic()
                        if now - self._last >= 0.4:
                            self._last = now
                            img = fr.to_image()
                            if img.width > 960:
                                img = img.resize((960, max(1, img.height * 960 // img.width)))
                            self.on_image(img)
            except Exception as e:
                console.log(f"[VideoRx] decode error ({self.codec}): {e}", "WARN")
                self.on_fail()


class VideoReceiver:
    def __init__(self, window, get_vc: Callable[[], Any]) -> None:
        self.win, self.get_vc = window, get_vc
        self.ssrc_info: Dict[int, Tuple[int, str]] = {}      # video ssrc -> (user id, 'camera')
        self.asm: Dict[int, _Assembler] = {}
        self.dec: Dict[int, _Decoder] = {}
        self.stats: Dict[Tuple[int, int], int] = {}          # (ssrc, payload type) -> packets
        self.counters = {"frames": 0, "dave_fail": 0, "pli": 0}
        self._pli_t: Dict[int, float] = {}
        self._pli_n = 0
        self._log_t = time.monotonic()

    # ── voice gateway VIDEO op (event voice_member_video) ──
    def on_video_state(self, member, streams) -> None:
        if member is None:
            return
        uid = member.id
        for ssrc, (u, _k) in list(self.ssrc_info.items()):
            if u == uid:
                self._drop(ssrc)
        active = 0
        seen = set()
        for s in streams.streams:
            if getattr(s, "type", "video") == "screen":     # Go Live is never sent to bots
                continue
            if s.active and s.ssrc:
                self.ssrc_info[s.ssrc] = (uid, "camera")
                seen.add("camera")
                active += 1
        if streams.video_ssrc and active == 0 and any(getattr(s, "active", True) for s in streams.streams):
            self.ssrc_info[streams.video_ssrc] = (uid, "camera")
            seen.add("camera")
        if "camera" not in seen:
            self.win.clear_video(uid, "camera")
        console.log(f"[VideoRx] {member} video state: {streams!r}", "INFO")

    def _drop(self, ssrc: int) -> None:
        self.ssrc_info.pop(ssrc, None)
        self.asm.pop(ssrc, None)
        d = self.dec.pop(ssrc, None)
        if d:
            d.put(None)

    def reset(self) -> None:
        for ssrc in list(self.ssrc_info):
            self._drop(ssrc)
        self.stats.clear()

    def forget_user(self, uid: int) -> None:
        for ssrc, (u, _k) in list(self.ssrc_info.items()):
            if u == uid:
                self._drop(ssrc)
        self.win.clear_video(uid)

    # ── RTP (network thread) ──
    def handle_packet(self, reader, pkt) -> None:
        ssrc, pt = pkt.ssrc, pkt.payload
        self.stats[(ssrc, pt)] = self.stats.get((ssrc, pt), 0) + 1
        self._maybe_log()
        info = self.ssrc_info.get(ssrc)
        codec = CODEC_BY_PT.get(pt)
        if info is None or codec is None or not pkt.decrypted_data:
            return
        asm = self.asm.get(ssrc)
        if asm is None or asm.codec != codec:
            asm = self.asm[ssrc] = _Assembler(codec)
        done = asm.push(pkt.sequence, pkt.timestamp, pkt.marker, bytes(pkt.decrypted_data))
        if asm.need_key:
            self._request_keyframe(reader, ssrc)
        if done is None:
            return
        frame = self._dave(info[0], done[0], codec)
        if frame is None:
            asm.need_key = True
            return
        dec = self.dec.get(ssrc)
        if dec is None:
            uid, kind = info
            dec = self.dec[ssrc] = _Decoder(codec, lambda img, u=uid, k=kind: self.win.set_video_frame(u, k, img),
                                            lambda a=asm: setattr(a, "need_key", True))
        self.counters["frames"] += 1
        dec.put(frame)

    def _dave(self, uid: int, frame: bytes, codec: str) -> Optional[bytes]:
        vc = self.get_vc()
        state = getattr(vc, "_connection", None)
        if not getattr(state, "dave_protocol_version", 0):
            return frame
        session = getattr(state, "dave_session", None)
        if davey is None or session is None or not session.ready:
            self.counters["dave_fail"] += 1
            return None
        variants = [frame]
        if codec == "h264":                              # start-code length is not on the wire: also try 3-byte codes
            variants.append(frame.replace(START4, START3))
        for v in variants:
            try:
                return session.decrypt(int(uid), davey.MediaType.video, v)
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
        self.counters["dave_fail"] += 1
        return None

    # ── RTCP PLI: ask the sender for a keyframe ──
    def _request_keyframe(self, reader, ssrc: int) -> None:
        now = time.monotonic()
        if now - self._pli_t.get(ssrc, 0.0) < 1.0:
            return
        self._pli_t[ssrc] = now
        try:
            vc = self.get_vc()
            dec = reader.decryptor
            if dec.mode != "aead_xchacha20_poly1305_rtpsize":
                return
            my_ssrc = vc._connection.ssrc
            header = struct.pack(">BBHI", 0x81, 206, 2, my_ssrc)
            self._pli_n = (self._pli_n + 1) & 0xFFFFFFFF
            nonce_suffix = struct.pack(">I", self._pli_n)
            nonce = nonce_suffix + bytes(20)
            enc = dec.box.encrypt(struct.pack(">I", ssrc), header, nonce).ciphertext
            vc._connection.socket.sendall(header + enc + nonce_suffix)
            self.counters["pli"] += 1
        except Exception as e:
            console.log(f"[VideoRx] PLI failed: {e}", "WARN")

    def _maybe_log(self) -> None:
        now = time.monotonic()
        if now - self._log_t < 10:
            return
        self._log_t = now
        vid = {k: v for k, v in self.stats.items() if k[1] != PT_AUDIO}
        console.log(f"[VideoRx] packets (ssrc,pt)->n: {vid} | {self.counters}", "INFO")


_installed: Optional[VideoReceiver] = None


def install(window, get_vc: Callable[[], Any]) -> Optional[VideoReceiver]:
    """Patches discord.py's voice gateway (declare video support) and voice_recv's reader (divert video RTP). Idempotent."""
    global _installed
    if _installed is not None:
        return _installed
    from discord.gateway import DiscordVoiceWebSocket as WS
    from discord.ext.voice_recv import reader as _reader, rtp

    rx = _installed = VideoReceiver(window, get_vc)

    orig_send = WS.send_as_json

    async def send_as_json(self, data):
        op = data.get("op") if isinstance(data, dict) else None
        if op == self.IDENTIFY:
            data["d"]["video"] = True
            data["d"]["streams"] = [{"type": "video", "rid": "100", "quality": 100}]
        elif op == self.SELECT_PROTOCOL:
            data["d"]["codecs"] = CODECS
        await orig_send(self, data)
        if op == self.SELECT_PROTOCOL:                   # like the official client: announce our (receive-only) video state
            try:
                await orig_send(self, {"op": 12, "d": {"audio_ssrc": self._connection.ssrc, "video_ssrc": 0, "rtx_ssrc": 0, "streams": [
                    {"type": "video", "rid": "100", "quality": 100, "active": False}]}})
            except Exception as e:
                console.log(f"[VideoRx] video-state announce failed: {e}", "WARN")

    WS.send_as_json = send_as_json

    orig_cb = _reader.AudioReader.callback

    def callback(self, packet_data):
        try:
            if len(packet_data) > 12 and not rtp.is_rtcp(packet_data):
                ssrc = struct.unpack_from(">I", packet_data, 8)[0]
                pt = packet_data[1] & 0x7F
                if ssrc in rx.ssrc_info or pt in CODEC_BY_PT or pt in RTX_PTS:
                    if pt in RTX_PTS:
                        return
                    pkt = rtp.decode_rtp(packet_data)
                    pkt.decrypted_data = self.decryptor.decrypt_rtp(pkt)
                    rx.handle_packet(self, pkt)
                    return
        except Exception as e:
            console.log(f"[VideoRx] packet error: {e}", "WARN")
            return
        return orig_cb(self, packet_data)

    _reader.AudioReader.callback = callback
    console.log("[VideoRx] experimental video receive installed", "INFO")
    return rx
