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
import re
import unicodedata
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
# Tieng Viet -> chu Han (Quang Dong, text_lang="yue"): model chi hoc ja nhung GPT-SoVITS doc duoc yue,
# tieng Cantonese gan tieng Viet (thanh dieu, am cuoi) nen 'viet lai' cach doc bang chu Han la nghe on nhat.
#  - VI_LEXICON : cum tu ghi de (khoa = tieng Viet KHONG DAU, chu thuong). Uu tien cao nhat. Them/sua trong
#                 bang vi_lexicon cua database/vi_yue.db (hoac o day).
#  - vi_syllable: bang am tiet (base, thanh) -> chu Han, sinh boi temp/vi_yue_build.py, luu trong database/vi_yue.db.
VI_LEXICON = {
    "anh do mixi": "晏度咪西",
    "do mixi": "度咪西",
    "mixi": "咪西",
}
VI_LANG = "yue"
_VI_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "vi_yue.db")
_VI_SYL = {}
_VI_TONES = {"\u0300": 1, "\u0301": 2, "\u0309": 3, "\u0303": 4, "\u0323": 5}  # huyen sac hoi nga nang (ngang = 0)
_LATIN_WORD_RE = re.compile(r"[A-Za-z\u0110\u0111\u00c0-\u1ef9]+")
_VI_DIACRITIC_RE = re.compile(
    r"\w*[ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]\w*"
)


def _fold_vi(s: str) -> str:
    """Bo dau + lowercase, GIU NGUYEN do dai (moi ky tu -> 1 ky tu) de index khop voi chuoi goc."""
    out = []
    for ch in s:
        if ch in "đĐ":
            out.append("d")
            continue
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
        out.append((base[:1] or ch).lower())
    return "".join(out)


def _compile_vi_re():
    global _VI_RE
    _VI_RE = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(r"\s+".join(map(re.escape, k.split())) for k in sorted(VI_LEXICON, key=len, reverse=True)) + r")(?![a-z0-9])"
    )


def load_vi_db():
    """(Re)load database/vi_yue.db -> _VI_SYL va VI_LEXICON. Goi lai sau khi sua DB."""
    try:
        import sqlite3
        con = sqlite3.connect(_VI_DB)
        _VI_SYL.clear()
        for base, tone, hanzi in con.execute("SELECT base, tone, hanzi FROM vi_syllable"):
            _VI_SYL[(base, tone)] = hanzi
        for key, hanzi in con.execute("SELECT key, hanzi FROM vi_lexicon"):
            VI_LEXICON[key] = hanzi
        con.close()
        console.log(f"VI->yue DB loaded: {len(_VI_SYL)} syllables, {len(VI_LEXICON)} lexicon", "INFO")
    except Exception as e:
        console.log(f"VI->yue DB not loaded ({_VI_DB}): {e}", "WARN")
    _compile_vi_re()


load_vi_db()


def _vi_syllable_hanzi(word: str):
    """'tày' / 'Tay' / 'đo' -> chu Han, hoac None neu khong phai am tiet Viet trong bang."""
    chars, tone = [], 0
    for c in unicodedata.normalize("NFD", word.lower()):
        if c in _VI_TONES:
            tone = _VI_TONES[c]
        else:
            chars.append(c)
    return _VI_SYL.get((unicodedata.normalize("NFC", "".join(chars)), tone))


def _convert_vi_words(chunk: str, lang: str, vi_context: bool):
    """chunk khong chua cum lexicon: tu Viet (co dau, hoac khong dau khi ca cau co tieng Viet) -> chu Han.
    Tra ve [(text, lang)], cac tu Viet lien nhau gop thanh 1 doan yue."""
    pieces, pos = [], 0   # ("vi", hanzi) | ("raw", text)
    for m in _LATIN_WORD_RE.finditer(chunk):
        w = m.group(0)
        hz = None
        if _VI_DIACRITIC_RE.fullmatch(w) or vi_context:
            hz = _vi_syllable_hanzi(w)
        if hz is None:
            continue
        if m.start() > pos:
            pieces.append(("raw", chunk[pos:m.start()]))
        pieces.append(("vi", hz))
        pos = m.end()
    if pos < len(chunk):
        pieces.append(("raw", chunk[pos:]))
    segs, buf, i = [], "", 0
    def flush_buf():
        nonlocal buf
        if buf:
            segs.append((buf, VI_LANG))
            buf = ""
    for i, (kind, val) in enumerate(pieces):
        if kind == "vi":
            buf += val
        elif buf and not re.search(r"\w", val) and i + 1 < len(pieces) and pieces[i + 1][0] == "vi":
            buf += re.sub(r"\s+", "", val)        # dau cau giua 2 tu Viet: giu lai, bo khoang trang
        else:
            flush_buf()
            if re.search(r"\w", val):
                segs.append((val.strip(), lang))
    flush_buf()
    return segs


def split_vietnamese(text: str, lang: str):
    """[(doan, text_lang)] - cum trong VI_LEXICON va am tiet Viet (bang vi_syllable) -> chu Han (yue),
    phan con lai giu nguyen lang."""
    folded = _fold_vi(text)
    vi_context = bool(_VI_DIACRITIC_RE.search(text))
    segs, pos = [], 0
    for m in _VI_RE.finditer(folded):
        segs += _convert_vi_words(text[pos:m.start()], lang, vi_context)
        segs.append((VI_LEXICON[re.sub(r"\s+", " ", m.group(0))], VI_LANG))
        pos = m.end()
    segs += _convert_vi_words(text[pos:], lang, vi_context)
    return segs or [(text, lang)]


async def text_to_speech(text: str, lang: str = "ja") -> str:
    segs = split_vietnamese(text, lang)
    if len(segs) == 1 and segs[0][1] == lang and segs[0][0] == text.strip():
        _vi_words = [m.group(0) for m in _VI_DIACRITIC_RE.finditer(text)]
        if _vi_words:  # co chu cai tieng Viet nhung khong doi duoc (khong co trong bang) -> frontend ja khong doc duoc
            console.log(f"TTS: tieng Viet khong co trong bang vi_syllable/vi_lexicon (se doc rat sai): {_vi_words}", "WARN")
        return await _tts_single(text, lang)
    if any(l == VI_LANG for _, l in segs):
        console.log(f"TTS tieng Viet -> {segs}", "INFO")
    parts = []
    for seg_text, seg_lang in segs:
        audio = await _tts_single(seg_text, seg_lang)
        if not audio:
            return ""
        parts.append(AudioSegment.from_file(BytesIO(audio)))
    if len(parts) == 1:
        return _export_wav(parts[0])
    merged = parts[0]
    for p in parts[1:]:
        merged += AudioSegment.silent(duration=60, frame_rate=merged.frame_rate) + p
    return _export_wav(merged)


def _export_wav(seg) -> bytes:
    buf = BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()


async def _tts_single(text: str, lang: str = "ja") -> str:
    
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


SYNTH_TIMEOUT_S = 3600  # /synth can be slow (first time each syllable is recorded); >= 10 min required
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
                _keys = ("Phrases", "Aligned", "Notes", "Unique", "Hit", "Miss", "Attempts", "TtsSec", "DspSec", "TotalSec", "Device")
                _st = "  ".join(f"{k}={response.headers.get('X-Synth-' + k)}" for k in _keys)
                console.log(f"Synth generated (Size: {len(audio)}) {_st}")
                return audio, response.headers.get("X-Synth-Transpose"), None
            err = (await response.text())[:500]
            console.log(f"Synth API ERROR:: {response.status} - {err}", "ERROR")
            return None, None, f"{response.status}: {err}"
    except asyncio.TimeoutError:
        return None, None, f"timed out after {timeout_s // 60} minutes"
    except Exception as e:
        console.log(f"Synth connection error: {e}", "ERROR")
        return None, None, f"connection error: {e}"
