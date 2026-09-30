"""
# WebAPI文档

` python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml `

## 执行参数:
    `-a` - `绑定地址, 默认"127.0.0.1"`
    `-p` - `绑定端口, 默认9880`
    `-c` - `TTS配置文件路径, 默认"GPT_SoVITS/configs/tts_infer.yaml"`

## 调用:

### 推理

endpoint: `/tts`
GET:
```
http://127.0.0.1:9880/tts?text=先帝创业未半而中道崩殂，今天下三分，益州疲弊，此诚危急存亡之秋也。&text_lang=zh&ref_audio_path=archive_jingyuan_1.wav&prompt_lang=zh&prompt_text=我是「罗浮」云骑将军景元。不必拘谨，「将军」只是一时的身份，你称呼我景元便可&text_split_method=cut5&batch_size=1&media_type=wav&streaming_mode=true
```

POST:
```json
{
    "text": "",                   # str.(required) text to be synthesized
    "text_lang: "",               # str.(required) language of the text to be synthesized
    "ref_audio_path": "",         # str.(required) reference audio path
    "aux_ref_audio_paths": [],    # list.(optional) auxiliary reference audio paths for multi-speaker tone fusion
    "prompt_text": "",            # str.(optional) prompt text for the reference audio
    "prompt_lang": "",            # str.(required) language of the prompt text for the reference audio
    "top_k": 5,                   # int. top k sampling
    "top_p": 1,                   # float. top p sampling
    "temperature": 1,             # float. temperature for sampling
    "text_split_method": "cut5",  # str. text split method, see text_segmentation_method.py for details.
    "batch_size": 1,              # int. batch size for inference
    "batch_threshold": 0.75,      # float. threshold for batch splitting.
    "split_bucket": True,         # bool. whether to split the batch into multiple buckets.
    "speed_factor":1.0,           # float. control the speed of the synthesized audio.
    "fragment_interval":0.3,      # float. to control the interval of the audio fragment.
    "seed": -1,                   # int. random seed for reproducibility.
    "parallel_infer": True,       # bool. whether to use parallel inference.
    "repetition_penalty": 1.35,   # float. repetition penalty for T2S model.
    "sample_steps": 32,           # int. number of sampling steps for VITS model V3.
    "super_sampling": False,      # bool. whether to use super-sampling for audio when using VITS model V3.
    "streaming_mode": False,      # bool or int. return audio chunk by chunk.T he available options are: 0,1,2,3 or True/False (0/False: Disabled | 1/True: Best Quality, Slowest response speed (old version streaming_mode) | 2: Medium Quality, Slow response speed | 3: Lower Quality, Faster response speed )
    "overlap_length": 2,          # int. overlap length of semantic tokens for streaming mode.
    "min_chunk_length": 16,       # int. The minimum chunk length of semantic tokens for streaming mode. (affects audio chunk size)
}
```

RESP:
成功: 直接返回 wav 音频流， http code 200
失败: 返回包含错误信息的 json, http code 400

### 命令控制

endpoint: `/control`

command:
"restart": 重新运行
"exit": 结束运行

GET:
```
http://127.0.0.1:9880/control?command=restart
```
POST:
```json
{
    "command": "restart"
}
```

RESP: 无


### 切换GPT模型

endpoint: `/set_gpt_weights`

GET:
```
http://127.0.0.1:9880/set_gpt_weights?weights_path=GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt
```
RESP:
成功: 返回"success", http code 200
失败: 返回包含错误信息的 json, http code 400


### 切换Sovits模型

endpoint: `/set_sovits_weights`

GET:
```
http://127.0.0.1:9880/set_sovits_weights?weights_path=GPT_SoVITS/pretrained_models/s2G488k.pth
```

RESP:
成功: 返回"success", http code 200
失败: 返回包含错误信息的 json, http code 400

"""

import os
import sys
import traceback
from typing import Generator, Union

now_dir = os.getcwd()
sys.path.append(now_dir)
sys.path.append("%s/GPT_SoVITS" % (now_dir))

import argparse
import subprocess
import wave
import signal
import numpy as np
import soundfile as sf
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
from io import BytesIO
from tools.i18n.i18n import I18nAuto
from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
from GPT_SoVITS.TTS_infer_pack.text_segmentation_method import get_method_names as get_cut_method_names
from pydantic import BaseModel
import threading
import torch

# print(sys.path)
i18n = I18nAuto()
cut_method_names = get_cut_method_names()

num_cores = 10
torch.set_num_threads(num_cores)
torch.set_num_interop_threads(num_cores)

os.environ["MKL_DOMAIN_NUM_THREADS"] = f"DOMAIN_DFT={num_cores}"
os.environ["MKL_DYNAMIC"] = "FALSE"

torch.backends.cudnn.benchmark = False
torch.set_flush_denormal(True)

parser = argparse.ArgumentParser(description="GPT-SoVITS api")
parser.add_argument("-c", "--tts_config", type=str, default="GPT_SoVITS/configs/tts_infer.yaml", help="tts_infer路径")
parser.add_argument("-a", "--bind_addr", type=str, default="127.0.0.1", help="default: 127.0.0.1")
parser.add_argument("-p", "--port", type=int, default="9880", help="default: 9880")
parser.add_argument("--device", type=str, default="cpu", help="指定运行设备，默认自动选择")
args = parser.parse_args()
config_path = args.tts_config
device = args.device
port = args.port
host = args.bind_addr
argv = sys.argv

if config_path in [None, ""]:
    config_path = "GPT-SoVITS/configs/tts_infer.yaml"

tts_config = TTS_Config(config_path)
tts_config.device = device
print(tts_config)
tts_pipeline = TTS(tts_config)
tts_pipeline.t2s_model = tts_pipeline.t2s_model.to(dtype=torch.bfloat16)
tts_pipeline.vits_model = tts_pipeline.vits_model.to(dtype=torch.bfloat16)

APP = FastAPI()


class TTS_Request(BaseModel):
    text: str = None
    text_lang: str = None
    ref_audio_path: str = None
    aux_ref_audio_paths: list = None
    prompt_lang: str = None
    prompt_text: str = ""
    top_k: int = 5
    top_p: float = 1
    temperature: float = 1
    text_split_method: str = "cut5"
    batch_size: int = 1
    batch_threshold: float = 0.75
    split_bucket: bool = True
    speed_factor: float = 1.0
    fragment_interval: float = 0.3
    seed: int = -1
    media_type: str = "wav"
    streaming_mode: Union[bool, int] = False
    parallel_infer: bool = True
    repetition_penalty: float = 1.35
    sample_steps: int = 32
    super_sampling: bool = False
    overlap_length: int = 2
    min_chunk_length: int = 16


def pack_ogg(io_buffer: BytesIO, data: np.ndarray, rate: int):
    # Author: AkagawaTsurunaki
    # Issue:
    #   Stack overflow probabilistically occurs
    #   when the function `sf_writef_short` of `libsndfile_64bit.dll` is called
    #   using the Python library `soundfile`
    # Note:
    #   This is an issue related to `libsndfile`, not this project itself.
    #   It happens when you generate a large audio tensor (about 499804 frames in my PC)
    #   and try to convert it to an ogg file.
    # Related:
    #   https://github.com/RVC-Boss/GPT-SoVITS/issues/1199
    #   https://github.com/libsndfile/libsndfile/issues/1023
    #   https://github.com/bastibe/python-soundfile/issues/396
    # Suggestion:
    #   Or split the whole audio data into smaller audio segment to avoid stack overflow?

    def handle_pack_ogg():
        with sf.SoundFile(io_buffer, mode="w", samplerate=rate, channels=1, format="ogg") as audio_file:
            audio_file.write(data)



    # See: https://docs.python.org/3/library/threading.html
    # The stack size of this thread is at least 32768
    # If stack overflow error still occurs, just modify the `stack_size`.
    # stack_size = n * 4096, where n should be a positive integer.
    # Here we chose n = 4096.
    stack_size = 4096 * 4096
    try:
        threading.stack_size(stack_size)
        pack_ogg_thread = threading.Thread(target=handle_pack_ogg)
        pack_ogg_thread.start()
        pack_ogg_thread.join()
    except RuntimeError as e:
        # If changing the thread stack size is unsupported, a RuntimeError is raised.
        print("RuntimeError: {}".format(e))
        print("Changing the thread stack size is unsupported.")
    except ValueError as e:
        # If the specified stack size is invalid, a ValueError is raised and the stack size is unmodified.
        print("ValueError: {}".format(e))
        print("The specified stack size is invalid.")

    return io_buffer


def pack_raw(io_buffer: BytesIO, data: np.ndarray, rate: int):
    io_buffer.write(data.tobytes())
    return io_buffer


def pack_wav(io_buffer: BytesIO, data: np.ndarray, rate: int):
    io_buffer = BytesIO()
    sf.write(io_buffer, data, rate, format="wav")
    return io_buffer


def pack_aac(io_buffer: BytesIO, data: np.ndarray, rate: int):
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-f",
            "s16le",  # 输入16位有符号小端整数PCM
            "-ar",
            str(rate),  # 设置采样率
            "-ac",
            "1",  # 单声道
            "-i",
            "pipe:0",  # 从管道读取输入
            "-c:a",
            "aac",  # 音频编码器为AAC
            "-b:a",
            "192k",  # 比特率
            "-vn",  # 不包含视频
            "-f",
            "adts",  # 输出AAC数据流格式
            "pipe:1",  # 将输出写入管道
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, _ = process.communicate(input=data.tobytes())
    io_buffer.write(out)
    return io_buffer


def pack_audio(io_buffer: BytesIO, data: np.ndarray, rate: int, media_type: str):
    if media_type == "ogg":
        io_buffer = pack_ogg(io_buffer, data, rate)
    elif media_type == "aac":
        io_buffer = pack_aac(io_buffer, data, rate)
    elif media_type == "wav":
        io_buffer = pack_wav(io_buffer, data, rate)
    else:
        io_buffer = pack_raw(io_buffer, data, rate)
    io_buffer.seek(0)
    return io_buffer


# from https://huggingface.co/spaces/coqui/voice-chat-with-mistral/blob/main/app.py
def wave_header_chunk(frame_input=b"", channels=1, sample_width=2, sample_rate=32000):
    # This will create a wave header then append the frame input
    # It should be first on a streaming wav file
    # Other frames better should not have it (else you will hear some artifacts each chunk start)
    wav_buf = BytesIO()
    with wave.open(wav_buf, "wb") as vfout:
        vfout.setnchannels(channels)
        vfout.setsampwidth(sample_width)
        vfout.setframerate(sample_rate)
        vfout.writeframes(frame_input)

    wav_buf.seek(0)
    return wav_buf.read()


def handle_control(command: str):
    if command == "restart":
        os.execl(sys.executable, sys.executable, *argv)
    elif command == "exit":
        os.kill(os.getpid(), signal.SIGTERM)
        exit(0)


def check_params(req: dict):
    text: str = req.get("text", "")
    text_lang: str = req.get("text_lang", "")
    ref_audio_path: str = req.get("ref_audio_path", "")
    streaming_mode: bool = req.get("streaming_mode", False)
    media_type: str = req.get("media_type", "wav")
    prompt_lang: str = req.get("prompt_lang", "")
    text_split_method: str = req.get("text_split_method", "cut5")

    if ref_audio_path in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "ref_audio_path is required"})
    if text in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "text is required"})
    if text_lang in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "text_lang is required"})
    elif text_lang.lower() not in tts_config.languages:
        return JSONResponse(
            status_code=400,
            content={"message": f"text_lang: {text_lang} is not supported in version {tts_config.version}"},
        )
    if prompt_lang in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "prompt_lang is required"})
    elif prompt_lang.lower() not in tts_config.languages:
        return JSONResponse(
            status_code=400,
            content={"message": f"prompt_lang: {prompt_lang} is not supported in version {tts_config.version}"},
        )
    if media_type not in ["wav", "raw", "ogg", "aac"]:
        return JSONResponse(status_code=400, content={"message": f"media_type: {media_type} is not supported"})
    # elif media_type == "ogg" and not streaming_mode:
    #     return JSONResponse(status_code=400, content={"message": "ogg format is not supported in non-streaming mode"})

    if text_split_method not in cut_method_names:
        return JSONResponse(
            status_code=400, content={"message": f"text_split_method:{text_split_method} is not supported"}
        )

    return None


async def tts_handle(req: dict):
    """
    Text to speech handler.

    Args:
        req (dict):
            {
                "text": "",                   # str.(required) text to be synthesized
                "text_lang: "",               # str.(required) language of the text to be synthesized
                "ref_audio_path": "",         # str.(required) reference audio path
                "aux_ref_audio_paths": [],    # list.(optional) auxiliary reference audio paths for multi-speaker tone fusion
                "prompt_text": "",            # str.(optional) prompt text for the reference audio
                "prompt_lang": "",            # str.(required) language of the prompt text for the reference audio
                "top_k": 5,                   # int. top k sampling
                "top_p": 1,                   # float. top p sampling
                "temperature": 1,             # float. temperature for sampling
                "text_split_method": "cut5",  # str. text split method, see text_segmentation_method.py for details.
                "batch_size": 1,              # int. batch size for inference
                "batch_threshold": 0.75,      # float. threshold for batch splitting.
                "split_bucket": True,         # bool. whether to split the batch into multiple buckets.
                "speed_factor":1.0,           # float. control the speed of the synthesized audio.
                "fragment_interval":0.3,      # float. to control the interval of the audio fragment.
                "seed": -1,                   # int. random seed for reproducibility.
                "parallel_infer": True,       # bool. whether to use parallel inference.
                "repetition_penalty": 1.35,   # float. repetition penalty for T2S model.
                "sample_steps": 32,           # int. number of sampling steps for VITS model V3.
                "super_sampling": False,      # bool. whether to use super-sampling for audio when using VITS model V3.
                "streaming_mode": False,      # bool or int. return audio chunk by chunk.T he available options are: 0,1,2,3 or True/False (0/False: Disabled | 1/True: Best Quality, Slowest response speed (old version streaming_mode) | 2: Medium Quality, Slow response speed | 3: Lower Quality, Faster response speed )
                "overlap_length": 2,          # int. overlap length of semantic tokens for streaming mode.
                "min_chunk_length": 16,       # int. The minimum chunk length of semantic tokens for streaming mode. (affects audio chunk size)
            }
    returns:
        StreamingResponse: audio stream response.
    """

    streaming_mode = req.get("streaming_mode", False)
    return_fragment = req.get("return_fragment", False)
    media_type = req.get("media_type", "wav")

    check_res = check_params(req)
    if check_res is not None:
        return check_res
    
    if streaming_mode == 0:
        streaming_mode = False
        return_fragment = False
        fixed_length_chunk = False
    elif streaming_mode == 1:
        streaming_mode = False
        return_fragment = True
        fixed_length_chunk = False
    elif streaming_mode == 2:
        streaming_mode = True
        return_fragment = False
        fixed_length_chunk = False
    elif streaming_mode == 3:
        streaming_mode = True
        return_fragment = False
        fixed_length_chunk = True

    else:
        return JSONResponse(status_code=400, content={"message": f"the value of streaming_mode must be 0, 1, 2, 3(int) or true/false(bool)"})

    req["streaming_mode"] = streaming_mode
    req["return_fragment"] = return_fragment
    req["fixed_length_chunk"] = fixed_length_chunk

    print(f"{streaming_mode} {return_fragment} {fixed_length_chunk}")

    streaming_mode = streaming_mode or return_fragment


    try:
        with torch.cpu.amp.autocast(enabled=True, dtype=torch.bfloat16):
            tts_generator = tts_pipeline.run(req)

        if streaming_mode:

            def streaming_generator(tts_generator: Generator, media_type: str):
                if_frist_chunk = True
                for sr, chunk in tts_generator:
                    if if_frist_chunk and media_type == "wav":
                        yield wave_header_chunk(sample_rate=sr)
                        media_type = "raw"
                        if_frist_chunk = False
                    yield pack_audio(BytesIO(), chunk, sr, media_type).getvalue()

            # _media_type = f"audio/{media_type}" if not (streaming_mode and media_type in ["wav", "raw"]) else f"audio/x-{media_type}"
            return StreamingResponse(
                streaming_generator(
                    tts_generator,
                    media_type,
                ),
                media_type=f"audio/{media_type}",
            )

        else:
            sr, audio_data = next(tts_generator)
            audio_data = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
            return Response(audio_data, media_type=f"audio/{media_type}")
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "tts failed", "Exception": str(e)})


@APP.get("/control")
async def control(command: str = None):
    if command is None:
        return JSONResponse(status_code=400, content={"message": "command is required"})
    handle_control(command)


@APP.get("/tts")
async def tts_get_endpoint(
    text: str = None,
    text_lang: str = None,
    ref_audio_path: str = None,
    aux_ref_audio_paths: list = None,
    prompt_lang: str = None,
    prompt_text: str = "",
    top_k: int = 5,
    top_p: float = 1,
    temperature: float = 1,
    text_split_method: str = "cut5",
    batch_size: int = 1,
    batch_threshold: float = 0.75,
    split_bucket: bool = True,
    speed_factor: float = 1.0,
    fragment_interval: float = 0.3,
    seed: int = -1,
    media_type: str = "wav",
    parallel_infer: bool = True,
    repetition_penalty: float = 1.35,
    sample_steps: int = 32,
    super_sampling: bool = False,
    streaming_mode: Union[bool, int] = False,
    overlap_length: int = 2,
    min_chunk_length: int = 16,
):
    req = {
        "text": text,
        "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "aux_ref_audio_paths": aux_ref_audio_paths,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang.lower(),
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "text_split_method": text_split_method,
        "batch_size": int(batch_size),
        "batch_threshold": float(batch_threshold),
        "speed_factor": float(speed_factor),
        "split_bucket": split_bucket,
        "fragment_interval": fragment_interval,
        "seed": seed,
        "media_type": media_type,
        "streaming_mode": streaming_mode,
        "parallel_infer": parallel_infer,
        "repetition_penalty": float(repetition_penalty),
        "sample_steps": int(sample_steps),
        "super_sampling": super_sampling,
        "overlap_length": int(overlap_length),
        "min_chunk_length": int(min_chunk_length),
    }
    return await tts_handle(req)


@APP.post("/tts")
async def tts_post_endpoint(request: TTS_Request):
    req = request.dict()
    return await tts_handle(req)


@APP.get("/set_refer_audio")
async def set_refer_aduio(refer_audio_path: str = None):
    try:
        tts_pipeline.set_ref_audio(refer_audio_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "set refer audio failed", "Exception": str(e)})
    return JSONResponse(status_code=200, content={"message": "success"})


# @APP.post("/set_refer_audio")
# async def set_refer_aduio_post(audio_file: UploadFile = File(...)):
#     try:
#         # 检查文件类型，确保是音频文件
#         if not audio_file.content_type.startswith("audio/"):
#             return JSONResponse(status_code=400, content={"message": "file type is not supported"})

#         os.makedirs("uploaded_audio", exist_ok=True)
#         save_path = os.path.join("uploaded_audio", audio_file.filename)
#         # 保存音频文件到服务器上的一个目录
#         with open(save_path , "wb") as buffer:
#             buffer.write(await audio_file.read())

#         tts_pipeline.set_ref_audio(save_path)
#     except Exception as e:
#         return JSONResponse(status_code=400, content={"message": f"set refer audio failed", "Exception": str(e)})
#     return JSONResponse(status_code=200, content={"message": "success"})


@APP.get("/set_gpt_weights")
async def set_gpt_weights(weights_path: str = None):
    try:
        if weights_path in ["", None]:
            return JSONResponse(status_code=400, content={"message": "gpt weight path is required"})
        tts_pipeline.init_t2s_weights(weights_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "change gpt weight failed", "Exception": str(e)})

    return JSONResponse(status_code=200, content={"message": "success"})


@APP.get("/set_sovits_weights")
async def set_sovits_weights(weights_path: str = None):
    try:
        if weights_path in ["", None]:
            return JSONResponse(status_code=400, content={"message": "sovits weight path is required"})
        tts_pipeline.init_vits_weights(weights_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "change sovits weight failed", "Exception": str(e)})
    return JSONResponse(status_code=200, content={"message": "success"})


# ============================================================
# /synth - UTAU-style note-array synthesis (per-syllable pitch + duration)
#
# Fully additive: does not import/modify anything used by /tts, does not
# mutate tts_pipeline state, and only calls tts_pipeline.run() the same
# read-only way the existing /tts endpoint does. A bug here cannot break
# /tts, /set_gpt_weights, /set_sovits_weights, etc.
# ============================================================
import librosa
import scipy.signal as _scipy_signal
import hashlib

_SYNTH_VOICEBANK_DIR = os.path.join(now_dir, "synth_voicebank")
try:
    os.makedirs(_SYNTH_VOICEBANK_DIR, exist_ok=True)
except Exception:
    pass


def _voicebank_key(lyric: str, req: dict, text_lang: str) -> str:
    # One cache slot per (lyric, voice/reference config) - this is exactly what a
    # UTAU voicebank is: one recorded sample per mora, reused for every note that
    # needs it. Anything that would change the raw TTS timbre goes into the key.
    raw = "|".join(str(x) for x in [
        "v7-cons",  # bump to invalidate clips cached by older cleaning logic
        req.get("ref_audio_path"), req.get("prompt_text"), req.get("prompt_lang"),
        text_lang, lyric, req.get("text_split_method", "cut0"),
        req.get("top_k", 5), req.get("top_p", 1), req.get("temperature", 1),
        req.get("seed", -1), req.get("repetition_penalty", 1.35),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _voicebank_load(key: str):
    path = os.path.join(_SYNTH_VOICEBANK_DIR, key + ".wav")
    if not os.path.exists(path):
        return None
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            frames = w.readframes(w.getnframes())
        audio_i16 = np.frombuffer(frames, dtype=np.int16)
        return sr, audio_i16.astype(np.float32) / 32768.0
    except Exception:
        return None


def _voicebank_save(key: str, sr: int, audio: np.ndarray) -> None:
    path = os.path.join(_SYNTH_VOICEBANK_DIR, key + ".wav")
    try:
        clipped = np.clip(audio, -1.0, 1.0).astype(np.float32)
        # soundfile instead of the stdlib `wave` module: wave.Wave_write's __del__ calls
        # close() unconditionally, and on this Python build close() does `del self._file`
        # on success - so the GC-triggered second close() throws "no attribute '_file'"
        # (harmless, but spams the console on every single syllable we cache).
        sf.write(path, clipped, sr, subtype="PCM_16")
    except Exception:
        pass


def _synth_trim_silence(audio: np.ndarray, top_db: float = 32.0) -> np.ndarray:
    # Strips leading/trailing near-silence from a single synthesized syllable.
    # GPT-SoVITS pads short single-mora outputs with silence at both ends; left
    # untrimmed, that padding stacks up between notes and reads as "big gaps
    # between syllables" once everything is concatenated.
    if len(audio) == 0:
        return audio
    try:
        trimmed, _ = librosa.effects.trim(audio, top_db=top_db)
        return trimmed if len(trimmed) > 0 else audio
    except Exception:
        return audio


import pyworld as _pw


def _resize_frames(arr: np.ndarray, new_len: int) -> np.ndarray:
    old_len = arr.shape[0]
    if old_len == new_len or old_len == 0 or new_len <= 0:
        return arr
    x_old = np.linspace(0.0, 1.0, old_len)
    x_new = np.linspace(0.0, 1.0, new_len)
    if arr.ndim == 1:
        return np.interp(x_new, x_old, arr)
    out = np.zeros((new_len, arr.shape[1]), dtype=arr.dtype)
    for i in range(arr.shape[1]):
        out[:, i] = np.interp(x_new, x_old, arr[:, i])
    return out


def _synth_apply_pitch_and_duration(audio: np.ndarray, sr: int, notenum, target_ms: float = None,
                                     base_notenum: int = None) -> np.ndarray:
    # WORLD-vocoder based note rendering - this is the actual technique UTAU-style
    # resamplers use under the hood: decompose into F0 (pitch) + spectral envelope
    # (formants/timbre) + aperiodicity, so pitch can be moved and duration can be
    # resized WITHOUT touching timbre at all (unlike plain resample, which drags
    # formants along -> chipmunk/"voice changer" sound; and unlike a phase vocoder
    # or hand-rolled LPC vocoder, which both tend to buzz/rob on very short clips).
    #
    # Per note we auto-detect the syllable's OWN natural pitch (median F0) and treat
    # that as its "monotone" reference, then shift relative to that - like a UTAU
    # voicebank sample recorded at one pitch, resampled to the target note. This is
    # more accurate than assuming a fixed global base_notenum, which is usually not
    # the TTS output's real natural pitch and was the source of "sai pitch" (wrong
    # target pitch) before.
    if len(audio) == 0:
        return audio
    frame_period = 5.0  # ms, standard WORLD analysis frame
    x = np.ascontiguousarray(audio.astype(np.float64))
    try:
        f0, t = _pw.dio(x, sr, frame_period=frame_period)
        f0 = _pw.stonemask(x, f0, t, sr)
        sp = _pw.cheaptrick(x, f0, t, sr)
        ap = _pw.d4c(x, f0, t, sr)
    except Exception:
        # WORLD analysis failed outright (e.g. degenerate/near-empty audio) - fall
        # back to returning the untouched clip rather than erroring the whole note.
        return audio

    voiced = f0 > 0
    if notenum is not None and voiced.any():
        base_freq = float(np.median(f0[voiced]))
        if base_notenum is not None and base_freq <= 0:
            base_freq = 440.0 * (2.0 ** ((base_notenum - 69) / 12.0))
        target_freq = 440.0 * (2.0 ** ((notenum - 69) / 12.0))
        if base_freq > 0:
            ratio = target_freq / base_freq
            f0 = np.where(voiced, f0 * ratio, 0.0)

    if target_ms:
        target_frames = max(1, int(round(target_ms / frame_period)))
        f0 = _resize_frames(f0, target_frames)
        sp = _resize_frames(sp, target_frames)
        ap = _resize_frames(ap, target_frames)

    try:
        y = _pw.synthesize(np.ascontiguousarray(f0.astype(np.float64)),
                            np.ascontiguousarray(sp.astype(np.float64)),
                            np.ascontiguousarray(ap.astype(np.float64)),
                            sr, frame_period)
    except Exception:
        return audio
    return y.astype(np.float32)


def _synth_join_segments(segments: list, sr: int, gap_ms: float = 25.0, fade_ms: float = 6.0) -> np.ndarray:
    # A small silence gap between syllables (like the natural micro-pauses between
    # morae in real speech/singing) instead of crossfading them straight into each
    # other - crossfading with no gap is what made everything sound "dinh voi
    # nhau" (glued together). A short fade-in/out at each segment's own edges
    # avoids clicks at the silence boundary without removing the gap itself.
    if not segments:
        return np.zeros(0, dtype=np.float32)
    fade_len = int(sr * fade_ms / 1000.0)
    gap = np.zeros(max(0, int(sr * gap_ms / 1000.0)), dtype=np.float32)
    parts = []
    for i, seg in enumerate(segments):
        seg = seg.astype(np.float32).copy()
        if fade_len > 0 and len(seg) > fade_len * 2:
            seg[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            seg[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
        parts.append(seg)
        if i < len(segments) - 1 and len(gap) > 0:
            parts.append(gap)
    return np.concatenate(parts)


class SynthNote(BaseModel):
    lyric: str = ""  # "R" or empty = rest
    notenum: int = None  # UTAU-style MIDI note number, e.g. 60 = C4. Omit -> keep natural pitch.
    noteNumber: int = None  # alias of notenum (UTAU json export naming)
    length: float = None  # note (beat) length in ms
    lang: str = None  # per-note language override (falls back to request text_lang)
    velocity: float = 100.0  # consonant velocity 0-200 (higher = shorter consonant)
    intensity: float = 100.0  # per-note volume %
    modulation: float = 0.0  # 0 = flat/monotone pitch, 100 = keep natural pitch contour
    flags: str = ""  # UTAU flags, supported: g (gender), B (breath), t (cents)
    preutterance: float = None  # ms override; default = consonant length
    overlap: float = None  # ms override; default = request overlap_ms
    tempo: float = None  # accepted for UTAU compatibility, unused (length is already ms)
    pbs: str = None  # UTAU pitch bend start "x;y" (ms ; 10 cent)
    pbw: str = None  # UTAU pitch bend widths (ms), comma separated
    pby: str = None  # UTAU pitch bend y (10 cent), comma separated
    pbm: str = None  # UTAU pitch bend interpolation modes
    vbr: str = None  # UTAU vibrato: len%,period ms,depth cent,in%,out%,phase%,offset%,height%


class Synth_Request(BaseModel):
    notes: list[SynthNote] = None
    ust: str = None  # raw text of a UTAU .ust file (used when notes is empty)
    text_lang: str = None
    ref_audio_path: str = None
    aux_ref_audio_paths: list = None
    prompt_lang: str = None
    prompt_text: str = ""
    base_notenum: int = 60  # MIDI note the ref_audio/prompt_text is assumed to sing at
    top_k: int = 5
    top_p: float = 1
    temperature: float = 1
    text_split_method: str = "cut0"
    batch_size: int = 1
    seed: int = -1
    media_type: str = "wav"
    repetition_penalty: float = 1.35
    crossfade_ms: float = 20.0
    gap_ms: float = 28.0  # vowel is released this many ms before the note ends (legato -> 0)
    overlap_ms: float = 8.0  # default crossfade between previous note tail and this note's consonant
    pitch_mode: str = "shift"  # "shift" = WORLD shifts pitch only (clearest); "ref" = also steer the model with a pitch-shifted reference (measured: worse intelligibility)
    record_attempts: int = 5  # max TTS takes per new syllable when building the voicebank
    transpose: int = None  # semitones added to every note; None = auto (whole octaves toward voice_center)
    auto_octave: bool = True
    voice_center: float = 66.0  # MIDI note Arona's voice naturally sits at
    verify_asr: bool = False  # True = ASR-check each new syllable take (slow; measured no clear intelligibility gain)
    clarity: float = 0.6  # 0 = off; cleaner voiced frames, crisper formants, louder consonants
    pitch_natural: float = 0.0  # fraction of natural pitch contour kept when modulation=0 (flat = clearest, but robotic)
    humanize: float = 0.0  # 0 = off; slow +-4 cent pitch jitter (perfectly steady F0 = robotic)

def _synth_check_params(req: dict):
    if not req.get("notes"):
        return JSONResponse(status_code=400, content={"message": "notes (json array) is required and must not be empty"})
    if req.get("ref_audio_path") in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "ref_audio_path is required"})
    text_lang = req.get("text_lang")
    prompt_lang = req.get("prompt_lang")
    if text_lang in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "text_lang is required"})
    elif text_lang.lower() not in tts_config.languages:
        return JSONResponse(status_code=400, content={"message": f"text_lang: {text_lang} is not supported in version {tts_config.version}"})
    if prompt_lang in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "prompt_lang is required"})
    elif prompt_lang.lower() not in tts_config.languages:
        return JSONResponse(status_code=400, content={"message": f"prompt_lang: {prompt_lang} is not supported in version {tts_config.version}"})
    return None


async def synth_handle(req: dict):
    """
    /synth - synthesize a sequence of UTAU-style notes (json array), each with its
    own lyric / pitch (notenum) / duration (length in ms), and stitch them into one clip.
    """
    check_res = _synth_check_params(req)
    if check_res is not None:
        return check_res

    notes = req["notes"]
    base_notenum = req.get("base_notenum", 60)
    media_type = req.get("media_type", "wav")
    crossfade_ms = req.get("crossfade_ms", 20.0)
    gap_ms = req.get("gap_ms", 20.0)
    overlap_ms = req.get("overlap_ms", 12.0)

    # Octave placement: Arona's natural speaking pitch is ~MIDI 66, but UTAU sources are
    # often written an octave or two lower. Shifting a voice 12+ semitones down with a
    # vocoder is what made it sound deep / "not Arona", so move the WHOLE song by whole
    # octaves so its median note lands near her natural range. `transpose` overrides.
    def _note_num(n):
        v = n.get("notenum")
        return n.get("noteNumber") if v is None else v
    transpose = req.get("transpose")
    if transpose is None and req.get("auto_octave", True):
        nums = [_note_num(n) for n in notes
                if (n.get("lyric") or "").strip().lower() not in ("", "r") and _note_num(n) is not None]
        if nums:
            med = float(np.median(nums))
            transpose = 12 * int(round((req.get("voice_center", 66) - med) / 12.0))
    transpose = int(transpose or 0)

    import synth_engine
    items = []
    out_sr = None
    try:
        for note in notes:
            lyric = (note.get("lyric") or "").strip()
            if lyric == "" or lyric.lower() == "r":
                if note.get("length"):
                    items.append({"kind": "rest", "length_ms": note.get("length")})
                continue

            text_lang_note = (note.get("lang") or req.get("text_lang")).lower()
            notenum = note.get("notenum")
            if notenum is None:
                notenum = note.get("noteNumber")
            if notenum is not None:
                notenum = int(notenum) + transpose
            length_ms = note.get("length")

            # A user test confirmed: asking GPT-SoVITS for a mora extended with the
            # Japanese long-vowel mark (e.g. "あー") naturally sustains the vowel properly,
            # instead of us DSP-stretching one short isolated take (which read as "ran out
            # of breath"). Repeating the WHOLE mora instead (e.g. "ににに") was tried first
            # but re-triggers the consonant each time - a held "ni" came out as "ni-ni"
            # instead of "nii", which is exactly the choonpu mark's job: extend the vowel
            # without re-attacking the consonant. So for notes that need real length,
            # extend with choonpu instead, and let the (now much smaller) WORLD stretch
            # just fine-tune the exact ms. Bucketed so the cache still gets reused across
            # notes of similar length instead of one cache entry per exact millisecond.
            def _repeat_count(ms):
                if not ms or ms <= 450:
                    return 1
                if ms <= 900:
                    return 2
                if ms <= 1500:
                    return 3
                return 4
            repeat_n = _repeat_count(length_ms)
            base_text = lyric + "ー" * (repeat_n - 1)

            # pitch_mode "ref": try to steer the model itself toward the note by giving it
            # a reference clip pitch-shifted (formants kept) to that note. Default "shift"
            # = generate as-is and move pitch afterwards with WORLD (UTAU style).
            req_note = dict(req)
            if req.get("pitch_mode") == "ref" and notenum is not None:
                try:
                    req_note["ref_audio_path"] = synth_engine.make_pitched_ref(
                        req["ref_audio_path"], notenum, os.path.join(_SYNTH_VOICEBANK_DIR, "refs"))
                except Exception:
                    traceback.print_exc()

            vb_key = _voicebank_key(base_text, req_note, text_lang_note)
            cached = _voicebank_load(vb_key)
            if cached is not None:
                sr, audio = cached
                out_sr = sr
            else:
                best = None
                # single mora alone often comes out as mostly breath; later attempts add
                # punctuation to push the model toward actually voicing it, AND turn up
                # sampling randomness/temperature so a syllable that's stuck producing
                # breath every time has an actual chance to land somewhere different
                # (same punctuation + same near-greedy sampling just reproduces the same
                # breath-only result attempt after attempt).
                text_variants = [base_text, base_text + "ー", base_text + "、", base_text + "。", base_text + "！"]
                # mora co phu am: GPT-SoVITS hay nuot phu am khi mora dung 1 minh. Them bien the sokuon
                # "っ"+mora (dong am -> burst/ma sat ro) va CHON take co phu am manh hon (consonant_score).
                is_cons_mora = lyric[:1] not in "あいうえおんー" and not lyric.isascii()
                if is_cons_mora:
                    text_variants = [base_text, "っ" + base_text, base_text + "ー", base_text + "、", base_text + "。"]
                text_variants = text_variants[:max(1, int(req.get("record_attempts", 5)))]
                use_asr = bool(req.get("verify_asr", False))
                for attempt in range(len(text_variants)):
                    seed0 = req.get("seed", -1)
                    escalate = attempt >= 2  # first 2 tries stay "clean"; only widen sampling once those failed
                    note_req = {
                        "text": text_variants[attempt],
                        "text_lang": text_lang_note,
                        "ref_audio_path": req_note.get("ref_audio_path"),
                        "aux_ref_audio_paths": req.get("aux_ref_audio_paths"),
                        "prompt_text": req.get("prompt_text", ""),
                        "prompt_lang": req.get("prompt_lang").lower(),
                        "top_k": req.get("top_k", 5) + (10 if escalate else 0),
                        "top_p": req.get("top_p", 1),
                        "temperature": req.get("temperature", 1) * (1.25 if escalate else 1.0),
                        "text_split_method": req.get("text_split_method", "cut0"),
                        "batch_size": int(req.get("batch_size", 1)),
                        "batch_threshold": 0.75,
                        "split_bucket": False,
                        "speed_factor": 1.0,
                        "fragment_interval": 0.3,
                        "seed": seed0 if seed0 < 0 else seed0 + attempt,
                        "media_type": "wav",
                        "streaming_mode": False,
                        "return_fragment": False,
                        "fixed_length_chunk": False,
                        "parallel_infer": True,
                        "repetition_penalty": req.get("repetition_penalty", 1.35),
                        "sample_steps": 32,
                        "super_sampling": False,
                        "overlap_length": 2,
                        "min_chunk_length": 16,
                    }

                    with torch.cpu.amp.autocast(enabled=True, dtype=torch.bfloat16):
                        gen = tts_pipeline.run(note_req)
                    sr, audio_i16 = next(gen)
                    out_sr = sr
                    cand = audio_i16.astype(np.float32) / 32768.0
                    cand = _synth_trim_silence(cand)
                    # cut leading/trailing breath, keep only the real voiced syllable
                    cand, ok, voiced_ms = synth_engine.clean_clip(cand, sr, merge_repeats=(repeat_n > 1))
                    # "recording session": ASR must actually hear this mora, otherwise the
                    # model said the wrong syllable (the main cause of lost letters)
                    heard = bool(synth_engine.verify_mora(cand, sr, lyric)) if (ok and use_asr) else False
                    if ok and not use_asr:
                        heard = True  # ASR disabled: first clip with a real voiced syllable wins
                    cscore = round(synth_engine.consonant_score(cand, sr), 1) if (is_cons_mora and ok) else 0.0
                    score = (heard, ok, cscore, -attempt)  # ties -> earliest (plain lyric) candidate
                    try:
                        with open(os.path.join(_SYNTH_VOICEBANK_DIR, "build.log"), "a", encoding="utf-8") as _lf:
                            _lf.write(f"{lyric}\tattempt={attempt}\ttext={text_variants[attempt]}\theard={heard}\tok={ok}\tvoiced_ms={voiced_ms:.0f}\tcons={cscore}\n")
                    except Exception:
                        pass
                    if best is None or score > best[2]:
                        best = (cand, sr, score)
                    if heard and ok and (attempt >= 1 or not is_cons_mora or len(text_variants) < 2):
                        break  # otherwise wrong/breathy -> re-roll (keeps best candidate seen); cons morae: luon thu 2 take dau
                audio, sr, _ = best
                _voicebank_save(vb_key, sr, audio)  # recorded once, reused like a UTAU sample from here on

            rendered, cons = synth_engine.render_note(
                audio, sr, notenum, length_ms,
                velocity=note.get("velocity", 100.0),
                modulation=note.get("modulation", 0.0),
                flags=note.get("flags", ""),
                intensity=note.get("intensity", 100.0),
                clarity=req.get("clarity", 0.6),
                pitch_natural=req.get("pitch_natural", 0.0),
                pitch_bend={k: note.get(k) for k in ("pbs", "pbw", "pby", "pbm", "vbr")},
                humanize=req.get("humanize", 0.0),
            )
            items.append({
                "kind": "note", "audio": rendered, "cons": cons, "length_ms": length_ms,
                "overlap_ms": note.get("overlap"), "pre_ms": note.get("preutterance"),
            })

        if not any(it["kind"] == "note" for it in items):
            return JSONResponse(status_code=400, content={"message": "no synthesizable notes (all lyrics empty/rests)"})

        final_audio = synth_engine.mix_timeline(items, out_sr, gap_ms, overlap_ms)
        peak = float(np.abs(final_audio).max()) if len(final_audio) else 0.0
        if peak > 0.98:
            final_audio = final_audio * (0.98 / peak)  # scale, don't hard-clip (clipping = harsh/raspy)
        final_audio = np.clip(final_audio, -1.0, 1.0)
        final_audio_i16 = (final_audio * 32767).astype(np.int16)
        audio_data = pack_audio(BytesIO(), final_audio_i16, out_sr, media_type).getvalue()
        return Response(audio_data, media_type=f"audio/{media_type}", headers={"X-Synth-Transpose": str(transpose)})

    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "synth failed", "Exception": str(e)})


# Defaults so a bare `curl --data-binary @song.ust /synth` works with no params.
# Any of these can be overridden via JSON fields or query params.
_SYNTH_DEFAULTS = {
    "text_lang": "ja",
    "prompt_lang": "ja",
    "ref_audio_path": "output/slicer_opt/2.wav_0020727040_0020837440.wav",
    "prompt_text": "どうですか先生? 頑張れそうですか?",
}

from fastapi import Request as _SynthHttpRequest


@APP.post("/synth")
async def synth_post_endpoint(http_request: _SynthHttpRequest):
    """
    Accepts any of:
      1. JSON object   {"notes": [...], ...params}
      2. JSON array    [ {note}, {note}, ... ]
      3. JSON object   {"ust": "<ust file text>", ...params}
      4. raw .ust body (any non-JSON content-type), params via query string
    UST input is parsed into the same note list as JSON (ticks -> ms via tempo).
    """
    import synth_engine
    try:
        ctype = (http_request.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            data = await http_request.json()
            if isinstance(data, list):
                data = {"notes": data}
        else:
            body = await http_request.body()
            data = dict(http_request.query_params)
            data["ust"] = synth_engine._decode_text(body)

        for k, v in _SYNTH_DEFAULTS.items():
            if data.get(k) in (None, ""):
                data[k] = v

        req = Synth_Request(**data).dict()
        if req.get("ust") and not req.get("notes"):
            req["notes"], _ = synth_engine.parse_ust(req["ust"])
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "bad synth request", "Exception": str(e)})
    return await synth_handle(req)


if __name__ == "__main__":
    try:
        if host == "None":  # 在调用时使用 -a None 参数，可以让api监听双栈
            host = None
        uvicorn.run(app=APP, host=host, port=port, workers=1)
    except Exception:
        traceback.print_exc()
        os.kill(os.getpid(), signal.SIGTERM)
        exit(0)
