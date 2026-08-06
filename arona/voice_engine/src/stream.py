import asyncio
from queue import Queue, Empty
import discord
from typing import Optional
from console import console

class QueuedStreamingPCMAudio(discord.AudioSource):
    def __init__(self, async_queue: asyncio.Queue[Optional[bytes]]) -> None:
        self.async_queue = async_queue
        self.sync_queue: Queue[Optional[bytes]] = Queue()
        self.buffer: bytearray = bytearray()
        self.pos: int = 0
        self._end_flag: bool = False
        self.interrupted: bool = False
        self.input_frame_size: int = 960  # 24kHz mono
        self.output_frame_size: int = 3840  # 48kHz stereo
        self.silence: bytes = b'\x00' * self.output_frame_size
        self.buffer_task: Optional[asyncio.Task[None]] = None
        # Grace period: consecutive empty reads before stopping player (50 * 20ms = 1s)
        self._empty_streak: int = 0
        self._max_empty_streak: int = 50
        self._start_buffer_task()

    def _start_buffer_task(self) -> None:
        async def buffer_filler() -> None:
            try:
                while not self._end_flag:
                    try:
                        chunk = await self.async_queue.get()
                        if chunk is None:
                            self._end_flag = True
                            break
                        self.sync_queue.put(chunk)
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        console.log(f"Buffer fill error: {e}", "ERROR")
                        break
            finally:
                self.sync_queue.put(None)
                
        self.buffer_task = asyncio.create_task(buffer_filler())

    def read(self) -> bytes:
        """Read audio frame - return silence if no data available."""
        try:
            # Fill buffer if needed
            while len(self.buffer) - self.pos < self.input_frame_size:
                try:
                    chunk = self.sync_queue.get_nowait()
                    if chunk is None:
                        # End of stream
                        if len(self.buffer) - self.pos <= 0:
                            return b''
                        break
                    self._empty_streak = 0
                    self.buffer.extend(chunk)
                except Empty:
                    # Grace period before stopping player — avoids cutting off mid-sentence
                    # due to momentary network jitter between Gemini audio chunks.
                    if self._end_flag:
                        return b''
                    self._empty_streak += 1
                    if self._empty_streak >= self._max_empty_streak:
                        return b''
                    return self.silence

            view = memoryview(self.buffer)
            chunk = view[self.pos:self.pos + self.input_frame_size]
            self.pos += self.input_frame_size

            # Buffer cleanup
            if self.pos > 48000:
                self.buffer = self.buffer[self.pos:]
                self.pos = 0

            # Upsample 24kHz mono -> 48kHz stereo
            result = bytearray(self.output_frame_size)
            
            for i in range(0, len(chunk), 2):
                sample = chunk[i:i+2]
                pos = i * 4
                # Duplicate sample 4 times (2x frequency, 2x channels)
                result[pos:pos+2] = sample
                result[pos+2:pos+4] = sample
                result[pos+4:pos+6] = sample
                result[pos+6:pos+8] = sample

            return bytes(result)

        except Exception as e:
            console.log(f"Read error: {e}", "ERROR")
            return self.silence

    def cleanup(self) -> None:
        console.log("Cleaning up audio source...", "DEBUG")
        self._end_flag = True
        self.interrupted = True
        if self.buffer_task and not self.buffer_task.done():
            self.buffer_task.cancel()
        self.buffer.clear()
        self.pos = 0