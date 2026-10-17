"""Tu tai bo soundfont (xem soundfonts/SOURCES.md) khi thu muc soundfonts/ chua co soundfont nao.

Repo chi giu soundfonts/SOURCES.md (va INSTRUMENTS.md); cac file .sf2/.sf3 bi .gitignore vi qua nang cho GitHub.
Moi file duoc tai ve file .part roi doi ten (atomic) nen tai do dang bi ngat se khong de lai file hong.
"""
import os
import shutil
import tempfile
import threading
import zipfile

import requests

from console import console

SF_EXTS = (".sf2", ".sf3")

_OSUOSL = "https://ftp.osuosl.org/pub/musescore/soundfont"
_LINUXAUDIO = "https://download.linuxaudio.org/musical-instrument-libraries/sf2/fluidr3-splitted"
_BRATPEKI = "https://github.com/bratpeki/soundfonts/raw/HEAD/SF2"
_WRIGHTFLYER = "https://github.com/wrightflyer/SF2_SoundFonts/raw/HEAD"

# (ten file luu trong soundfonts/, url, True neu url la file .zip -> lay file .sf2 lon nhat trong zip)
MANIFEST: list[tuple[str, str, bool]] = [
    # bank GM day du (uu tien dung lam mac dinh nen tai truoc)
    ("GeneralUser-GS.sf2", "https://github.com/mrbumpy409/GeneralUser-GS/raw/main/GeneralUser-GS.sf2", False),
    ("MuseScore_General.sf3", f"{_OSUOSL}/MuseScore_General/MuseScore_General.sf3", False),
    ("TimGM6mb.sf2", f"{_WRIGHTFLYER}/TimGM6mb.sf2", False),
    ("FluidR3_GM.sf2", f"{_OSUOSL}/fluid-soundfont.zip", True),
    ("FluidR3Mono_GM.sf3", f"{_WRIGHTFLYER}/FluidR3Mono_GM.sf3", False),
    # soundfont chuyen cho tung nhom nhac cu (FluidR3 cat nho)
    *[(f"{name}_fluidr3.sf2", f"{_LINUXAUDIO}/fluidr3gm_{src}.sf2", False) for name, src in (
        ("piano", "piano"), ("chromatic_percussion", "chromaticpercussion"), ("organ", "organ"),
        ("guitar", "guitar"), ("bass", "bass"), ("ensemble", "ensemble"), ("brass", "brass"),
        ("reed", "reed"), ("pipe", "pipe"), ("ethnic", "ethnic"), ("percussive", "percussive"),
        ("drums", "drums"),
    )],
    ("drums_909.sf2", f"{_BRATPEKI}/DRUMS/909_drum_sf.sf2", False),
    # soundfont cong dong
    ("ChaosBank.sf2", f"{_BRATPEKI}/GM/ChaosBank.sf2", False),
    ("Jnsgm2.sf2", f"{_BRATPEKI}/GM/Jnsgm2.sf2", False),
    ("Masterpiece.sf2", f"{_BRATPEKI}/GM/Masterpiece.sf2", False),
    ("Unison.sf2", f"{_BRATPEKI}/GM/Unison.SF2", False),
    ("WeedsGM3.sf2", f"{_BRATPEKI}/GM/WeedsGM3.sf2", False),
    ("merlin_gmv32.sf2", f"{_WRIGHTFLYER}/merlin_gmv32.sf2", False),
    # lon nhat (~470 MB sau khi giai nen) -> tai cuoi cung
    ("orchestra_sso.sf2", f"{_OSUOSL}/Sonatina_Symphonic_Orchestra_SF2.zip", True),
]

# default.sf2 (dung boi utils/file_converter.py: MIDI -> wav) = ban sao cua soundfont nho nhat
DEFAULT_SF_SOURCE = "TimGM6mb.sf2"

_lock = threading.Lock()


def has_soundfonts(sf_dir: str) -> bool:
    try:
        return any(f.lower().endswith(SF_EXTS) for f in os.listdir(sf_dir))
    except FileNotFoundError:
        return False


def _download(url: str, dest: str) -> None:
    part = dest + ".part"
    try:
        with requests.get(url, stream=True, timeout=(15, 60), allow_redirects=True) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def _fetch_one(sf_dir: str, name: str, url: str, is_zip: bool) -> None:
    dest = os.path.join(sf_dir, name)
    if not is_zip:
        _download(url, dest)
        return
    with tempfile.TemporaryDirectory(dir=sf_dir) as tmp:
        zpath = os.path.join(tmp, "dl.zip")
        _download(url, zpath)
        with zipfile.ZipFile(zpath) as z:
            members = [i for i in z.infolist() if i.filename.lower().endswith(".sf2") and not i.is_dir()]
            if not members:
                raise RuntimeError("zip khong chua file .sf2")
            big = max(members, key=lambda i: i.file_size)
            part = dest + ".part"
            with z.open(big) as src, open(part, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            os.replace(part, dest)


def ensure_soundfonts(sf_dir: str) -> int:
    """Neu sf_dir chua co soundfont nao -> tai ca bo trong MANIFEST. Chay blocking (goi qua asyncio.to_thread). Tra ve so file tai duoc."""
    with _lock:
        if has_soundfonts(sf_dir):
            return 0
        os.makedirs(sf_dir, exist_ok=True)
        console.log(f"Khong co soundfont nao trong {sf_dir} -> tu tai {len(MANIFEST)} file (xem SOURCES.md), co the mat vai phut...", "WARN")
        ok = 0
        for name, url, is_zip in MANIFEST:
            if os.path.exists(os.path.join(sf_dir, name)):
                ok += 1
                continue
            try:
                console.log(f"Dang tai soundfont {name} ...", "INFO")
                _fetch_one(sf_dir, name, url, is_zip)
                ok += 1
            except Exception as e:
                console.log(f"Tai soundfont {name} that bai ({url}): {e}", "ERROR")
        default_path = os.path.join(sf_dir, "default.sf2")
        src = os.path.join(sf_dir, DEFAULT_SF_SOURCE)
        if not os.path.exists(default_path) and os.path.exists(src):
            try:
                shutil.copyfile(src, default_path)
            except OSError as e:
                console.log(f"Khong tao duoc default.sf2: {e}", "ERROR")
        console.log(f"Tai soundfont xong: {ok}/{len(MANIFEST)} file", "INFO")
        return ok
