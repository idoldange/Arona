import asyncio
import os
import base64
import json
import traceback
from typing import Optional, Dict, Any, List
from websockets.client import WebSocketClientProtocol
from websockets.asyncio.client import connect
from discord import VoiceClient, Message
from arona.voice_engine.src.stream import QueuedStreamingPCMAudio
from attachment import discord_attachment_to_parts
from console import console
import config

class GeminiWebSocket:
    def __init__(self, voice: str = 'aoede', persona: str = "You are a helpful assistant") -> None:
        self.ws: Optional[WebSocketClientProtocol] = None
        self.lock: asyncio.Lock = asyncio.Lock()
        self.persona: str = persona
        self.tools = None
        self.function_handler = None
        self.current_user = None
        self.current_channel = None
        self.current_guild = None
        self.voice_client: Optional[VoiceClient] = None
        self.receive_task: Optional[asyncio.Task] = None
        self.audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self.config: Dict[str, Any] = {
            'generation_config': {
                "response_modalities": ["AUDIO"],
                'speech_config': {
                    'voice_config': {
                        'prebuilt_voice_config': {'voice_name': voice}
                 
                    }
                },
                #"enable_affective_dialog": True
                #"thinking_level": "LOW"
            }
        }
    
    @property
    def is_voice_session(self) -> bool:
        """Check if the bot is in a voice session."""
        return self.voice_client is not None and self.voice_client.is_connected()
        
    async def close(self) -> None:
        """Closes the WebSocket connection."""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        if self.receive_task:
            self.receive_task.cancel()
            self.receive_task = None

    async def connect(self) -> None:
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
                    
                    return
                except Exception as e:
                    console.log(f"Failed to connect to Gemini (Key {i+1}): {e}", "WARN")
                    self.ws = None
            console.log("All API keys failed to connect to Gemini WebSocket.", "ERROR")
            
    async def setup(self) -> None:
        tools_config = self.tools if self.tools else [{'google_search': {}}]
        setup_msg = {
            "setup": {
                "model": f"models/{config.LIVE_MODEL}", #"models/gemini-2.5-flash-native-audio-preview-12-2025", # use 3.1 now
                "generation_config": self.config["generation_config"],
                "system_instruction": {"parts": [{"text": self.persona}]},
                "tools": tools_config,
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
                        "silence_duration_ms": 200, 
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
        try:
            while True:
                if not self.ws or not self.ws.protocol.state.name == 'OPEN':
                    break
                try:
                    raw_response: bytes = await self.ws.recv()
                    response: Dict[str, Any] = json.loads(raw_response.decode("utf-8"))
                    await self.handle_message(response)
                except Exception as e:
                    console.log(f"[Gemini Live] Listen loop error: {e}", "ERROR")
                    break
        except asyncio.CancelledError:
            console.log("Listen loop cancelled", "INFO")
        finally:
            console.log("[Gemini Live] Connection ended. Cleaning up...", "WARN")
            
            if self.voice_client and self.voice_client.is_connected():
                console.log("Gemini disconnected, leaving Voice Channel.", "INFO")
                vc = self.voice_client
                ch = self.current_channel
                async def _do_leave():
                    if ch:
                        try: await ch.send("Goodbye!")
                        except: pass
                    try: await vc.disconnect(force=True)
                    except: pass
                asyncio.create_task(_do_leave())
            
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None

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
                    self.voice_client.stop()

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
        msg = {
            "realtime_input": {
                "media_chunks": [{
                    "mime_type": "audio/pcm",
                    "data": base64.b64encode(audio_data).decode("utf-8")
                }]
            }
        }
        try:
            await self.ws.send(json.dumps(msg))
        except Exception as e:
            console.log(f"[Gemini] Send audio error: {e}", "ERROR")

    async def send_voice_reference(self) -> None:
        """Sends the reference audio clip as the first user turn so Gemini mimics
        its tone, pitch and rhythm for the rest of the voice session."""
        if not self.ws:
            return

        try:
            with open(config.VOICE_CALL_REF_AUD, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            console.log(f"[Gemini Live] Failed to load voice reference audio: {e}", "ERROR")
            return

        prompt_text = (
            "Listen to the attached audio clip closely and mimic its exact tone, pitch, "
            "and speaking rhythm/cadence for the rest of this conversation. "
            f"Here is the transcript of the clip: \"{config.VOICE_CALL_REF_TEXT}\". "
            "From now on, keep speaking with that same voice vibe consistently, "
            "no matter what topic comes up."
        )

        msg = {
            "client_content": {
                "turns": [{
                    "role": "user",
                    "parts": [
                        {"text": prompt_text},
                        {
                            "inline_data": {
                                "mime_type": "audio/wav",
                                "data": audio_b64
                            }
                        }
                    ]
                }],
                "turn_complete": True
            }
        }

        try:
            await self.ws.send(json.dumps(msg))
            console.log("[Gemini Live] Sent voice reference audio + mimic prompt", "INFO")
        except Exception as e:
            console.log(f"[Gemini Live] Failed to send voice reference: {e}", "ERROR")

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
            if self.function_handler:
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