import asyncio
import copy
import os
import base64
import json
import traceback
from typing import Optional, Dict, Any, List
from websockets.client import WebSocketClientProtocol
from websockets.asyncio.client import connect
from discord import VoiceClient, Message
from arona.voice_engine.src.stream import QueuedStreamingPCMAudio
from arona.voice_engine.src.window import VirtualDiscordWindow, WINDOW_TOOLS, WINDOW_TOOL_NAMES, WINDOW_PROMPT
from attachment import discord_attachment_to_parts
from console import console
import config

class GeminiWebSocket:
    def __init__(self, voice: str = 'aoede', persona: str = "You are a helpful assistant") -> None:
        self.ws: Optional[WebSocketClientProtocol] = None
        self.lock: asyncio.Lock = asyncio.Lock()
        self._connecting: bool = False
        self.persona: str = persona
        self.tools = None
        self.function_handler = None
        self.current_user = None
        self.current_channel = None
        self.current_guild = None
        self.voice_client: Optional[VoiceClient] = None
        self.receive_task: Optional[asyncio.Task] = None
        self.audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._audio_taps: set = set()   # debug web listeners: queues fed with what the bot hears
        self.window: VirtualDiscordWindow = VirtualDiscordWindow(self)
        self.config: Dict[str, Any] = {
            'generation_config': {
                "response_modalities": ["AUDIO"],
                'speech_config': {
                    'voice_config': {
                        'prebuilt_voice_config': {'voice_name': voice}
                 
                    }
                },
                "enable_affective_dialog": True
                #"thinking_level": "LOW"
            }
        }
    
    def subscribe_audio(self) -> "asyncio.Queue[bytes]":
        """Debug tap: receive a copy of every audio chunk sent to Gemini (16 kHz mono PCM16)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._audio_taps.add(q)
        return q

    def unsubscribe_audio(self, q) -> None:
        self._audio_taps.discard(q)

    def _tap_audio(self, audio_data: bytes) -> None:
        for q in list(self._audio_taps):
            try:
                q.put_nowait(audio_data)
            except asyncio.QueueFull:
                pass   # slow listener: drop instead of blocking the voice path

    @property
    def is_voice_session(self) -> bool:
        """Check if the bot is in a voice session."""
        return self.voice_client is not None and self.voice_client.is_connected()
        
    async def close(self) -> None:
        """Closes the WebSocket connection."""
        try:
            await self.window.stop()
        except Exception as e:
            console.log(f"[Gemini Live] Failed to stop virtual window: {e}", "WARN")
        ws, self.ws = self.ws, None
        task, self.receive_task = self.receive_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                console.log(f"[Gemini Live] Receive task cleanup failed: {e}", "WARN")
        if ws:
            try:
                await ws.close()
            except Exception as e:
                console.log(f"[Gemini Live] WebSocket close failed: {e}", "WARN")

    async def connect(self) -> None:
        if self._connecting:  # a connect (ws open + setup) is already in flight
            return
        self._connecting = True
        try:
            await self._connect_inner()
        finally:
            self._connecting = False

    async def wait_ready(self) -> None:
        """Waits until an in-flight connect() (socket + setup + voice reference) has finished."""
        while self._connecting:
            await asyncio.sleep(0.05)

    async def _connect_inner(self) -> None:
        if self.ws and self.ws.protocol.state.name == "OPEN":
            return
        if self.ws:
            stale_ws, self.ws = self.ws, None
            try:
                await stale_ws.close()
            except Exception as e:
                console.log(f"[Gemini Live] Failed to close stale WebSocket: {e}", "WARN")

        api_key_list = os.getenv('GEMINI_API_KEY')
        try:
            api_keys = json.loads(api_key_list) if api_key_list else []
            if isinstance(api_keys, str): api_keys = [api_keys]
        except:
            api_keys = [api_key_list] if api_key_list else []
        
        base_url: str = "wss://generativelanguage.googleapis.com"
        endpoint: str = "/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
        
        if not self.ws:
            for i, api_key in enumerate(api_keys):
                try:
                    uri = f"{base_url}{endpoint}?key={api_key}"
                    self.ws = await connect(
                        uri,
                        additional_headers={"Content-Type": "application/json"},
                        ping_interval=10,
                        ping_timeout=10
                    )
                    console.log(f"Connected to Gemini WebSocket (Key {i+1})", "INFO")
                    await self.setup()
                    self.receive_task = asyncio.create_task(self.listen())
                    await self.send_voice_reference()
                    self.window.force_next()
                    
                    return
                except Exception as e:
                    console.log(f"Failed to connect to Gemini (Key {i+1}): {e}", "WARN")
                    self.ws = None
            console.log("All API keys failed to connect to Gemini WebSocket.", "ERROR")
            
    async def setup(self) -> None:
        tools_config = copy.deepcopy(self.tools) if self.tools else [{'google_search': {}}]
        # Virtual Discord window tools (scroll / zoom / media control)
        _decl = next((t for t in tools_config if isinstance(t, dict) and "function_declarations" in t), None)
        if _decl is not None:
            _decl["function_declarations"] = list(_decl["function_declarations"]) + WINDOW_TOOLS
        else:
            tools_config.append({"function_declarations": WINDOW_TOOLS})
        setup_msg = {
            "setup": {
                "model": f"models/{config.LIVE_MODEL}", #"models/gemini-2.5-flash-native-audio-preview-12-2025", # use 3.1 now
                "generation_config": self.config["generation_config"],
                "system_instruction": {"parts": [{"text": self.persona + WINDOW_PROMPT}]},
                "tools": tools_config,
                # audio+video sessions are capped at ~2 min without compression
                "context_window_compression": {"sliding_window": {}},
                # Allow model to call multiple functions in parallel
                "tool_config": {
                    "function_calling_config": {"mode": "AUTO"}
                },
        
                ## 2. Bật Proactive (Bỏ qua tạp âm, chỉ trả lời khi cần)
                #"proactivity": {
                #    "proactive_audio": True
                #},
        
                # 3. Bật và cấu hình VAD (Tự động nhận diện giọng nói)
                "realtime_input_config": {
                    "automatic_activity_detection": {
                        "disabled": False,
                        "silence_duration_ms": 10, 
                        "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
                        "end_of_speech_sensitivity": "END_SENSITIVITY_LOW"
                    }
                }
            }
        }
        if self.ws:
            await self.ws.send(json.dumps(setup_msg))
            console.log("Sent setup message", "DEBUG")
            setup_response = await self.ws.recv()
            setup_result = json.loads(setup_response)
            console.log(setup_result, "DEBUG")

    async def listen(self) -> None:
        """Continuous listening loop for server messages."""
        ws = self.ws
        if not ws:
            console.log("[Gemini Live] Listener started without a WebSocket.", "ERROR")
            return
        try:
            while True:
                if ws.protocol.state.name != 'OPEN':
                    break
                try:
                    raw_response: bytes = await ws.recv()
                    response: Dict[str, Any] = json.loads(raw_response.decode("utf-8"))
                    await self.handle_message(response)
                except Exception as e:
                    console.log(
                        f"[Gemini Live] Listen loop error "
                        f"(state={ws.protocol.state.name}): {type(e).__name__}: {e}",
                        "ERROR",
                    )
                    break
        except asyncio.CancelledError:
            console.log("Listen loop cancelled", "INFO")
        finally:
            console.log(
                f"[Gemini Live] Receive loop ended "
                f"(state={ws.protocol.state.name}, still_current={self.ws is ws}); "
                "voice connection will remain active.",
                "WARN",
            )
            if self.ws is ws:
                self.ws = None
                try:
                    await ws.close()
                except Exception as e:
                    console.log(f"[Gemini Live] Failed to close ended WebSocket: {e}", "WARN")

    async def handle_message(self, response: Dict[str, Any]) -> None:
        if "error" in response:
            console.log(f"Error in Gemini response: {response['error']}", "ERROR")
            return

        # Tool calls come as a top-level "toolCall" key, NOT inside serverContent
        if "toolCall" in response:
            await self.handle_tool_use(response["toolCall"])

        if "serverContent" in response:
            server_content = response["serverContent"]

            # Interrupt: model was cut off mid-speech — clear queued audio and stop playback.
            # This lets the model stay silent, but new audio can queue normally afterwards.
            if server_content.get("interrupted"):
                console.log("[Gemini Live] Interrupted — clearing audio queue", "DEBUG")
                while not self.audio_queue.empty():
                    try:
                        self.audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if self.voice_client and self.voice_client.is_playing():
                    # VoiceRecvClient.stop() also calls stop_listening(), which would deaf the bot after the first
                    # interruption. Only stop playback.
                    getattr(self.voice_client, "stop_playing", self.voice_client.stop)()

            if "modelTurn" in server_content:
                parts = server_content["modelTurn"].get("parts", [])
                for part in parts:
                    if "inlineData" in part:
                        b64data = part["inlineData"]["data"]
                        if b64data:
                            audio_bytes = base64.b64decode(b64data)
                            await self.play_audio(audio_bytes)

            # Flush remaining audio in voice changer buffer when turn ends
            if server_content.get("turnComplete"):
                if hasattr(self, "_voice_bridge") and self._voice_bridge and self._voice_bridge.enabled:
                    asyncio.create_task(self._voice_bridge.flush(self))

    async def play_audio(self, audio_data: bytes) -> None:
        if not self.voice_client or not self.voice_client.is_connected():
            return
        
        await self.audio_queue.put(audio_data)
        
        if not self.voice_client.is_playing():
             source = QueuedStreamingPCMAudio(self.audio_queue)
             self.voice_client.play(source, after=lambda e: console.log(f"Playback finished: {e}", "DEBUG") if e else None)
        
    async def send_audio_chunk(self, audio_data: bytes) -> None:
        if not self.ws or not self.ws.protocol.state.name == 'OPEN':
            console.log("[Gemini] Connection lost/closed. Reconnecting for audio...", "WARN")
            await self.connect()
            
        if not self.ws:
            return

        console.log(f"Sending {len(audio_data)} bytes to Gemini", "DEBUG") 
        self._tap_audio(audio_data)
        msg = {
            "realtime_input": {
                "audio": {
                    "mime_type": "audio/pcm;rate=16000",
                    "data": base64.b64encode(audio_data).decode("utf-8")
                }
            }
        }
        try:
            await self.ws.send(json.dumps(msg))
        except Exception as e:
            console.log(f"[Gemini] Send audio error: {e}", "ERROR")

    @staticmethod
    def _load_ref_audio_pcm16(path: str) -> tuple[bytes, int]:
        """Reads a WAV file and returns (raw mono 16-bit PCM bytes, sample_rate).
        Only converts bit-depth/channel count if needed — the Live API resamples
        on its own as long as the real sample rate is declared in the mime type
        (e.g. "audio/pcm;rate=<actual_rate>"), so no manual resampling here."""
        import wave
        with wave.open(path, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        try:
            import audioop
            if sampwidth != 2:
                frames = audioop.lin2lin(frames, sampwidth, 2)
                sampwidth = 2
            if n_channels != 1:
                frames = audioop.tomono(frames, sampwidth, 0.5, 0.5)
        except ImportError:
            console.log("[Gemini Live] audioop unavailable, sending reference audio as-is (mono/16-bit not verified)", "WARN")

        # The 2.5 native-audio model rejects a sample-rate change after the first audio input
        # (1007 "Sample rate changed from previously 32000 to 16000"), and the mic stream is 16 kHz,
        # so the reference clip has to be 16 kHz as well.
        if framerate != 16000:
            try:
                import audioop
                frames, _ = audioop.ratecv(frames, 2, 1, framerate, 16000, None)
                framerate = 16000
            except ImportError:
                console.log("[Gemini Live] audioop unavailable, cannot resample reference audio to 16 kHz", "WARN")

        return frames, framerate

    async def send_voice_reference(self) -> None:
        """Primes the session with a short reference clip so Gemini mimics its
        tone/rhythm for the rest of the voice call.

        NOTE: the Live API only accepts raw 16-bit PCM audio (mono, little-endian)
        — no WAV/MP3/etc. containers, and not as inline_data in client_content
        turns either (that closes the socket with 1008 "Operation is not
        implemented"). Audio must go through realtime_input, so it's bracketed
        with explicit BEGIN/END REF AUDIO marker turns via client_content: an
        opening marker (turn left open, so it doesn't trigger a response), then
        the clip itself, then a closing marker with the transcript that
        explicitly tells the model this was calibration data, not a request
        from Sensei — otherwise the model's ASR/VAD will transcribe and act on
        the clip's spoken content like real user speech (confirmed: it fired a
        schaledb_query for "Shiroko" straight from the ref clip's transcript).
        The real sample rate is declared in the mime type so the API resamples
        it server-side — no local resampling needed.
        """
        if not self.ws:
            return

        try:
            pcm_data, sample_rate = self._load_ref_audio_pcm16(config.VOICE_CALL_REF_AUD)
        except Exception as e:
            console.log(f"[Gemini Live] Failed to load voice reference audio: {e}", "ERROR")
            return

        begin_marker = (
            "=== BEGIN REF AUDIO ===\n"
            "これから送る音声はトーン調整用のリファレンスです。システムのデータで先生の発言ではないので、応答しないでください。"
        )
        end_marker = (
            "=== END REF AUDIO ===\n"
            f"トランスクリプト（参考）: 「{config.VOICE_CALL_REF_TEXT}」\n\n"
            "転写・翻訳・応答・実行はせず、トーンと話すリズムだけを静かに取り入れてください。"
            "今すぐ、クリップには触れず、短くキャラクターらしい挨拶だけを返し、先生の最初のメッセージを待ってください。"
        )

        try:
            await self.ws.send(json.dumps({
                "client_content": {
                    "turns": [{"role": "user", "parts": [{"text": begin_marker}]}],
                    "turn_complete": False
                }
            }))
        except Exception as e:
            console.log(f"[Gemini Live] Failed to send BEGIN REF AUDIO marker: {e}", "ERROR")
            return

        mime_type = f"audio/pcm;rate={sample_rate}"
        try:
            await self.ws.send(json.dumps({
                "realtime_input": {
                    "audio": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(pcm_data).decode("utf-8")
                    }
                }
            }))
        except Exception as e:
            console.log(f"[Gemini Live] Failed to send voice reference audio: {e}", "ERROR")
            return

        try:
            await self.ws.send(json.dumps({
                "client_content": {
                    "turns": [{"role": "user", "parts": [{"text": end_marker}]}],
                    "turn_complete": True
                }
            }))
            console.log(f"[Gemini Live] Sent bracketed voice reference audio ({mime_type})", "INFO")
        except Exception as e:
            console.log(f"[Gemini Live] Failed to send END REF AUDIO marker: {e}", "ERROR")

    async def send_video_frame(self, jpeg: bytes) -> None:
        """Streams one JPEG frame (the virtual Discord window) through realtime video input."""
        if not self.ws or not self.ws.protocol.state.name == 'OPEN':
            return
        try:
            await self.ws.send(json.dumps({
                "realtime_input": {"video": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode("utf-8")}}
            }))
        except Exception as e:
            console.log(f"[Gemini] Send video frame error: {e}", "ERROR")

    async def send_realtime_text(self, text: str) -> None:
        """Sends text mid-session through realtime input (the model reacts to it like live input)."""
        if not self.ws or not self.ws.protocol.state.name == 'OPEN':
            return
        try:
            if "3.1" in config.LIVE_MODEL:
                # 3.1 live: client_content is only for seeding history, mid-session text goes through realtime input
                msg = {"realtime_input": {"text": text}}
            else:
                # 2.5 native audio: regular user turn
                msg = {"client_content": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turn_complete": True}}
            await self.ws.send(json.dumps(msg))
        except Exception as e:
            console.log(f"[Gemini] Send realtime text error: {e}", "ERROR")

    async def send_message(self, text: str) -> None:
        """Sends a text message (context or system info) without blocking for audio response immediately."""
        if not self.ws: return
        msg = {
            "client_content": {
                "turns": [{"role": "user", "parts": [{"text": text}]}],
                "turn_complete": True
            }
        }
        await self.ws.send(json.dumps(msg))

    async def send_multimodal_message(self, text: str, images: List[Dict[str, Any]] = None) -> None:
        """Sends a multimodal message (text + images) to the model."""
        if not self.ws or not self.ws.protocol.state.name == 'OPEN':
            console.log("Live agent websocket closed, restarting...", "WARN")
            await self.connect()

        if not self.ws: return
        parts = [{"text": text}]
        if images:
            for img in images:
                parts.append({
                    "inline_data": {
                        "mime_type": img.get("mime_type"),
                        "data": img.get("data")
                    }
                })
        msg = {
            "client_content": {
                "turns": [{"role": "user", "parts": parts}],
                "turn_complete": True
            }
        }
        await self.ws.send(json.dumps(msg))

    async def handle_tool_use(self, tool_use: Dict[str, Any]) -> None:
        func_calls = tool_use.get("functionCalls", [])
        function_responses = []

        for call in func_calls:
            name = call["name"]
            args = call["args"]
            call_id = call["id"]
            console.log(f"[Gemini Live] Tool Call: {name} {args}", "INFO")
            
            result = "Function execution failed or handler not set."
            if name in WINDOW_TOOL_NAMES:
                result = await self.window.handle_tool(name, args)
            elif self.function_handler:
                # Mock message object
                class MockMsg: pass
                mock_msg = MockMsg()
                mock_msg.author = self.current_user
                mock_msg.channel = self.current_channel
                mock_msg.guild = self.current_guild
                
                try:
                    result = await self.function_handler(name, args, mock_msg)
                except Exception as e:
                    console.log(f"Tool execution error: {e}", "ERROR")
                    result = f"Error: {e}"
            
            function_responses.append({
                "name": name,
                "id": call_id,
                "response": {"result": result}
            })

        if function_responses:
            msg = {
                "tool_response": {
                    "function_responses": function_responses
                }
            }
            if self.ws:
                await self.ws.send(json.dumps(msg))