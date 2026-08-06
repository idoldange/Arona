import os
import subprocess
import tempfile
import asyncio
from PIL import Image 
import zipfile
import tarfile
import gzip
import struct
from io import BytesIO
import hashlib
from typing import Optional, Dict, Tuple
from console import console
from pathlib import Path
import trimesh
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance
import io
import uuid
import mido
import json
import xml.etree.ElementTree as ET
import re
import magic
import torch

async def model3d_to_image(model_data: bytes, output_format: str = 'PNG') -> bytes:
    """Convert 3D model to multi-view image with UUID-based temp files for thread safety"""
    
    # Use UUID to ensure unique temp files when running async
    
    #mimetype to ext
    ext_map = {
        'model/obj': '.obj',
        'model/fbx': '.fbx',
        'model/gltf': '.gltf',
        'model/glb': '.glb',
        'model/dae': '.dae',
        'model/3ds': '.3ds',
        'model/stl': '.stl',
        'model/blend': '.blend'
    }
    mime = magic.from_buffer(model_data, mime=True)
    ext = ext_map.get(mime, '.obj')

    unique_id = str(uuid.uuid4())
    tmp_model_path = os.path.join(tempfile.gettempdir(), f'model3d_{unique_id}{ext}')
    
    try:
        # Write model data to temp file
        with open(tmp_model_path, 'wb') as f:
            f.write(model_data)
        
        mesh = trimesh.load(tmp_model_path)
        
        # Giữ nguyên texture gốc - không ghi đè với màu xám
        # mesh.visual.face_colors = [100, 100, 100, 255]
        
        # Tính toán khoảng cách camera để lấy toàn bộ model
        bounding_radius = mesh.bounding_sphere.radius
        fov_degrees = 60
        fov_radians = np.radians(fov_degrees)
        # Tính khoảng cách từ tâm để model vừa khít trong view
        camera_distance = (bounding_radius * 2.5) / np.tan(fov_radians / 2)
        
        # 6 hình ảnh thay vì 4
        views = [
            {'angle': 0, 'axis': [0,1,0], 'name': 'Front'},
            {'angle': np.pi/2, 'axis': [0,1,0], 'name': 'Right'},
            {'angle': np.pi, 'axis': [0,1,0], 'name': 'Back'},
            {'angle': -np.pi/2, 'axis': [0,1,0], 'name': 'Left'},
            {'angle': np.pi/4, 'axis': [1,1,1], 'name': 'Isometric'}, 
            {'angle': np.pi/6, 'axis': [0,1,0], 'name': 'Top'}
        ]
        
        renders = []
        scene = trimesh.Scene(mesh)
        
        # Setup lighting - ambient light
        if hasattr(scene, 'ambient'):
            scene.ambient = [0.6, 0.6, 0.6]
        
        for view in views:
            rot = trimesh.transformations.rotation_matrix(
                angle=view['angle'],
                direction=view['axis'], 
                point=mesh.centroid
            )
            scene.camera_transform = rot
            
            scene.camera.resolution = [512, 512]
            scene.camera.fov = [fov_degrees, fov_degrees]
            
            # Áp dụng khoảng cách camera được tính toán
            scene.camera_transform = np.dot(
                scene.camera_transform,
                trimesh.transformations.translation_matrix(
                    [0, 0, camera_distance]
                )
            )
            
            solid = scene.save_image(resolution=[512,512], visible=True)
            
            wire = scene.save_image(resolution=[512,512], visible=True, wireframe=True)
            
            solid_img = Image.open(io.BytesIO(solid)).convert('RGBA')
            wire_img = Image.open(io.BytesIO(wire)).convert('RGBA')
            
            enhancer = ImageEnhance.Contrast(wire_img)
            wire_img = enhancer.enhance(2.0)
            
            result = Image.blend(solid_img, wire_img, 0.3)
            
            draw = ImageDraw.Draw(result)
            draw.text((10, 10), view['name'], fill=(255,255,255))
            
            renders.append(result)
        
        # Lưới 2x3 cho 6 hình ảnh
        final_img = Image.new('RGB', (1536, 1024))
        final_img.paste(renders[0], (0,0))       # Front
        final_img.paste(renders[1], (512,0))     # Right
        final_img.paste(renders[2], (1024,0))    # Back
        final_img.paste(renders[3], (0,512))     # Left
        final_img.paste(renders[4], (512,512))   # Isometric
        final_img.paste(renders[5], (1024,512))  # Top
        
        out_bytes = io.BytesIO() 
        final_img.save(out_bytes, format=output_format)
        return out_bytes.getvalue()
        
    finally:
        # Ensure cleanup
        if os.path.exists(tmp_model_path):
            try:
                os.unlink(tmp_model_path)
            except OSError as e:
                console.log(f"Warning: Failed to delete temp model file {tmp_model_path}: {e}", "WARN")

async def extract_archive(archive_data: bytes, target_dir: str = None) -> list[str]:
    """Extract archive files with UUID-based temp directory for async safety"""

    if target_dir is None:
        # Use UUID for unique directory name when running async
        unique_id = str(uuid.uuid4())
        target_dir = os.path.join(tempfile.gettempdir(), f'archive_{unique_id}')
        os.makedirs(target_dir, exist_ok=True)
    
    target_path = Path(target_dir)
    extracted_files = []
        
    if archive_data.startswith(b'PK\x03\x04'): 
        with zipfile.ZipFile(BytesIO(archive_data)) as zf:
            for name in zf.namelist():
                try:
                    out_path = (target_path / name).resolve()
                    if not str(out_path).startswith(str(target_path)):
                        continue
                    zf.extract(name, target_dir)
                    if os.path.isfile(out_path):
                        extracted_files.append(name)
                except (zipfile.BadZipFile, OSError) as e:
                    console.log(f"Error extracting zip member {name}: {e}", "WARN")
                    continue
            return extracted_files
            
    elif archive_data.startswith(b'\x1f\x8b'):  
        try:
            with gzip.GzipFile(fileobj=BytesIO(archive_data)) as gz:
                content = gz.read()
                out_path = target_path / f'extracted_{str(uuid.uuid4())}'
                out_path.write_bytes(content)
                return [str(out_path.relative_to(target_path))]
        except OSError as e:
            console.log(f"Error extracting gzip archive: {e}", "WARN")
            return []
            
    elif archive_data.startswith(b'ustar'): 
        try:
            with tarfile.open(fileobj=BytesIO(archive_data)) as tf:
                for member in tf.getmembers():
                    try:
                        if not member.isfile():
                            continue
                            
                        out_path = (target_path / member.name).resolve()
                        if not str(out_path).startswith(str(target_path)):
                            continue
                            
                        tf.extract(member, target_dir)
                        extracted_files.append(member.name)
                    except Exception as e:
                        console.log(f"Error extracting tar member {member.name}: {e}", "WARN")
                        continue
                return extracted_files
        except Exception as e:
            console.log(f"Error opening tar archive: {e}", "WARN")
            return []
            
    return [] 

CACHE_MAX_SIZE = 128
_CACHE: Dict[bytes, bytes] = {}
_CACHE_LOCK = asyncio.Lock()
_CACHE_ORDER = [] 

def _get_cache_key(midi_data: bytes, soundfont_path: str, sample_rate: int) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(midi_data)
    hasher.update(soundfont_path.encode('utf-8'))
    hasher.update(str(sample_rate).encode('utf-8'))
    return hasher.digest()

def _update_lru_order(key: bytes):
    if key in _CACHE_ORDER:
        _CACHE_ORDER.remove(key)
    _CACHE_ORDER.insert(0, key)

def _trim_cache():
    while len(_CACHE_ORDER) > CACHE_MAX_SIZE:
        lru_key = _CACHE_ORDER.pop()
        if lru_key in _CACHE:
            del _CACHE[lru_key]

async def midi_to_wav(
    midi_data: bytes, 
    soundfont_path: str="soundfonts/default.sf2",
    sample_rate: int = 44100
) -> Optional[bytes]:
    """Convert MIDI to WAV with UUID-based temp files for async safety"""
    
    cache_key = _get_cache_key(midi_data, soundfont_path, sample_rate)

    async with _CACHE_LOCK:
        if cache_key in _CACHE:
            _update_lru_order(cache_key)
            return _CACHE[cache_key]

    if not os.path.exists(soundfont_path):
        console.log(f"Lỗi: Không tìm thấy SoundFont tại đường dẫn: {soundfont_path}", "ERROR")
        return None

    # Use UUID for unique temp files
    unique_id = str(uuid.uuid4())
    tmp_mid_path = os.path.join(tempfile.gettempdir(), f'midi_{unique_id}.mid')
    tmp_wav_path = os.path.join(tempfile.gettempdir(), f'midi_{unique_id}.wav')
    wav_bytes = None

    try:
        # Write MIDI data to temp file
        with open(tmp_mid_path, 'wb') as f:
            f.write(midi_data)
        
        command = [
            './fluidsynth/bin/fluidsynth', '-ni', soundfont_path, tmp_mid_path, 
            '-F', tmp_wav_path, '-r', str(sample_rate)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            console.log(f"Lỗi khi chạy FluidSynth. Mã thoát: {process.returncode}", "ERROR")
            if stderr:
                 console.log(f"FluidSynth Error Output:\n{stderr.decode()}", "ERROR")
            return None
        
        with open(tmp_wav_path, 'rb') as f:
            wav_bytes = f.read()
            
    except FileNotFoundError:
        console.log("Lỗi: Không tìm thấy lệnh 'fluidsynth'.", "ERROR")
        return None
    except Exception as e:
        console.log(f"Đã xảy ra lỗi trong quá trình chuyển đổi: {e}", "ERROR")
        return None
        
    finally:
        # Ensure cleanup with error handling
        for path in [tmp_mid_path, tmp_wav_path]:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError as e:
                    console.log(f"Warning: Failed to delete temp file {path}: {e}", "WARN")
            
    if wav_bytes:
        async with _CACHE_LOCK:
            _CACHE[cache_key] = wav_bytes
            _update_lru_order(cache_key)
            _trim_cache()
            
    return wav_bytes

async def raw_to_wav(raw_data: bytes, sample_rate: int = 24000) -> bytes:
    """Convert raw PCM to WAV with UUID-based temp files for async safety"""
    
    # Use UUID for unique temp files
    unique_id = str(uuid.uuid4())
    tmp_raw_path = os.path.join(tempfile.gettempdir(), f'raw_{unique_id}.raw')
    tmp_wav_path = os.path.join(tempfile.gettempdir(), f'raw_{unique_id}.wav')
    
    try:
        # Write raw data to temp file
        with open(tmp_raw_path, 'wb') as f:
            f.write(raw_data)
        
        command = [
            'ffmpeg', '-f', 's16le', '-ar', str(sample_rate), '-ac', '1',
            '-i', tmp_raw_path,
            '-ar', str(sample_rate),
            '-ac', '1',
            tmp_wav_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            console.log(f"Lỗi khi chạy ffmpeg. Mã thoát: {process.returncode}", "ERROR")
            if stderr:
                console.log(f"ffmpeg Error Output:\n{stderr.decode()}", "ERROR")
            return b''
        
        with open(tmp_wav_path,  'rb') as f:
            wav_bytes = f.read()
            
        return wav_bytes
            
    except FileNotFoundError:
        console.log("Lỗi: Không tìm thấy lệnh 'ffmpeg'.", "ERROR")
        return b''
    except Exception as e:
        console.log(f"Đã xảy ra lỗi trong quá trình chuyển đổi raw to wav: {e}", "ERROR")
        return b''
        
    finally:
        # Ensure cleanup with error handling
        for path in [tmp_raw_path, tmp_wav_path]:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError as e:
                    console.log(f"Warning: Failed to delete temp file {path}: {e}", "WARN")

async def gif_to_png(gif_data: bytes) -> bytes:
    """Convert GIF image to PNG format"""
    try:
        gif_image = Image.open(BytesIO(gif_data))
        
        # Convert to RGB if necessary (to remove alpha channel if needed)
        if gif_image.mode in ('RGBA', 'LA', 'P'):
            # Create white background
            background = Image.new('RGB', gif_image.size, (255, 255, 255))
            # Convert GIF to RGBA first if in palette mode
            if gif_image.mode == 'P':
                gif_image = gif_image.convert('RGBA')
            background.paste(gif_image, mask=gif_image.split()[-1] if gif_image.mode == 'RGBA' else None)
            gif_image = background
        elif gif_image.mode != 'RGB':
            gif_image = gif_image.convert('RGB')
        
        png_buffer = BytesIO()
        gif_image.save(png_buffer, format='PNG')
        return png_buffer.getvalue()
    except Exception as e:
        console.log(f"Error converting GIF to PNG: {e}", "ERROR")
        raise

async def midi_to_json(midi_data: bytes) -> str:
    try:
        midi_file = mido.MidiFile(file=BytesIO(midi_data))
        ticks_per_beat = midi_file.ticks_per_beat
        abc_header = "X:1\nT:MIDI Export\nM:4/4\nL:1/4\nQ:1/4=120\nK:C\n"
        abc_body = ""
        note_map = ['C', '^C', 'D', '^D', 'E', 'F', '^F', 'G', '^G', 'A', '^A', 'B']
        octave_map = {0: ",,,", 1: ",,", 2: ",", 3: "", 4: "'", 5: "''", 6: "'''"}
        for i, track in enumerate(midi_file.tracks):
            active_notes = {}
            sequence = []
            current_tick = 0
            for msg in track:
                current_tick += msg.time
                if msg.type == 'note_on' and msg.velocity > 0:
                    active_notes[msg.note] = {"start": current_tick}
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active_notes:
                        note_info = active_notes.pop(msg.note)
                        duration = current_tick - note_info["start"]
                        # Tính độ dài nốt theo ticks_per_beat
                        length = max(1, round((duration / ticks_per_beat) * 4))
                        # Chuyển sang ABC notation
                        midi_note = msg.note
                        note_name = note_map[midi_note % 12]
                        octave = (midi_note // 12) - 1
                        abc_note = note_name
                        # ABC octave: C,,, (low) đến C''' (high)
                        if octave in octave_map:
                            abc_note += octave_map[octave]
                        else:
                            abc_note += ""
                        abc_note += str(length)
                        sequence.append(abc_note)
            if sequence:
                abc_body += " ".join(sequence) + "\n"
        if not abc_body:
            return "No notes found"
        return abc_header + abc_body
    except Exception as e:
        return f"Error: {str(e)}"
    
async def voicebank_to_json(file_bytes: bytes, file_type: str = "ust") -> str:
    
    note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    compact_data = []

    try:
        if file_type.lower() == "ust":
            content = ""
            for enc in ['shift-jis', 'utf-8-sig', 'utf-8', 'cp1252']:
                try:
                    content = file_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            
            if not content:
                return json.dumps({"error": "Unknown Encoding: Can't decode UST file"})

            sections = re.findall(r'\[#(\d+)\]\r?\n(.*?)(?=\[#|\z)', content, re.DOTALL)
            
            for _, section in sections:
                lyric_match = re.search(r'Lyric=(.*)', section)
                note_num_match = re.search(r'NoteNum=(\d+)', section)
                length_match = re.search(r'Length=(\d+)', section)
                
                if lyric_match and note_num_match and length_match:
                    lyric = lyric_match.group(1).strip()
                    if lyric.upper() == "R" or not lyric: 
                        continue 
                    
                    n_num = int(note_num_match.group(1))
                    length = int(length_match.group(1))
                    
                    full_note = f"{note_names[n_num % 12]}{(n_num // 12) - 1}"
                    
                    duration = max(1, round(length / 120))
                    
                    compact_data.append({"n": full_note, "t": lyric, "d": duration})

        elif file_type.lower() == "vsqx":
            try:
                root = ET.fromstring(file_bytes)
            except ET.ParseError:
                return json.dumps({"error": "Invalid XML/VSQX format"})

            ns_url = root.tag.split('}')[0].strip('{') if '}' in root.tag else ''
            ns = {'v': ns_url} if ns_url else {}

            xpath = './/v:note' if ns else './/note'
            
            for note in root.findall(xpath, ns):
                y_tag = 'v:y' if ns else 'y'       # Lyric
                n_tag = 'v:n' if ns else 'n'       # Note Number
                dur_tag = 'v:dur' if ns else 'dur' # Duration
                
                lyric_elem = note.find(y_tag, ns)
                n_num_elem = note.find(n_tag, ns)
                dur_elem = note.find(dur_tag, ns)
                
                if lyric_elem is not None and n_num_elem is not None:
                    lyric = lyric_elem.text.strip()
                    if lyric.upper() == "R" or not lyric: 
                        continue
                        
                    n_num = int(n_num_elem.text)
                    dur_val = int(dur_elem.text) if dur_elem is not None else 480
                    
                    full_note = f"{note_names[n_num % 12]}{(n_num // 12) - 1}"
                    duration = max(1, round(dur_val / 120))
                    
                    compact_data.append({"n": full_note, "t": lyric, "d": duration})

        else:
            return json.dumps({"error": f"Unsupported file type: {file_type}"})

        return json.dumps(compact_data, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"Internal Error: {str(e)}"})
    
async def audio_to_wav(file_data: bytes, src_ext: str = '.m4a') -> bytes:
    """
    Convert bất kỳ audio container nào sang WAV bằng ffmpeg.

    Cần thiết cho các format dùng container video (M4A/M4B/M4R = MPEG-4,
    WebM audio, OGG audio đôi khi bị misdetect, v.v.) mà python-magic hoặc
    Discord báo là video/* → Gemini reject "0 frames found".

    src_ext: extension gốc của file (ví dụ '.m4a', '.webm', '.ogg')
             để ffmpeg nhận diện format input đúng.
    """
    if not src_ext.startswith('.'):
        src_ext = f'.{src_ext}'
    unique_id = str(uuid.uuid4())
    tmp_in  = os.path.join(tempfile.gettempdir(), f'audio_in_{unique_id}{src_ext}')
    tmp_out = os.path.join(tempfile.gettempdir(), f'audio_out_{unique_id}.wav')
    try:
        with open(tmp_in, 'wb') as f:
            f.write(file_data)

        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', tmp_in,
            '-vn',           # drop video stream (nếu có)
            '-ar', '44100',  # sample rate
            '-ac', '2',      # stereo
            tmp_out,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg exit {proc.returncode}: {stderr.decode(errors='replace')}")

        with open(tmp_out, 'rb') as f:
            return f.read()

    finally:
        for p in (tmp_in, tmp_out):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


async def convert_model_to_text_bytes(input_bytes: bytes) -> bytes:
    try:
        
        buffer = io.BytesIO(input_bytes)
        data = torch.load(buffer, map_location='cpu', weights_only=True)
        
        console.log(f"Analyzing model...", "INFO")

        def build_struct(obj, indent=0):
            sp = "    " * indent
            if isinstance(obj, dict):
                res = f"dict (len={len(obj)}) {{\n"
                for k, v in obj.items():
                    res += f"{sp}    '{k}': {build_struct(v, indent + 1)}\n"
                res += f"{sp}}}"
                return res
            elif isinstance(obj, list):
                res = f"list (len={len(obj)}) {{\n"
                for i, v in enumerate(obj):
                    res += f"{sp}    [{i}]: {build_struct(v, indent + 1)}\n"
                res += f"{sp}}}"
                return res
            elif torch.is_tensor(obj):
                if obj.dim() == 0:
                    return f"tensor (shape=(), dtype={obj.dtype}, device={obj.device}) {obj.item()}"
                return f"tensor (shape={tuple(obj.shape)}, dtype={obj.dtype}, device={obj.device})"
            else:
                return str(obj)

        text_result = build_struct(data)
        
        return text_result.encode('utf-8')

    except Exception as e:
        error_msg = f"Lỗi converter: {str(e)}"
        console.log(error_msg, "ERROR")
        return error_msg.encode('utf-8')
    
def convert_npz_to_text_bytes(input_bytes: bytes) -> bytes:
    try:
        
        buffer = io.BytesIO(input_bytes)
        data = np.load(buffer, allow_pickle=True)
        
        files = data.files 
        res = f"dict (len={len(files)}) {{\n"
        
        for name in files:
            array = data[name]
            res += f"    '{name}': numpy_array (shape={array.shape}, dtype={array.dtype})\n"
            
            if array.size <= 5:
                res += f"        value: {array.tolist()}\n"
        
        res += "}"
        
        return res.encode('utf-8')

    except Exception as e:
        error_msg = f"Lỗi đọc NPZ: {str(e)}"
        console.log(error_msg, "ERROR")
        return error_msg.encode('utf-8')

async def numpy_to_text(file_data: bytes, filename: str = "") -> bytes:
    """Async wrapper for convert_npz_to_text_bytes. Handles both .npy and .npz."""
    import numpy as np
    from io import BytesIO
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext == ".npy":
            arr = np.load(BytesIO(file_data), allow_pickle=True)
            result = f"numpy_array (shape={arr.shape}, dtype={arr.dtype})"
            if arr.size <= 20:
                result += f"\nvalues: {arr.tolist()}"
            return result.encode("utf-8")
        # .npz or unknown — use existing converter
        return convert_npz_to_text_bytes(file_data)
    except Exception as e:
        return f"Error reading numpy file {filename}: {e}".encode("utf-8")