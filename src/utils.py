"""
iPod Music Manager — Utility Functions
Constants, file scanning, size estimation, ffmpeg detection, drive detection, thumbnails.
"""

import os
import sys
import hashlib
import shutil
import subprocess
import ctypes
import string

__version__ = "1.0 (4G)"
__title__ = "iPod Music Manager"

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ipod_manager_config.json")

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav")
ALL_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav", ".flac", ".ogg", ".wma", ".aiff", ".aif", ".opus")
IPOD_COMPATIBLE = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav")


# ══════════════════════════════════════════════════════════════════════════════
#  FFMPEG DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def find_ffmpeg():
    """Locate ffmpeg executable. Returns path or None."""
    bundled = resource_path("ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled

    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if result.returncode == 0 or "ffmpeg version" in (result.stdout + result.stderr):
            return "ffmpeg"
    except Exception:
        pass
    # Check common locations on Windows
    common_paths = [
        os.path.join(os.environ.get("PROGRAMFILES", ""), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p
    return None



def scan_source_folder(source_path):
    """
    Scan a music source folder for audio files.
    Returns list of dicts: [{path, rel_path, folder, size, ext}, ...]
    """
    files = []
    if not os.path.isdir(source_path):
        return files

    def _scan(directory):
        try:
            for entry in os.scandir(directory):
                if entry.name.startswith('.'):
                    continue
                if entry.is_dir():
                    _scan(entry.path)
                elif entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in ALL_AUDIO_EXTENSIONS:
                        rel = os.path.relpath(entry.path, source_path)
                        folder_parts = rel.split(os.sep)
                        folder = folder_parts[0] if len(folder_parts) > 1 else None
                        try:
                            size = entry.stat().st_size
                        except OSError:
                            size = 0
                        files.append({
                            "path": entry.path,
                            "rel_path": rel,
                            "folder": folder,
                            "size": size,
                            "ext": ext,
                        })
        except OSError:
            pass

    _scan(source_path)
    return sorted(files, key=lambda x: x["path"].lower())


def estimate_transcoded_size(file_info, target_bitrate_kbps, target_format):
    """
    Estimate the output file size after transcoding.
    Uses heuristic: output_size ≈ (bitrate_kbps / 8) * duration_seconds
    Duration estimated from source: duration ≈ file_size / (source_bitrate / 8)
    For unknown source bitrate, assume ~192kbps for compressed, raw calc for wav/flac.
    """
    ext = file_info["ext"]
    size = file_info["size"]

    # Estimate duration from file size
    if ext in (".wav", ".aiff", ".aif"):
        # WAV: ~1411 kbps (CD quality 16-bit 44.1kHz stereo)
        duration_s = size / (1411 * 1000 / 8) if size > 0 else 0
    elif ext in (".flac",):
        # FLAC: roughly 50-70% of WAV, assume ~800 kbps average
        duration_s = size / (800 * 1000 / 8) if size > 0 else 0
    else:
        # Compressed formats (mp3, m4a, ogg, etc.): assume ~192 kbps average
        duration_s = size / (192 * 1000 / 8) if size > 0 else 0

    # Estimated output size at target bitrate
    estimated = int((target_bitrate_kbps * 1000 / 8) * duration_s)
    return max(estimated, 1024)  # minimum 1KB


def get_disk_usage(path):
    """Get total, used, free bytes for the drive containing path."""
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free
    except Exception:
        return 0, 0, 0


def format_size(bytes_val):
    """Human-readable file size."""
    sign = "-" if bytes_val < 0 else ""
    bytes_val = abs(bytes_val)
    if bytes_val < 1024:
        return f"{sign}{int(bytes_val)} B"
    elif bytes_val < 1024 * 1024:
        return f"{sign}{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{sign}{bytes_val / (1024*1024):.1f} MB"
    else:
        return f"{sign}{bytes_val / (1024*1024*1024):.2f} GB"


# ══════════════════════════════════════════════════════════════════════════════
#  THUMBNAILS
# ══════════════════════════════════════════════════════════════════════════════

def extract_thumbnail_ppm(filepath, ffmpeg_path, size=24):
    """
    Extract embedded album art into PPM format which tk.PhotoImage can read directly.
    Returns bytes or None if extraction fails.
    """
    if not ffmpeg_path:
        return None
    try:
        cmd = [
            ffmpeg_path, '-y', '-i', filepath,
            '-an', '-vcodec', 'ppm', '-vframes', '1',
            '-s', f'{size}x{size}', '-f', 'image2pipe', '-'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        out, _ = proc.communicate(timeout=2)
        if proc.returncode == 0 and out.startswith(b'P6'):
            return out
    except Exception:
        pass
    return None


def scan_ipod_existing(ipod_path):
    """Scan iPod_Control/Music/ for files already present. Returns dict of {basename_lower: ipod_rel_path}."""
    existing = {}
    music_dir = os.path.join(ipod_path, "iPod_Control", "Music")
    if not os.path.isdir(music_dir):
        return existing
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in files:
            if fname.startswith('.'):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, ipod_path)
                ipod_rel = "/" + rel.replace("\\", "/")
                # Key by basename (without ext) + folder for matching
                rel_to_music = os.path.relpath(root, music_dir)
                key = (rel_to_music.lower(), os.path.splitext(fname)[0].lower())
                existing[key] = ipod_rel
    return existing


def get_ipod_safe_key(folder, basename):
    """Generate the matching key used for existing files based on non-ASCII rules."""
    def is_ascii(s):
        return all(ord(c) < 128 for c in s)
    f_safe = folder if folder else "_root"
    if not is_ascii(f_safe):
        f_safe = "F_" + hashlib.md5(f_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
    b_safe = basename
    if not is_ascii(b_safe):
        b_safe = "T_" + hashlib.md5(b_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
    return (f_safe.lower(), b_safe.lower())


# ══════════════════════════════════════════════════════════════════════════════
#  DRIVE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_ipod_drives():
    """Detect drives with iPod_Control folder."""
    drives = []
    if sys.platform == "win32":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                try:
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                    if drive_type in (2, 3):
                        ipod_ctrl = os.path.join(drive_path, "iPod_Control")
                        if os.path.isdir(ipod_ctrl):
                            buf = ctypes.create_unicode_buffer(256)
                            ctypes.windll.kernel32.GetVolumeInformationW(
                                drive_path, buf, 256, None, None, None, None, 0
                            )
                            label = buf.value or "iPod"
                            drives.append((drive_path, f"{letter}: ({label})"))
                except Exception:
                    pass
            bitmask >>= 1
    return drives

