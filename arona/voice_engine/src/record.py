import asyncio
import traceback
import discord
import audioop
import time
from typing import Optional, Dict, Set, Any
from arona.voice_engine.src.gemini import GeminiWebSocket 
from discord.ext import voice_recv, commands
from console import console

# --- diagnostics: find out who stops the reader (the sink gets cleaned up a few seconds after joining) ---
try:
    from discord.ext.voice_recv.reader import AudioReader as _AudioReader
    if not getattr(_AudioReader, "_arona_traced", False):
        _orig_reader_stop = _AudioReader.stop

        def _traced_reader_stop(self):
            console.log(
                f"[VoiceRecv] AudioReader.stop() active={self.active} error={self.error!r} called from:\n"
                + "".join(traceback.format_stack(limit=12)),
                "WARN",
            )
            return _orig_reader_stop(self)

        _AudioReader.stop = _traced_reader_stop
        _AudioReader._arona_traced = True
except Exception as _e:  # pragma: no cover
    console.log(f"[VoiceRecv] could not install reader trace: {_e}", "WARN")


class AudioProcessor(voice_recv.AudioSink):
    def __init__(self, 
                 user: discord.User, 
                 channel: discord.TextChannel, 
                 bot: commands.Bot, 
                 gemini_ws: GeminiWebSocket,
                 vc: Optional[discord.VoiceClient] = None) -> None:
        super().__init__()
        self.buffer: bytes = b""
        self.target_user: discord.User = user
        self.channel: discord.TextChannel = channel
        self.bot: commands.Bot = bot
        self.gemini_ws: GeminiWebSocket = gemini_ws
        self.vc: Optional[discord.VoiceClient] = vc
        self.resample_states: Dict[int, Any] = {}
        self.known_ssrcs = set()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, audio_data):
        """Stream ALL audio from ALL users directly to Gemini."""
        # Log SSRCs
        if hasattr(audio_data, 'ssrc') and audio_data.ssrc not in self.known_ssrcs:
            self.known_ssrcs.add(audio_data.ssrc)
            console.log(f"Registered SSRC: {audio_data.ssrc} from {user}", "DEBUG")
        
        # ← SIMPLE: Chỉ cần check có audio + user là stream luôn
        if not audio_data.pcm or not user:
            return
        
        # Bỏ qua audio từ bot
        if user.id == self.bot.user.id:
            return
            
        try:
            user_id = user.id if hasattr(user, 'id') else 0
            state = self.resample_states.get(user_id)

            # 1. Convert Stereo to Mono
            mono_data = audioop.tomono(audio_data.pcm, 2, 1, 1)
            
            # 1b. Track who is speaking (shown in the virtual Discord window)
            if audioop.rms(mono_data, 2) > 250:
                self.gemini_ws.window.last_audio[user_id] = time.monotonic()

            # 2. Resample 48000 -> 16000
            resampled_data, state = audioop.ratecv(mono_data, 2, 1, 48000, 16000, state)
            self.resample_states[user_id] = state
            
            # 3. Stream to Gemini - NO CONDITIONS
            asyncio.run_coroutine_threadsafe(
                self.gemini_ws.send_audio_chunk(resampled_data), 
                self.bot.loop
            )
        except Exception as e:
            console.log(f"Error processing audio from {user}: {e}", "ERROR")

    # ← BỎ HẾT: Không cần speaking events
    # def on_voice_member_speaking_start(self, member: discord.Member) -> None:
    #     pass
    
    # def on_voice_member_speaking_stop(self, member: discord.Member) -> None:
    #     pass
    
    def cleanup(self) -> None:
        console.log("AudioSink cleanup complete.", "INFO")