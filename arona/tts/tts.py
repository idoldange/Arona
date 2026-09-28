from io import BytesIO
import aiohttp
import asyncio
import base64
from console import console
import requests
import asyncio
from pydub import AudioSegment, silence
import json
import os
import hashlib
from config import *
import time
import base64
from utils.http_session import session_manager

async def _get_shared_session():
    return await session_manager.get_session()


def _init_():
  try:
    resp = requests.get(f"{API_URL}/set_gpt_weights", params={"weights_path": GPT_MODEL_PATH})
    vresp = requests.get(f"{API_URL}/set_sovits_weights", params={"weights_path": SOVITS_MODEL_PATH})
    if resp.status_code == 200 and vresp.status_code == 200:
        console.log("TTS models loaded successfully.", "INFO")
    else:
        console.log(f"Failed to load TTS models.", "ERROR")
  except Exception as e:
        console.log(f"Error occurred while initializing TTS models: {e}", "ERROR")
_init_()

gpu_lock = asyncio.Semaphore(4) # Actually TTS use CPU(in my case)
async def text_to_speech(text: str, lang: str = "ja") -> str:
    
    async with gpu_lock:
        preset = TTS_REF
        
        params = {
            "text": text,
            "text_lang": lang.lower(),
            "ref_audio_path": preset["ref_path"],
            "prompt_text": preset["prompt_text"],
            "prompt_lang": preset["prompt_lang"].lower(),
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 0.85,  
            "speed_factor": 1.0,
            "parallel_infer": "true"
        }
    
        session = await _get_shared_session()
        try:
            async with session.get(API_URL+"/tts", params=params, timeout=60) as response:
                if response.status == 200:
                    audio_content = await response.read()
                    console.log(f"TTS generated (Size: {len(audio_content)})")
                    return audio_content
                else:
                    error_text = await response.text()
                    console.log(f"TTS API ERROR:: {response.status} - {error_text}", "ERROR")
                    return ""
                    
        except Exception as e:
            console.log(f"TTS connection error: {e}", "ERROR")
            return ""


SYNTH_TIMEOUT_S = 1800  # /synth can be slow (first time each syllable is recorded); >= 10 min required
synth_lock = asyncio.Lock()


async def synth_song(body: bytes, content_type: str, params: dict | None = None, timeout_s: int = SYNTH_TIMEOUT_S):
    """POST a UST (raw) or JSON body to the GPT-SoVITS /synth endpoint.
    Returns (wav_bytes, transpose_str, None) on success or (None, None, error_message)."""
    timeout = aiohttp.ClientTimeout(total=timeout_s, sock_read=timeout_s)
    try:
        session = await _get_shared_session()
        async with session.post(API_URL + "/synth", data=body, params=params or {},
                                headers={"Content-Type": content_type}, timeout=timeout) as response:
            if response.status == 200:
                audio = await response.read()
                console.log(f"Synth generated (Size: {len(audio)})")
                return audio, response.headers.get("X-Synth-Transpose"), None
            err = (await response.text())[:500]
            console.log(f"Synth API ERROR:: {response.status} - {err}", "ERROR")
            return None, None, f"{response.status}: {err}"
    except asyncio.TimeoutError:
        return None, None, f"timed out after {timeout_s // 60} minutes"
    except Exception as e:
        console.log(f"Synth connection error: {e}", "ERROR")
        return None, None, f"connection error: {e}"
