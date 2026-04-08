"""
Antigravity iPod Manager v1.0 (4G)
Full Sync Manager with Transcoding, VoiceOver & Space Management

=== DATABASE ARCHITECTURE (4th Gen iPod Shuffle - Little Endian) ===

The iTunesSD file is structured as a sequence of binary segments:

  [bdhs - TunesSD Header]  64 bytes fixed
  [hths - Track Header]    20 bytes + (num_tracks * 4) pointer bytes
  [rths - Track 0..N]      0x174 (372) bytes each
  [hphs - Playlist Header] 20 bytes + (num_playlists * 4) pointer bytes
  [lphs - Playlist 0..N]   44 bytes + (track_count * 4) index bytes

All multi-byte integers are Little-Endian (<I format in struct).

=== SYNC WORKFLOW ===

1. User selects iPod drive + external music source folder
2. Space calculator estimates output size based on bitrate/format settings
3. Safety check: estimated size must fit in iPod free space
4. Sync clears iPod_Control/Music/, copies/transcodes files, rebuilds iTunesSD
5. Subfolder structure in source -> named playlists on iPod
"""

import os
import sys
import struct
import collections
import hashlib
import threading
import queue
import shutil
import subprocess
import json
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ctypes
import string
import time
import concurrent.futures

__version__ = "1.0 (4G)"
__title__ = "Antigravity"

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".antigravity_config.json")


# ── Tooltip Widget ───────────────────────────────────────────────────────

class ToolTip:
    """Lightweight hover tooltip for any tkinter widget."""
    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._tip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self):
        if self._tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         bg="#1c2128", fg="#c9d1d9", relief="solid", borderwidth=1,
                         font=("Segoe UI Variable Display", 11), padx=8, pady=4)
        label.pack()

    def _hide(self):
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav")
ALL_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav", ".flac", ".ogg", ".wma", ".aiff", ".aif", ".opus")
IPOD_COMPATIBLE = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav")


# ══════════════════════════════════════════════════════════════════════════════
#  FFMPEG DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def find_ffmpeg():
    """Locate ffmpeg executable. Returns path or None."""
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


# ══════════════════════════════════════════════════════════════════════════════
#  CORE DATABASE STRUCTS (preserved from Part 1/2)
# ══════════════════════════════════════════════════════════════════════════════

class Record(object):
    """Base class: compiles ordered field definitions into packed Little-Endian byte sequences."""
    def __init__(self):
        self._struct = collections.OrderedDict()
        self._fields = {}

    def __getitem__(self, item):
        if item not in self._struct:
            raise KeyError(item)
        return self._fields.get(item, self._struct[item][1])

    def __setitem__(self, item, value):
        self._fields[item] = value

    def construct(self):
        output = b""
        for key in self._struct:
            fmt, default = self._struct[key]
            val = self._fields.get(key, default)
            output += struct.pack("<" + fmt, val)
        return output


class TunesSD(Record):
    """bdhs: Base Database Header (64 bytes)."""
    def __init__(self, num_tracks, num_playlists):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",                    ("4s", b"bdhs")),
            ("unknown1",                     ("I",  0x02000003)),
            ("total_length",                 ("I",  64)),
            ("total_number_of_tracks",       ("I",  num_tracks)),
            ("total_number_of_playlists",    ("I",  num_playlists)),
            ("unknown2",                     ("Q",  0)),
            ("max_volume",                   ("B",  0)),
            ("voiceover_enabled",            ("B",  0)),
            ("unknown3",                     ("H",  0)),
            ("total_tracks_without_podcasts", ("I", num_tracks)),
            ("track_header_offset",          ("I",  64)),
            ("playlist_header_offset",       ("I",  0)),
            ("unknown4",                     ("20s", b"\x00" * 20)),
        ])


class TrackHeader(Record):
    """hths: Track Header. Size = 20 + (num_tracks * 4) bytes."""
    def __init__(self, num_tracks):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",        ("4s", b"hths")),
            ("total_length",     ("I",  20 + num_tracks * 4)),
            ("number_of_tracks", ("I",  num_tracks)),
            ("unknown1",         ("Q",  0)),
        ])


class Track(Record):
    """rths: Individual track record (0x174 = 372 bytes)."""
    def __init__(self):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",       ("4s", b"rths")),
            ("header_length",   ("I",  0x174)),
            ("start_at_pos_ms", ("I",  0)),
            ("stop_at_pos_ms",  ("I",  0)),
            ("volume_gain",     ("I",  0)),
            ("filetype",        ("I",  1)),
            ("filename",        ("256s", b"\x00" * 256)),
            ("bookmark",        ("I",  0)),
            ("dontskip",        ("B",  1)),
            ("remember",        ("B",  0)),
            ("unintalbum",      ("B",  0)),
            ("unknown",         ("B",  0)),
            ("pregap",          ("I",  0x200)),
            ("postgap",         ("I",  0x200)),
            ("numsamples",      ("I",  0)),
            ("unknown2",        ("I",  0)),
            ("gapless",         ("I",  0)),
            ("unknown3",        ("I",  0)),
            ("albumid",         ("I",  0)),
            ("track",           ("H",  1)),
            ("disc",            ("H",  0)),
            ("unknown4",        ("Q",  0)),
            ("dbid",            ("8s", b"\x00" * 8)),
            ("artistid",        ("I",  0)),
            ("unknown5",        ("32s", b"\x00" * 32)),
        ])

    def populate(self, ipod_path, display_name=None):
        """Set track metadata from an iPod-relative path like /iPod_Control/Music/F00/song.mp3"""
        self["filename"] = ipod_path.encode('utf-8')
        ext = os.path.splitext(ipod_path)[1].lower()
        if ext in (".m4a", ".m4b", ".m4p", ".aa"):
            self["filetype"] = 2
            if ext in (".m4b", ".aa"):
                self["dontskip"] = 0
                self["bookmark"] = 1
                self["remember"] = 1
        
        if display_name is None:
            display_name = os.path.splitext(os.path.basename(ipod_path))[0]
        self["dbid"] = hashlib.md5(display_name.encode('utf-8', 'ignore')).digest()[:8]


class PlaylistHeader(Record):
    """hphs: Playlist Header. Size = 20 + (num_playlists * 4) bytes."""
    def __init__(self, num_playlists):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",                     ("4s", b"hphs")),
            ("total_length",                  ("I",  0x14 + num_playlists * 4)),
            ("number_of_playlists",           ("I",  num_playlists)),
            ("number_of_non_podcast_lists",   ("2s", b"\xFF\xFF")),
            ("number_of_master_lists",        ("2s", b"\x01\x00")),
            ("number_of_non_audiobook_lists", ("2s", b"\xFF\xFF")),
            ("unknown2",                      ("2s", b"\x00" * 2)),
        ])


class Playlist(Record):
    """lphs: Playlist record. 44 bytes + (num_tracks * 4) index bytes."""
    def __init__(self, num_tracks, listtype=1, dbid=None):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",        ("4s", b"lphs")),
            ("total_length",     ("I",  44 + 4 * num_tracks)),
            ("number_of_songs",  ("I",  num_tracks)),
            ("number_of_nonaudio", ("I", num_tracks)),
            ("dbid",             ("8s", dbid if dbid else b"\x00" * 8)),
            ("listtype",         ("I",  listtype)),
            ("unknown1",         ("16s", b"\x00" * 16)),
        ])


# ══════════════════════════════════════════════════════════════════════════════
#  SPACE CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

def scan_source_folder(source_path):
    """
    Scan a music source folder for audio files.
    Returns list of dicts: [{path, rel_path, folder, size, ext}, ...]
    """
    files = []
    if not os.path.isdir(source_path):
        return files

    for root, dirs, filenames in os.walk(source_path):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith('.')]
        for fname in sorted(filenames, key=lambda x: x.lower()):
            if fname.startswith('.'):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in ALL_AUDIO_EXTENSIONS:
                full = os.path.join(root, fname)
                rel = os.path.relpath(root, source_path)
                folder = rel.split(os.sep)[0] if rel != "." else None
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                files.append({
                    "path": full,
                    "rel_path": os.path.relpath(full, source_path),
                    "folder": folder,
                    "size": size,
                    "ext": ext,
                })
    return files


def estimate_transcoded_size(file_info, target_bitrate_kbps, target_format, convert_all):
    """
    Estimate the output file size after transcoding.
    If convert_all is False and file is already compatible, use original size.
    Uses heuristic: output_size ≈ (bitrate_kbps / 8) * duration_seconds
    Duration estimated from source: duration ≈ file_size / (source_bitrate / 8)
    For unknown source bitrate, assume ~192kbps for compressed, raw calc for wav/flac.
    """
    ext = file_info["ext"]
    size = file_info["size"]

    # If not converting compatible files, keep original size
    if not convert_all and ext in IPOD_COMPATIBLE:
        return size

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
#  VOICEOVER GENERATION (Windows SAPI via PowerShell)
# ══════════════════════════════════════════════════════════════════════════════

def dbid_to_hex_filename(dbid_bytes):
    """Convert 8-byte dbid to the hex filename format used by iPod Speakable."""
    return ''.join(format(b, '02X') for b in reversed(dbid_bytes))


def generate_voiceover_wav(out_path, text, ffmpeg_path=None):
    """Generate a spoken WAV file using gTTS (if online) or Windows SAPI via PowerShell."""
    if os.path.isfile(out_path):
        return True  # Already exists

    def _fallback_sapi():
        safe_text = text.replace("'", "''").replace('"', '`"')
        ps_script = (
            f"Add-Type -AssemblyName System.Speech; "
            f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile('{out_path.replace(chr(39), chr(39)+chr(39))}'); "
            f"$s.Speak('{safe_text}'); "
            f"$s.Dispose()"
        )
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                                    capture_output=True, text=True, timeout=15, creationflags=creationflags)
            return result.returncode == 0 and os.path.isfile(out_path)
        except Exception:
            return False

    success = False
    try:
        from gtts import gTTS  # type: ignore
        lang = 'en'
        for c in text:
            if '\u3040' <= c <= '\u30ff': lang = 'ja'; break
            if '\uac00' <= c <= '\ud7a3': lang = 'ko'; break
            if '\u4e00' <= c <= '\u9fff': lang = 'zh-CN'; break
            
        mp3_path = out_path + ".mp3"
        tts = gTTS(text=text, lang=lang)
        tts.save(mp3_path)
        
        if ffmpeg_path and os.path.isfile(mp3_path):
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            res = subprocess.run([ffmpeg_path, "-y", "-i", mp3_path, "-ac", "1", "-ar", "16000", out_path],
                                 capture_output=True, timeout=10, creationflags=creationflags)
            os.remove(mp3_path)
            if os.path.isfile(out_path):
                success = True
    except Exception:
        pass

    if not success:
        return _fallback_sapi()
    return True


def generate_silent_wav(out_path):
    """Generate a minimal silent WAV file as fallback."""
    if os.path.isfile(out_path):
        return
    # Minimal WAV: 44 byte header + 8000 samples of silence (0.5s at 16kHz mono)
    import wave
    try:
        with wave.open(out_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b'\x00\x00' * 8000)
    except Exception:
        pass


def build_voiceover(ipod_path, track_dbids, playlist_dbids, ffmpeg_path=None, log_cb=None):
    """
    Generate VoiceOver .wav files for tracks and playlists.
    track_dbids: dict of {ipod_rel_path: (dbid_bytes, display_name)}
    playlist_dbids: dict of {folder_name: dbid_bytes}
    Returns estimated total size in bytes.
    """
    def log(msg):
        if log_cb:
            log_cb(msg)

    speak_base = os.path.join(ipod_path, "iPod_Control", "Speakable")
    tracks_dir = os.path.join(speak_base, "Tracks")
    playlists_dir = os.path.join(speak_base, "Playlists")
    os.makedirs(tracks_dir, exist_ok=True)
    os.makedirs(playlists_dir, exist_ok=True)

    total_size = 0
    generated = 0
    failed = 0

    # Generate playlist voiceovers
    for folder_name, dbid in playlist_dbids.items():
        hex_name = dbid_to_hex_filename(dbid)
        wav_path = os.path.join(playlists_dir, hex_name + ".wav")
        if generate_voiceover_wav(wav_path, folder_name, ffmpeg_path):
            generated += 1
        else:
            generate_silent_wav(wav_path)
            failed += 1
        if os.path.isfile(wav_path):
            total_size += os.path.getsize(wav_path)

    # Generate track voiceovers
    for ipod_rel, (dbid, display_name) in track_dbids.items():
        hex_name = dbid_to_hex_filename(dbid)
        wav_path = os.path.join(tracks_dir, hex_name + ".wav")
        if generate_voiceover_wav(wav_path, display_name, ffmpeg_path):
            generated += 1
        else:
            generate_silent_wav(wav_path)
            failed += 1
        if os.path.isfile(wav_path):
            total_size += os.path.getsize(wav_path)

    log(f"  VoiceOver: {generated} generated, {failed} fallback silent ({format_size(total_size)} total)")

    valid_wavs = set()
    for dbid in playlist_dbids.values():
        valid_wavs.add(dbid_to_hex_filename(dbid) + ".wav")
    for dbid, _ in track_dbids.values():
        valid_wavs.add(dbid_to_hex_filename(dbid) + ".wav")

    sweep_vo = 0
    for d in (tracks_dir, playlists_dir):
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.endswith(".wav") and f not in valid_wavs:
                    try:
                        os.remove(os.path.join(d, f))
                        sweep_vo += 1
                    except Exception: pass
    if sweep_vo > 0:
        log(f"  Cleaned up {sweep_vo} orphaned VoiceOver cache file(s)")

    return total_size


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


# ══════════════════════════════════════════════════════════════════════════════
#  SYNC ENGINE
# ══════════════════════════════════════════════════════════════════════════════

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
        import hashlib
        f_safe = "F_" + hashlib.md5(f_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
    b_safe = basename
    if not is_ascii(b_safe):
        import hashlib
        b_safe = "T_" + hashlib.md5(b_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
    return (f_safe.lower(), b_safe.lower())


def sync_to_ipod(ipod_path, source_files, target_format, target_bitrate, convert_all,
                 ffmpeg_path, voiceover_enabled=False, log_cb=None, progress_cb=None):
    """
    Incremental sync pipeline:
    1. Scan what's already on iPod
    2. Only copy/transcode NEW or MISSING files (unless convert_all forces full re-sync)
    3. Rebuild iTunesSD database with ALL files (existing + new)
    Returns (success, summary)
    """
    def log(msg):
        if log_cb:
            log_cb(msg)

    def progress(cur, tot, phase=""):
        if progress_cb:
            progress_cb(cur, tot, phase)

    music_dest = os.path.join(ipod_path, "iPod_Control", "Music")
    itunes_dir = os.path.join(ipod_path, "iPod_Control", "iTunes")
    os.makedirs(music_dest, exist_ok=True)
    os.makedirs(itunes_dir, exist_ok=True)

    # Clean legacy iTunes files
    for f in ["iTunesDB", "iTunesPrefs", "iTunesPrefs.plist", "iTunesControl", "iTunesStats", "iTunesPState"]:
        fp = os.path.join(itunes_dir, f)
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except:
                pass

    # If convert_all is checked, do a full wipe first (user wants everything re-encoded)
    if convert_all:
        log("Full re-encode requested. Clearing iPod music folder...")
        if os.path.isdir(music_dest):
            try:
                shutil.rmtree(music_dest)
            except Exception as e:
                log(f"  Warning: Could not fully clear: {e}")
            os.makedirs(music_dest, exist_ok=True)
        existing_on_ipod = {}
    else:
        log("Scanning iPod for existing files...")
        existing_on_ipod = scan_ipod_existing(ipod_path)
        log(f"  Found {len(existing_on_ipod)} existing track(s) on iPod")

    # Phase 2: Copy/Transcode only NEW files
    out_ext = ".mp3" if target_format == "MP3" else ".m4a"
    total = len(source_files)
    all_ipod_tracks = []   # ALL tracks (existing + new) for database
    playlists = {}         # folder_name -> [ipod_paths]
    errors = []
    copied_count = 0
    skipped_count = 0

    lock = threading.Lock()
    processed_count = 0
    
    def process_file(finfo):
        nonlocal copied_count, skipped_count, processed_count
        src = finfo["path"]
        ext = finfo["ext"]
        folder = finfo["folder"]
        basename = os.path.splitext(os.path.basename(src))[0]

        def is_ascii(s):
            return all(ord(c) < 128 for c in s)

        dest_subfolder = folder if folder else "_root"
        if not is_ascii(dest_subfolder):
            import hashlib
            dest_subfolder = "F_" + hashlib.md5(dest_subfolder.encode('utf-8', 'ignore')).hexdigest()[:8]
            
        dest_dir = os.path.join(music_dest, dest_subfolder)

        needs_transcode = convert_all or (ext not in IPOD_COMPATIBLE)
        
        safe_basename = basename
        if not is_ascii(safe_basename):
            import hashlib
            safe_basename = "T_" + hashlib.md5(safe_basename.encode('utf-8', 'ignore')).hexdigest()[:8]
            
        if needs_transcode and ffmpeg_path:
            out_filename = safe_basename + out_ext
        else:
            out_filename = safe_basename + ext

        ipod_rel = "/iPod_Control/Music/" + dest_subfolder + "/" + out_filename

        match_key = (dest_subfolder.lower(), safe_basename.lower())
        if match_key in existing_on_ipod:
            with lock:
                skipped_count += 1
            res_ipod_rel = existing_on_ipod[match_key]
        else:
            os.makedirs(dest_dir, exist_ok=True)
            if needs_transcode and ffmpeg_path:
                dest_file = os.path.join(dest_dir, out_filename)
                
                # Show what's happening
                with lock:
                    if total < 100 or processed_count % max(1, total // 50) == 0:
                        log(f"  \u2699 Transcoding \u2192 {out_filename}")

                try:
                    cmd = [ffmpeg_path, "-y", "-i", src]
                    if target_format == "MP3":
                        cmd += ["-codec:a", "libmp3lame", "-ar", "44100", "-ac", "2", "-b:a", f"{target_bitrate}k"]
                    else:
                        cmd += ["-codec:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", f"{target_bitrate}k"]
                    cmd += ["-map_metadata", "-1", "-vn", dest_file]

                    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                                            creationflags=creationflags)
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr[:200])
                    with lock:
                        copied_count += 1
                except Exception as e:
                    with lock:
                        errors.append(f"{basename}: {e}")
                        log(f"  \u2718 Error transcoding: {basename}")
                    return None, folder, basename
            else:
                dest_file = os.path.join(dest_dir, out_filename)
                
                with lock:
                    if total < 100 or processed_count % max(1, total // 50) == 0:
                        log(f"  \u27A4 Copying \u2192 {out_filename}")

                try:
                    shutil.copy2(src, dest_file)
                    with lock:
                        copied_count += 1
                except Exception as e:
                    with lock:
                        errors.append(f"{basename}: {e}")
                        log(f"  \u2718 Error copying: {basename}")
                    return None, folder, basename
            res_ipod_rel = ipod_rel

        with lock:
            processed_count += 1
            # Rate limit GUI updates via thread safely
            progress(processed_count, total, "Syncing")
            
        return res_ipod_rel, folder, basename

    workers = min(32, (os.cpu_count() or 1) + 4)
    original_titles = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_file, f) for f in source_files]
        for future in futures:
            res_ipod_rel, folder, basename = future.result()
            if res_ipod_rel:
                all_ipod_tracks.append(res_ipod_rel)
                original_titles[res_ipod_rel] = basename
                if folder:
                    if folder not in playlists:
                        playlists[folder] = []
                    playlists[folder].append(res_ipod_rel)

    log(f"  Copied {copied_count} new, skipped {skipped_count} existing ({len(errors)} errors)")

    if not all_ipod_tracks:
        log("ERROR: No tracks available for database.")
        return False, {"tracks": 0, "playlists": 0, "errors": errors}

    valid_ipod_paths = {p.lower() for p in all_ipod_tracks}
    sweep_count = 0
    for key, rel_path in existing_on_ipod.items():
        if rel_path.lower() not in valid_ipod_paths:
            full_remove = os.path.normpath(os.path.join(ipod_path, rel_path.lstrip("/")))
            try:
                os.remove(full_remove)
                sweep_count += 1
            except Exception:
                pass
    if sweep_count > 0:
        log(f"  Cleaned up {sweep_count} orphaned/unselected file(s) from iPod.")

    # Sweep empty folders
    try:
        for d in os.listdir(music_dest):
            dp = os.path.join(music_dest, d)
            if os.path.isdir(dp) and not os.listdir(dp):
                os.rmdir(dp)
    except Exception:
        pass

    # Phase 3: Build database
    log("Phase 3: Building iTunesSD database...")
    num_tracks = len(all_ipod_tracks)
    num_playlists = 1 + len(playlists)

    tunessd = TunesSD(num_tracks, num_playlists)
    if voiceover_enabled:
        tunessd["voiceover_enabled"] = 1
    track_header = TrackHeader(num_tracks)
    track_header_offset = 64

    track_header_bytes = track_header.construct()
    tracks_chunk = b""
    track_index_map = {}
    track_dbids = {}  # For VoiceOver: {ipod_path: (dbid_bytes, display_name)}

    for i, ipod_path_str in enumerate(all_ipod_tracks):
        track = Track()
        orig_title = original_titles.get(ipod_path_str)
        track.populate(ipod_path_str, orig_title)

        ptr_offset = track_header_offset + 20 + (num_tracks * 4) + len(tracks_chunk)
        track_header_bytes += struct.pack("<I", ptr_offset)
        tracks_chunk += track.construct()

        track_index_map[ipod_path_str] = i
        # Collect dbid for VoiceOver
        display_name = orig_title if orig_title else os.path.splitext(os.path.basename(ipod_path_str))[0]
        track_dbids[ipod_path_str] = (track["dbid"], display_name)
        progress(i + 1, num_tracks, "Indexing")

    full_track_segment = track_header_bytes + tracks_chunk

    playlist_header_offset = track_header_offset + len(full_track_segment)
    tunessd["playlist_header_offset"] = playlist_header_offset

    play_header = PlaylistHeader(num_playlists)
    play_header_base = play_header.construct()
    playlist_chunks = []
    playlist_dbids = {}  # For VoiceOver: {folder_name: dbid_bytes}

    # Master playlist
    master = Playlist(num_tracks, listtype=1)
    master_data = master.construct()
    for i in range(num_tracks):
        master_data += struct.pack("<I", i)
    playlist_chunks.append(master_data)

    # Folder playlists
    for folder_name in sorted(playlists.keys()):
        folder_tracks = playlists[folder_name]
        indices = [track_index_map[t] for t in folder_tracks if t in track_index_map]
        if not indices:
            continue
        dbid = hashlib.md5(folder_name.encode('utf-8')).digest()[:8]
        pl = Playlist(len(indices), listtype=2, dbid=dbid)
        pl_data = pl.construct()
        for idx in indices:
            pl_data += struct.pack("<I", idx)
        playlist_chunks.append(pl_data)
        playlist_dbids[folder_name] = dbid
        log(f"  Playlist '{folder_name}': {len(indices)} tracks")

    # Adjust if any playlists were skipped
    actual_playlists = len(playlist_chunks)
    if actual_playlists != num_playlists:
        tunessd = TunesSD(num_tracks, actual_playlists)
        if voiceover_enabled:
            tunessd["voiceover_enabled"] = 1
        tunessd["playlist_header_offset"] = playlist_header_offset
        play_header = PlaylistHeader(actual_playlists)
        play_header_base = play_header.construct()
        num_playlists = actual_playlists

    play_header_total_length = 20 + (num_playlists * 4)
    current_offset = playlist_header_offset + play_header_total_length
    play_header_with_ptrs = play_header_base
    for chunk in playlist_chunks:
        play_header_with_ptrs += struct.pack("<I", current_offset)
        current_offset += len(chunk)

    full_playlist_segment = play_header_with_ptrs + b"".join(playlist_chunks)
    final_db = tunessd.construct() + full_track_segment + full_playlist_segment

    try:
        with open(os.path.join(itunes_dir, "iTunesSD"), "wb") as f:
            f.write(final_db)
        log(f"  Database written: {format_size(len(final_db))}")
    except Exception as e:
        log(f"ERROR: Failed to write iTunesSD: {e}")
        return False, {"tracks": num_tracks, "playlists": num_playlists, "errors": errors}

    # Phase 4: VoiceOver generation
    vo_size = 0
    if voiceover_enabled:
        log("Phase 4: Generating VoiceOver audio...")
        vsize = build_voiceover(ipod_path, track_dbids, playlist_dbids, ffmpeg_path=ffmpeg_path, log_cb=log)

    summary = {
        "tracks": num_tracks,
        "playlists": num_playlists,
        "errors": errors,
        "playlist_names": sorted(playlists.keys()),
        "db_size": len(final_db),
        "voiceover_size": vo_size,
    }
    return True, summary


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


# ══════════════════════════════════════════════════════════════════════════════
#  GUI APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class AntigravityApp:
    BG_DARK    = "#141218"
    BG_PANEL   = "#211F26"
    BG_INPUT   = "#36343B"
    BG_CARD    = "#2B2930"
    FG_TEXT    = "#E6E1E5"
    FG_DIM     = "#CAC4D0"
    FG_BRIGHT  = "#FFFFFF"
    ACCENT     = "#D0BCFF"
    ACCENT_HOV = "#E8DEF8"
    SUCCESS    = "#9BCF53"
    WARNING    = "#F4B678"
    ERROR      = "#F2B8B5"
    BORDER     = "#332D41"

    BITRATES = ["64", "96", "128", "160", "192", "256", "320"]
    FORMATS  = ["MP3", "AAC"]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{__title__} v{__version__}")
        self.root.geometry("1200x700")
        self.root.minsize(1050, 680)
        self.root.configure(bg=self.BG_DARK)

        # Dark title bar
        try:
            self.root.update()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

        self.ffmpeg_path = find_ffmpeg()
        self._drive_paths = {}
        self._source_files = []
        self._estimated_size = 0
        self.view_mode = "LIST"

        # Thumbnail system
        self._thumbnail_cache = {}  # path -> tk.PhotoImage
        self._thumbnail_queue = queue.Queue()
        self._thumbnail_thread_running = True
        self._folder_grid_containers = {}
        self._thumbnail_thread = threading.Thread(target=self._thumbnail_worker, daemon=True)
        self._thumbnail_thread.start()

        self._setup_styles()
        self._build_ui()
        self._refresh_drives()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(100, self._load_config)

    def _thumbnail_worker(self):
        """Worker thread to extract thumbnails sequentially."""
        while self._thumbnail_thread_running:
            try:
                path, cb_widget = self._thumbnail_queue.get(timeout=1)
                
                # Check cache first
                if path in self._thumbnail_cache:
                    ppm_data = self._thumbnail_cache[path]
                else:
                    ppm_data = extract_thumbnail_ppm(path, self.ffmpeg_path, size=140)
                    if ppm_data:
                        self._thumbnail_cache[path] = ppm_data
                
                # Update UI safely
                if ppm_data:
                    def update_ui(w=cb_widget, d=ppm_data):
                        try:
                            # PhotoImage must be kept alive, store on widget
                            img = tk.PhotoImage(data=d)
                            w.img_ref = img
                            
                            # Clean up placeholder text if it exists and reset dimensions to pixel count instead of character count
                            w.configure(image=img, text="", compound=tk.CENTER, width=140, height=140)
                        except Exception:
                            pass
                    self.root.after(0, update_ui)
            except queue.Empty:
                pass
            except Exception:
                pass

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background=self.BG_DARK)
        style.configure("Card.TFrame", background=self.BG_CARD)
        style.configure("Dark.TLabel", background=self.BG_DARK, foreground=self.FG_TEXT, font=("Segoe UI Variable Display", 11))
        style.configure("Title.TLabel", background=self.BG_DARK, foreground=self.FG_BRIGHT, font=("Segoe UI Variable Display", 26, "bold"))
        style.configure("Subtitle.TLabel", background=self.BG_DARK, foreground=self.FG_DIM, font=("Segoe UI Variable Display", 11))
        style.configure("Card.TLabel", background=self.BG_CARD, foreground=self.FG_TEXT, font=("Segoe UI Variable Display", 11))
        style.configure("CardDim.TLabel", background=self.BG_CARD, foreground=self.FG_DIM, font=("Segoe UI Variable Display", 11))
        style.configure("CardBright.TLabel", background=self.BG_CARD, foreground=self.FG_BRIGHT, font=("Segoe UI Variable Display", 13, "bold"))
        style.configure("CardValue.TLabel", background=self.BG_CARD, foreground=self.ACCENT, font=("Segoe UI Variable Display", 13, "bold"))
        style.configure("CardWarn.TLabel", background=self.BG_CARD, foreground=self.ERROR, font=("Segoe UI Variable Display", 11, "bold"))
        style.configure("CardOk.TLabel", background=self.BG_CARD, foreground=self.SUCCESS, font=("Segoe UI Variable Display", 11, "bold"))
        style.configure("Status.TLabel", background=self.BG_DARK, foreground=self.SUCCESS, font=("Segoe UI Variable Display", 13, "bold"))
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#381E72",
                         font=("Segoe UI Variable Display", 13, "bold"), padding=(24, 10), borderwidth=0)
        style.map("Accent.TButton", background=[("active", self.ACCENT_HOV), ("disabled", self.BG_INPUT)],
                  foreground=[("disabled", self.FG_DIM)])
        style.configure("Secondary.TButton", background=self.BG_INPUT, foreground=self.FG_TEXT,
                         font=("Segoe UI Variable Display", 11), padding=(12, 6), borderwidth=0)
        style.map("Secondary.TButton", background=[("active", self.BORDER)])
        style.configure("Small.TButton", background=self.BG_INPUT, foreground=self.FG_TEXT,
                         font=("Segoe UI Variable Display", 10), padding=(12, 6), borderwidth=0)
        style.map("Small.TButton", background=[("active", self.BORDER)])
        style.configure("Dark.TCombobox", fieldbackground=self.BG_INPUT, background=self.BG_INPUT,
                         foreground=self.FG_TEXT, arrowcolor=self.ACCENT, borderwidth=1, relief="flat")
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", self.BG_INPUT)],
                  selectbackground=[("readonly", self.BG_INPUT)],
                  selectforeground=[("readonly", self.FG_TEXT)])

        style.configure("Panel.TFrame", background=self.BG_PANEL)
        style.configure("green.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,
                         background=self.ACCENT, borderwidth=0, thickness=12)
        style.configure("space.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,
                         background=self.SUCCESS, borderwidth=0, thickness=20)
        style.configure("spacewarn.Horizontal.TProgressbar", troughcolor=self.BG_INPUT,
                         background=self.ERROR, borderwidth=0, thickness=20)
        style.configure("Dark.TCheckbutton", background=self.BG_CARD, foreground=self.FG_TEXT,
                         font=("Segoe UI Variable Display", 11))
        style.map("Dark.TCheckbutton", background=[("active", self.BG_CARD)])

    def _build_ui(self):
        # ── Root container ────────────────────────────────────────────────
        root_frame = ttk.Frame(self.root, style="Dark.TFrame")
        root_frame.pack(fill=tk.BOTH, expand=True)

        # ── Header bar ───────────────────────────────────────────────────
        header = ttk.Frame(root_frame, style="Dark.TFrame")
        header.pack(fill=tk.X, padx=24, pady=(24, 8))
        ttk.Label(header, text="\u2B21  Antigravity", style="Title.TLabel").pack(side=tk.LEFT)
        sub_text = f"iPod Shuffle 4G Sync Manager  \u2022  v{__version__}"
        if not self.ffmpeg_path:
            sub_text += "  \u2022  \u26A0 ffmpeg not found"
        ttk.Label(header, text=sub_text, style="Subtitle.TLabel").pack(side=tk.LEFT, padx=(12, 0), pady=(8, 0))

        # ── Two-panel body ───────────────────────────────────────────────
        body = tk.PanedWindow(root_frame, orient=tk.HORIZONTAL, bg=self.BG_DARK,
                              bd=0, sashwidth=4, sashrelief="flat",
                              sashpad=0)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 12))

        # ═══════════════════════════════════════════════════════════════
        #  LEFT PANEL — Controls
        # ═══════════════════════════════════════════════════════════════
        left_border = tk.Frame(body, bg=self.BORDER)
        body.add(left_border, minsize=360, width=380)

        self._left_canvas = tk.Canvas(left_border, bg=self.BG_DARK, highlightthickness=0, bd=0)
        left_scroll = ttk.Scrollbar(left_border, orient=tk.VERTICAL, command=self._left_canvas.yview)
        
        left_inner = ttk.Frame(self._left_canvas, style="Dark.TFrame")
        
        def _on_left_inner_configure(e):
            self._left_canvas.configure(scrollregion=self._left_canvas.bbox("all"))
        left_inner.bind("<Configure>", _on_left_inner_configure)
        
        self._left_canvas_window = self._left_canvas.create_window((0, 0), window=left_inner, anchor="nw")
        
        def _on_left_canvas_configure(e):
            self._left_canvas.itemconfig(self._left_canvas_window, width=e.width)
        self._left_canvas.bind("<Configure>", _on_left_canvas_configure)
        
        self._left_canvas.configure(yscrollcommand=left_scroll.set)
        
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)

        left = ttk.Frame(left_inner, style="Dark.TFrame")
        left.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)

        # ── iPod Drive ────────────────────────────────────────────────
        ttk.Label(left, text="iPod Drive", style="Dark.TLabel").pack(anchor="w")
        row1 = ttk.Frame(left, style="Dark.TFrame")
        row1.pack(fill=tk.X, pady=(2, 8))
        self.drive_var = tk.StringVar()
        self.drive_combo = ttk.Combobox(row1, textvariable=self.drive_var, state="readonly",
                                         style="Dark.TCombobox", font=("Segoe UI Variable Display", 11))
        self.drive_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.drive_combo.bind("<<ComboboxSelected>>", lambda e: self._recalculate())
        self.btn_refresh = ttk.Button(row1, text="\u21BB", style="Small.TButton",
                                      command=self._refresh_drives)
        self.btn_refresh.pack(side=tk.LEFT, padx=(0, 2))
        ToolTip(self.btn_refresh, "Refresh external drives list")

        self.btn_browse_drive = ttk.Button(row1, text="...", style="Small.TButton",
                                           command=self._browse_drive)
        self.btn_browse_drive.pack(side=tk.LEFT)
        ToolTip(self.btn_browse_drive, "Manually browse for iPod drive path")

        # ── Music Source ──────────────────────────────────────────────
        ttk.Label(left, text="Music Source Folder", style="Dark.TLabel").pack(anchor="w")
        row2 = ttk.Frame(left, style="Dark.TFrame")
        row2.pack(fill=tk.X, pady=(2, 10))
        self.music_var = tk.StringVar()
        self.music_entry = tk.Entry(row2, textvariable=self.music_var, font=("Segoe UI Variable Display", 11),
                                     bg=self.BG_INPUT, fg=self.FG_TEXT, insertbackground=self.FG_TEXT,
                                     relief="flat", bd=4, state="readonly",
                                     readonlybackground=self.BG_INPUT)
        self.music_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.btn_browse_music = ttk.Button(row2, text="Browse\u2026", style="Small.TButton",
                                           command=self._browse_music)
        self.btn_browse_music.pack(side=tk.LEFT)
        ToolTip(self.btn_browse_music, "Select the parent folder of your playlists/music")

        # ── Separator ────────────────────────────────────────────────
        tk.Frame(left, bg=self.BORDER, height=1).pack(fill=tk.X, pady=(0, 8))

        # ── Transcoding ──────────────────────────────────────────────
        tc_card = tk.Frame(left, bg=self.BG_CARD)
        tc_card.pack(fill=tk.X, pady=(0, 8))

        tc_hdr = tk.Frame(tc_card, bg=self.BG_CARD)
        tc_hdr.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(tc_hdr, text="Transcoding", font=("Segoe UI Variable Display", 11, "bold"),
                 bg=self.BG_CARD, fg=self.FG_BRIGHT).pack(side=tk.LEFT)

        tc_row = tk.Frame(tc_card, bg=self.BG_CARD)
        tc_row.pack(fill=tk.X, padx=10, pady=(0, 4))
        tk.Label(tc_row, text="Format:", font=("Segoe UI Variable Display", 11),
                 bg=self.BG_CARD, fg=self.FG_TEXT).pack(side=tk.LEFT, padx=(0, 4))
        self.format_var = tk.StringVar(value="MP3")
        fmt_combo = ttk.Combobox(tc_row, textvariable=self.format_var, values=self.FORMATS,
                                  state="readonly" if self.ffmpeg_path else "disabled",
                                  style="Dark.TCombobox", font=("Segoe UI Variable Display", 11), width=5)
        fmt_combo.pack(side=tk.LEFT, padx=(0, 10))
        fmt_combo.bind("<<ComboboxSelected>>", lambda e: self._recalculate())

        tk.Label(tc_row, text="Bitrate:", font=("Segoe UI Variable Display", 11),
                 bg=self.BG_CARD, fg=self.FG_TEXT).pack(side=tk.LEFT, padx=(0, 4))
        self.bitrate_var = tk.StringVar(value="128")
        br_combo = ttk.Combobox(tc_row, textvariable=self.bitrate_var, values=self.BITRATES,
                                 state="readonly" if self.ffmpeg_path else "disabled",
                                 style="Dark.TCombobox", font=("Segoe UI Variable Display", 11), width=5)
        br_combo.pack(side=tk.LEFT, padx=(0, 2))
        br_combo.bind("<<ComboboxSelected>>", lambda e: self._recalculate())
        tk.Label(tc_row, text="kbps", font=("Segoe UI Variable Display", 10),
                 bg=self.BG_CARD, fg=self.FG_DIM).pack(side=tk.LEFT)

        tc_opts = tk.Frame(tc_card, bg=self.BG_CARD)
        tc_opts.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.convert_all_var = tk.BooleanVar(value=False)
        self.convert_check = tk.Checkbutton(tc_opts, text="Re-encode all",
                                             variable=self.convert_all_var, font=("Segoe UI Variable Display", 10),
                                             bg=self.BG_CARD, fg=self.FG_TEXT,
                                             selectcolor=self.BG_INPUT, activebackground=self.BG_CARD,
                                             command=self._recalculate)
        self.convert_check.pack(side=tk.LEFT, padx=(0, 8))

        self.voiceover_var = tk.BooleanVar(value=True)
        self.voiceover_check = tk.Checkbutton(tc_opts, text="VoiceOver",
                                               variable=self.voiceover_var, font=("Segoe UI Variable Display", 10),
                                               bg=self.BG_CARD, fg=self.FG_TEXT,
                                               selectcolor=self.BG_INPUT, activebackground=self.BG_CARD,
                                               command=self._recalculate)
        self.voiceover_check.pack(side=tk.LEFT)

        if not self.ffmpeg_path:
            self.convert_check.configure(state="disabled")

        # ── Space Dashboard ──────────────────────────────────────────
        sd_card = tk.Frame(left, bg=self.BG_CARD)
        sd_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(sd_card, text="Space Dashboard", font=("Segoe UI Variable Display", 11, "bold"),
                 bg=self.BG_CARD, fg=self.FG_BRIGHT).pack(anchor="w", padx=10, pady=(8, 4))

        stats_frame = tk.Frame(sd_card, bg=self.BG_CARD)
        stats_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
        stats_frame.columnconfigure(1, weight=1)

        def add_stat(row, label_text, var_name):
            tk.Label(stats_frame, text=label_text, font=("Segoe UI Variable Display", 10),
                     bg=self.BG_CARD, fg=self.FG_DIM).grid(row=row, column=0, sticky="w", padx=(0, 4), pady=1)
            lbl = tk.Label(stats_frame, text="--", font=("Segoe UI Variable Display", 11, "bold"),
                           bg=self.BG_CARD, fg=self.ACCENT)
            lbl.grid(row=row, column=1, sticky="w", pady=1)
            setattr(self, var_name, lbl)

        add_stat(0, "Source:", "_lbl_source_size")
        add_stat(1, "Files:", "_lbl_file_count")
        add_stat(2, "Output:", "_lbl_estimated_size")
        add_stat(3, "Playlists:", "_lbl_playlist_count")
        add_stat(4, "Free:", "_lbl_free_space")
        add_stat(5, "After:", "_lbl_remaining")

        bar_frame = tk.Frame(sd_card, bg=self.BG_CARD)
        bar_frame.pack(fill=tk.X, padx=10, pady=(2, 4))
        self.space_pct_var = tk.DoubleVar(value=0)
        self.space_bar = ttk.Progressbar(bar_frame, variable=self.space_pct_var,
                                          maximum=100, style="space.Horizontal.TProgressbar")
        self.space_bar.pack(fill=tk.X)
        self.space_status_label = tk.Label(sd_card, text="", font=("Segoe UI Variable Display", 10),
                                           bg=self.BG_CARD, fg=self.FG_DIM)
        self.space_status_label.pack(anchor="w", padx=10, pady=(0, 8))

        # ── Spacer to keep consistent gap ──────────────────────────────
        tk.Frame(left, height=20, bg=self.BG_DARK).pack(fill=tk.X)

        # ── Action Buttons ───────────────────────────────────────────
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(left, variable=self.progress_var,
                                             maximum=100, style="green.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 4))
        self.progress_label = ttk.Label(left, text="", style="Subtitle.TLabel")
        self.progress_label.pack(anchor="w", pady=(0, 4))

        btn_row = ttk.Frame(left, style="Dark.TFrame")
        btn_row.pack(fill=tk.X, pady=(0, 4))
        self.sync_btn = ttk.Button(btn_row, text="\u25B6  Sync to iPod",
                                    style="Accent.TButton", command=self._start_sync)
        self.sync_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ToolTip(self.sync_btn, "Upload checked tracks and update iTunesDB on device")

        self.rebuild_btn = ttk.Button(btn_row, text="\u2699 Rebuild",
                                       style="Secondary.TButton", command=self._start_rebuild_only)
        self.rebuild_btn.pack(side=tk.LEFT)
        ToolTip(self.rebuild_btn, "Skip copying. Scan iPod contents and rebuild database only")

        self.status_label = ttk.Label(left, text="Select iPod drive and music folder to begin.",
                                       style="Subtitle.TLabel", wraplength=360)
        self.status_label.pack(anchor="w", pady=(2, 0))

        # ═══════════════════════════════════════════════════════════════
        #  RIGHT PANEL — Music Selection + Console
        # ═══════════════════════════════════════════════════════════════
        right_border = tk.Frame(body, bg=self.BORDER)
        right_inner = ttk.Frame(right_border, style="Dark.TFrame")
        right_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        body.add(right_border, minsize=400)

        right = ttk.Frame(right_inner, style="Dark.TFrame")
        right.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)

        # ── Selection Panel ──────────────────────────────────────────
        sel_border = tk.Frame(right, bg=self.BORDER, bd=0)
        sel_border.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        sel_outer = tk.Frame(sel_border, bg=self.BG_PANEL)
        sel_outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Search bar
        search_frame = tk.Frame(sel_outer, bg=self.BG_CARD)
        search_frame.pack(fill=tk.X)
        tk.Label(search_frame, text=" \u2315", font=("Segoe UI Variable Display", 13),
                 bg=self.BG_CARD, fg=self.FG_DIM).pack(side=tk.LEFT, padx=(6, 0))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *a: self._apply_search())
        search_entry = tk.Entry(search_frame, textvariable=self._search_var, font=("Segoe UI Variable Display", 11),
                                bg=self.BG_INPUT, fg=self.FG_TEXT, insertbackground=self.FG_TEXT,
                                relief="flat", bd=4)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        
        self.btn_view_toggle = ttk.Button(search_frame, text="\u2630 List", style="Small.TButton",
                                          command=self._toggle_view_mode, width=8)
        self.btn_view_toggle.pack(side=tk.RIGHT, padx=4, pady=4)
        ToolTip(self.btn_view_toggle, "Toggle between fast List view and visual Grid view")

        # Header row
        self.sel_hdr = tk.Frame(sel_outer, bg=self.BG_CARD)
        self.sel_hdr.pack(fill=tk.X)
        tk.Label(self.sel_hdr, text="  Playlist / Track", font=("Segoe UI Variable Display", 11, "bold"),
                 bg=self.BG_CARD, fg=self.FG_TEXT, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=3)
        tk.Label(self.sel_hdr, text="Size", font=("Segoe UI Variable Display", 11, "bold"),
                 bg=self.BG_CARD, fg=self.FG_TEXT, width=10, anchor="e").pack(side=tk.RIGHT, padx=8, pady=3)

        # Scrollable canvas
        self._sel_canvas = tk.Canvas(sel_outer, bg=self.BG_PANEL, highlightthickness=0, bd=0)
        sel_scroll = ttk.Scrollbar(sel_outer, orient=tk.VERTICAL, command=self._sel_canvas.yview)
        self._sel_inner = tk.Frame(self._sel_canvas, bg=self.BG_PANEL)

        self._sel_inner.bind("<Configure>",
            lambda e: self._sel_canvas.configure(scrollregion=self._sel_canvas.bbox("all")))
        self._sel_canvas.create_window((0, 0), window=self._sel_inner, anchor="nw")
        self._sel_canvas.configure(yscrollcommand=sel_scroll.set)

        sel_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._sel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Data structures for the checkbox panel
        self._file_map = {}
        self._file_vars = {}
        self._folder_vars = {}
        self._folder_file_paths = {}
        self._folder_content_frames = {}
        self._folder_expanded = {}
        self._folder_toggle_btns = {}
        self._folder_header_frames = {}
        self._all_file_rows = {}

        # Select All New button
        tree_btn_row = ttk.Frame(right, style="Dark.TFrame")
        tree_btn_row.pack(fill=tk.X, pady=(0, 6))
        self.btn_select_new = ttk.Button(tree_btn_row, text="\u271a Select All New", style="Small.TButton",
                                         command=self._select_all_new)
        self.btn_select_new.pack(side=tk.LEFT)
        ToolTip(self.btn_select_new, "Read iPod and auto-check only songs you haven't synced yet")

        # ── Console Log ──────────────────────────────────────────────
        log_border = tk.Frame(right, bg=self.BORDER, bd=0)
        log_border.pack(fill=tk.X, pady=(0, 0))
        log_inner = tk.Frame(log_border, bg=self.BG_PANEL)
        log_inner.pack(fill=tk.BOTH, expand=False, padx=1, pady=1)

        self.log_text = tk.Text(log_inner, bg=self.BG_PANEL, fg=self.FG_DIM,
                                font=("Consolas", 10), wrap=tk.WORD,
                                insertbackground=self.FG_TEXT, relief="flat",
                                padx=10, pady=6, state=tk.DISABLED, height=7,
                                selectbackground=self.ACCENT, selectforeground=self.BG_DARK)
        scrollbar = tk.Scrollbar(log_inner, command=self.log_text.yview,
                                  bg=self.BG_INPUT, troughcolor=self.BG_PANEL,
                                  activebackground=self.ACCENT, width=8, relief="flat")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ── Drive Management ─────────────────────────────────────────────────

        # Smart global mouse wheel scrolling
        def _global_mousewheel(event):
            w_path = str(event.widget)
            if w_path.startswith(str(self._sel_canvas)):
                self._sel_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif hasattr(self, '_left_canvas') and w_path.startswith(str(self._left_canvas)):
                self._left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif w_path.startswith(str(self.log_text)):
                self.log_text.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.root.bind_all("<MouseWheel>", _global_mousewheel)

    def _refresh_drives(self):
        drives = detect_ipod_drives()
        self._drive_paths = {}
        display_list = []
        for path, label in drives:
            display_list.append(label)
            self._drive_paths[label] = path
        self.drive_combo["values"] = display_list
        if display_list:
            self.drive_combo.current(0)
            self._log(f"Detected {len(display_list)} iPod drive(s)")
        else:
            self.drive_var.set("")
            self._log("No iPod drives detected. Use Browse to select manually.")
        self._recalculate()

    def _browse_drive(self):
        path = filedialog.askdirectory(title="Select iPod Root Directory")
        if path:
            path = os.path.normpath(path)
            label = f"Custom: {path}"
            vals = [v for v in self.drive_combo["values"] if not v.startswith("Custom:")]
            vals.append(label)
            self.drive_combo["values"] = vals
            self._drive_paths[label] = path
            self.drive_var.set(label)
            self._recalculate()

    def _browse_music(self):
        path = filedialog.askdirectory(title="Select Music Source Folder")
        if path:
            self.music_var.set(os.path.normpath(path))
            self._log(f"Music source: {path}")
            self._populate_tree()
            self._recalculate()

    def _populate_tree(self):
        # Clear existing widgets
        for w in self._sel_inner.winfo_children():
            w.destroy()
        
        # Clear thumbnail queue to avoid backlogs on quick refreshes
        while not self._thumbnail_queue.empty():
            try:
                self._thumbnail_queue.get_nowait()
            except queue.Empty:
                break

        # Save old states before purging to preserve checks across toggles/refreshes
        old_vars = {p: v.get() for p, v in self._file_vars.items()}
        old_folders = {fn: v.get() for fn, v in self._folder_vars.items()}

        self._file_map.clear()
        self._file_vars.clear()
        self._folder_vars.clear()
        self._folder_file_paths.clear()
        self._folder_content_frames.clear()
        self._folder_grid_containers.clear()
        self._folder_expanded.clear()
        self._folder_toggle_btns.clear()
        self._folder_header_frames.clear()
        self._all_file_rows.clear()

        music_path = self.music_var.get()
        if not music_path or not os.path.isdir(music_path):
            return

        all_files = scan_source_folder(music_path)
        folders = {}
        for f in all_files:
            folder = f["folder"] if f["folder"] else "Root (All Songs)"
            if folder not in folders:
                folders[folder] = []
            folders[folder].append(f)

        if getattr(self, "view_mode", "LIST") == "LIST":
            self._build_list_view(folders, old_vars, old_folders)
            if hasattr(self, "btn_view_toggle"):
                self.btn_view_toggle.configure(text="\u2630 List")
            if hasattr(self, "sel_hdr"):
                self.sel_hdr.pack(fill=tk.X, before=self._sel_canvas)
        else:
            self._build_grid_view(folders, old_vars, old_folders)
            if hasattr(self, "btn_view_toggle"):
                self.btn_view_toggle.configure(text="\u25A6 Grid")
            if hasattr(self, "sel_hdr"):
                self.sel_hdr.pack_forget()

        # Re-apply any active search
        self._apply_search()
        
        # Reset scroll position
        self._sel_canvas.yview_moveto(0)

    def _build_list_view(self, folders, old_vars, old_folders):
        """Ultra-fast default render with no thumbnails."""
        for folder_name in sorted(folders.keys()):
            folder_var = tk.BooleanVar(value=old_folders.get(folder_name, True))
            self._folder_vars[folder_name] = folder_var
            self._folder_file_paths[folder_name] = []
            self._folder_expanded[folder_name] = True

            folder_frame = tk.Frame(self._sel_inner, bg=self.BG_CARD)
            folder_frame.pack(fill=tk.X, padx=8, pady=(16, 4))
            self._folder_header_frames[folder_name] = folder_frame

            folder_size = sum(f["size"] for f in folders[folder_name])

            toggle_btn = tk.Label(folder_frame, text="\u25BC", font=("Segoe UI Variable Display", 11),
                                  bg=self.BG_CARD, fg=self.ACCENT, cursor="hand2", width=2)
            toggle_btn.pack(side=tk.LEFT, padx=(4, 0))
            toggle_btn.bind("<Button-1>", lambda e, fn=folder_name: self._toggle_folder(fn))
            self._folder_toggle_btns[folder_name] = toggle_btn

            cb = tk.Checkbutton(folder_frame,
                                text=folder_name + f"  ({len(folders[folder_name])} tracks)",
                                variable=folder_var, font=("Segoe UI Variable Display", 11, "bold"),
                                bg=self.BG_CARD, fg=self.FG_BRIGHT,
                                selectcolor=self.BG_INPUT, activebackground=self.BG_CARD,
                                activeforeground=self.FG_BRIGHT, anchor="w",
                                command=lambda fn=folder_name: self._on_folder_toggle(fn))
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=0, pady=2)

            tk.Label(folder_frame, text=format_size(folder_size), font=("Segoe UI Variable Display", 11),
                     bg=self.BG_CARD, fg=self.FG_DIM, width=10, anchor="e").pack(side=tk.RIGHT, padx=8)

            file_frames = []
            for f in folders[folder_name]:
                path = f["path"]
                file_var = tk.BooleanVar(value=old_vars.get(path, True))
                self._file_map[path] = f
                self._file_vars[path] = file_var
                self._folder_file_paths[folder_name].append(path)

                file_row = tk.Frame(self._sel_inner, bg=self.BG_PANEL)
                file_row.pack(fill=tk.X, padx=8, pady=(2, 2))

                basename = os.path.basename(path)

                cb = tk.Checkbutton(file_row, text=f"  {basename}",
                                    variable=file_var, font=("Segoe UI Variable Display", 11),
                                    bg=self.BG_PANEL, fg=self.FG_TEXT,
                                    selectcolor=self.BG_INPUT, activebackground=self.BG_PANEL,
                                    activeforeground=self.FG_TEXT, anchor="w",
                                    command=self._recalculate)
                cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(32, 8), pady=4)

                tk.Label(file_row, text=format_size(f["size"]), font=("Segoe UI Variable Display", 10),
                         bg=self.BG_PANEL, fg=self.FG_DIM, width=10, anchor="e").pack(side=tk.RIGHT, padx=8)

                file_frames.append(file_row)
                self._all_file_rows[path] = file_row

            self._folder_content_frames[folder_name] = file_frames

    def _build_grid_view(self, folders, old_vars, old_folders):
        """Spotify-style cards with lazy loaded album art."""
        for folder_name in sorted(folders.keys()):
            folder_var = tk.BooleanVar(value=old_folders.get(folder_name, True))
            self._folder_vars[folder_name] = folder_var
            self._folder_file_paths[folder_name] = []
            self._folder_expanded[folder_name] = True

            folder_frame = tk.Frame(self._sel_inner, bg=self.BG_PANEL)
            folder_frame.pack(fill=tk.X, padx=8, pady=(16, 4))
            self._folder_header_frames[folder_name] = folder_frame

            toggle_btn = tk.Label(folder_frame, text="\u25BC", font=("Segoe UI Variable Display", 14),
                                  bg=self.BG_PANEL, fg=self.ACCENT, cursor="hand2", width=2)
            toggle_btn.pack(side=tk.LEFT, padx=(0, 4))
            toggle_btn.bind("<Button-1>", lambda e, fn=folder_name: self._toggle_folder(fn))
            self._folder_toggle_btns[folder_name] = toggle_btn

            tk.Label(folder_frame, text=folder_name, font=("Segoe UI Variable Display", 16, "bold"),
                     bg=self.BG_PANEL, fg=self.FG_BRIGHT).pack(side=tk.LEFT)
            
            cb = tk.Checkbutton(folder_frame, text="Sync Playlist",
                                variable=folder_var, font=("Segoe UI Variable Display", 11),
                                bg=self.BG_PANEL, fg=self.FG_TEXT,
                                selectcolor=self.BG_INPUT, activebackground=self.BG_PANEL,
                                activeforeground=self.FG_TEXT,
                                command=lambda fn=folder_name: self._on_folder_toggle(fn))
            cb.pack(side=tk.LEFT, padx=16)

            grid_container = tk.Frame(self._sel_inner, bg=self.BG_PANEL)
            grid_container.pack(fill=tk.X, padx=8)
            self._folder_grid_containers[folder_name] = grid_container

            file_frames = []
            for f in folders[folder_name]:
                path = f["path"]
                file_var = tk.BooleanVar(value=old_vars.get(path, True))
                self._file_map[path] = f
                self._file_vars[path] = file_var
                self._folder_file_paths[folder_name].append(path)

                card = tk.Frame(grid_container, bg=self.BG_CARD, width=150, height=200)
                card.pack_propagate(False)
                
                img_lbl = tk.Label(card, text="♪", font=("Segoe UI", 36), bg=self.BORDER, fg=self.FG_DIM, width=15, height=5)
                img_lbl.pack(fill=tk.X, padx=10, pady=(10, 4))
                
                basename = os.path.basename(path)
                title_lbl = tk.Label(card, text=basename[:40], font=("Segoe UI Variable Display", 11), bg=self.BG_CARD, fg=self.FG_TEXT, wraplength=130, justify=tk.CENTER)
                title_lbl.pack(fill=tk.BOTH, expand=True, padx=4)

                cb = tk.Checkbutton(card, text="", variable=file_var, bg=self.BG_CARD, 
                                    selectcolor=self.BG_INPUT, activebackground=self.BG_CARD,
                                    command=self._recalculate)
                cb.place(x=4, y=4)
                
                file_frames.append(card)
                self._all_file_rows[path] = card
                
                if self.ffmpeg_path:
                    self._thumbnail_queue.put((path, img_lbl))

            self._folder_content_frames[folder_name] = file_frames

    def _toggle_folder(self, folder_name):
        """Collapse or expand a folder's track rows."""
        expanded = self._folder_expanded.get(folder_name, True)
        self._folder_expanded[folder_name] = not expanded

        if expanded:
            # Collapse: hide file rows
            if getattr(self, "view_mode", "LIST") == "LIST":
                for frame in self._folder_content_frames.get(folder_name, []):
                    frame.pack_forget()
            else:
                if folder_name in self._folder_grid_containers:
                    self._folder_grid_containers[folder_name].pack_forget()
            self._folder_toggle_btns[folder_name].configure(text="\u25B6")
        else:
            # Expand: trigger layout engine
            self._apply_search()
            self._folder_toggle_btns[folder_name].configure(text="\u25BC")

    def _on_folder_toggle(self, folder_name):
        """When a folder checkbox is toggled, cascade to all its children."""
        val = self._folder_vars[folder_name].get()
        for path in self._folder_file_paths.get(folder_name, []):
            self._file_vars[path].set(val)
        self._recalculate()

    def _apply_search(self):
        """Filter the visible folders and tracks based on search text."""
        query = self._search_var.get().strip().lower()
        view_mode = getattr(self, "view_mode", "LIST")

        # Step 1: Hide EVERYTHING first to reset layout order
        for folder_name in self._folder_file_paths:
            header = self._folder_header_frames.get(folder_name)
            if header:
                header.pack_forget()
            if view_mode == "LIST":
                for frame in self._folder_content_frames.get(folder_name, []):
                    frame.pack_forget()
            else:
                if folder_name in self._folder_grid_containers:
                    self._folder_grid_containers[folder_name].pack_forget()
                for frame in self._folder_content_frames.get(folder_name, []):
                    frame.grid_forget()

        # Step 2: Re-pack in correct order, applying filter
        for folder_name in sorted(self._folder_file_paths.keys()):
            header = self._folder_header_frames.get(folder_name)
            file_frames = self._folder_content_frames.get(folder_name, [])
            paths = self._folder_file_paths.get(folder_name, [])

            # Check if folder name matches
            folder_match = query in folder_name.lower() if query else False

            # Check which individual files match
            matching_indices = []
            if query:
                for i, path in enumerate(paths):
                    basename = os.path.basename(path).lower()
                    if folder_match or query in basename:
                        matching_indices.append(i)
            else:
                matching_indices = list(range(len(paths)))

            if not query and not self._folder_expanded.get(folder_name, True):
                # Folder is collapsed and no search query: show only header
                if header:
                    header.pack(fill=tk.X, padx=2, pady=(4, 0))
                continue

            # Need to display items
            if folder_match or matching_indices:
                if header:
                    header.pack(fill=tk.X, padx=2, pady=(4, 0))
                
                # Show matching files (override collapse during search)
                visible_frames = [f for i, f in enumerate(file_frames) if (not query) or folder_match or (i in matching_indices)]
                
                if view_mode == "LIST":
                    for frame in visible_frames:
                        frame.pack(fill=tk.X, padx=2)
                else: # GRID
                    grid_container = self._folder_grid_containers.get(folder_name)
                    if grid_container:
                        grid_container.pack(fill=tk.X, padx=8)
                        for idx, frame in enumerate(visible_frames):
                            frame.grid(row=idx//5, column=idx%5, padx=6, pady=8)
            # else: folder stays hidden (already pack_forget'd)

    def _toggle_view_mode(self):
        """Switch between List View and Grid View."""
        if getattr(self, "view_mode", "LIST") == "LIST":
            self.view_mode = "GRID"
        else:
            self.view_mode = "LIST"
        # Layout depends on full recreation
        self._populate_tree()

    def _select_all_new(self):
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")
        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showinfo("Select Drive", "Please select a valid iPod drive first to scan for existing files.")
            return

        existing = scan_ipod_existing(ipod_path)
        new_count = 0

        for folder_name, paths in self._folder_file_paths.items():
            folder_has_new = False
            for path in paths:
                finfo = self._file_map.get(path)
                if not finfo:
                    continue
                folder = finfo["folder"]
                basename = os.path.splitext(os.path.basename(path))[0]
                key = get_ipod_safe_key(folder, basename)

                if key in existing:
                    self._file_vars[path].set(False)
                else:
                    self._file_vars[path].set(True)
                    folder_has_new = True
                    new_count += 1

            self._folder_vars[folder_name].set(folder_has_new)

        self._log(f"Select All New: {new_count} new track(s) selected")
        self._recalculate()

    # ── State Persistence ────────────────────────────────────────────────
        
    def _save_config(self):
        unchecked = []
        try:
            for path, var in self._file_vars.items():
                if not var.get():
                    unchecked.append(path)
        except Exception:
            pass

        config = {
            "music_folder": self.music_var.get(),
            "ipod_drive_label": self.drive_var.get(),
            "format": self.format_var.get(),
            "bitrate": self.bitrate_var.get(),
            "convert_all": self.convert_all_var.get(),
            "voiceover": self.voiceover_var.get(),
            "unchecked": unchecked
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def _load_config(self):
        if not os.path.isfile(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
            
            if "format" in config and config["format"] in self.FORMATS:
                self.format_var.set(config["format"])
            if "bitrate" in config and config["bitrate"] in self.BITRATES:
                self.bitrate_var.set(config["bitrate"])
            if "convert_all" in config:
                self.convert_all_var.set(config["convert_all"])
            if "voiceover" in config:
                self.voiceover_var.set(config["voiceover"])
            
            # Select drive
            label = config.get("ipod_drive_label", "")
            if label and label in self.drive_combo["values"]:
                self.drive_var.set(label)

            # Load music folder
            mfolder = config.get("music_folder", "")
            if mfolder and os.path.isdir(mfolder):
                self.music_var.set(mfolder)
                self._populate_tree()

                # Restore unchecked items
                unchecked_set = set(config.get("unchecked", []))
                if unchecked_set:
                    for path in unchecked_set:
                        if path in self._file_vars:
                            self._file_vars[path].set(False)
                    # Update folder checkboxes
                    for folder_name, paths in self._folder_file_paths.items():
                        all_unchecked = all(not self._file_vars[p].get() for p in paths)
                        if all_unchecked:
                            self._folder_vars[folder_name].set(False)

            self._recalculate()
        except Exception as e:
            print(f"Failed to load config: {e}")

    def _on_closing(self):
        """Handle window close event."""
        self._thumbnail_thread_running = False
        self._save_config()
        self.root.destroy()

    # ── Space Calculation ────────────────────────────────────────────────

    def _recalculate(self):
        """Recalculate space estimates whenever inputs change."""
        music_path = self.music_var.get()
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        self._source_files = []
        try:
            for path, var in self._file_vars.items():
                if var.get() and path in self._file_map:
                    self._source_files.append(self._file_map[path])
        except Exception:
            pass

        num_files = len(self._source_files)
        source_total = sum(f["size"] for f in self._source_files)

        # Count playlists (unique folder names)
        folders = set(f["folder"] for f in self._source_files if f["folder"])
        num_playlists = 1 + len(folders)  # master + folders

        # Estimate output size — only for NEW files not already on iPod
        bitrate = int(self.bitrate_var.get())
        fmt = self.format_var.get()
        convert_all = self.convert_all_var.get()

        # Scan what's already on the iPod to identify new vs existing
        if ipod_path and os.path.isdir(ipod_path) and not convert_all:
            existing = scan_ipod_existing(ipod_path)
        else:
            existing = {}

        new_files = []
        existing_count = 0
        for f in self._source_files:
            folder = f["folder"]
            basename = os.path.splitext(os.path.basename(f["path"]))[0]
            key = get_ipod_safe_key(folder, basename)
            if key in existing and not convert_all:
                existing_count += 1
            else:
                new_files.append(f)

        self._new_file_count = len(new_files)
        self._existing_count = existing_count

        if self.ffmpeg_path and (convert_all or any(f["ext"] not in IPOD_COMPATIBLE for f in new_files)):
            self._estimated_size = sum(
                estimate_transcoded_size(f, bitrate, fmt, convert_all) for f in new_files
            )
        else:
            self._estimated_size = sum(f["size"] for f in new_files)

        # iPod free space
        if ipod_path and os.path.isdir(ipod_path):
            total_disk, used_disk, free_disk = get_disk_usage(ipod_path)
        else:
            total_disk, used_disk, free_disk = 0, 0, 0

        remaining = free_disk - self._estimated_size
        fits = remaining >= 0

        # Update labels
        self._lbl_source_size.configure(text=format_size(source_total) if num_files else "--")
        new_ct = getattr(self, '_new_file_count', 0)
        exist_ct = getattr(self, '_existing_count', 0)
        self._lbl_file_count.configure(text=f"{num_files} ({new_ct} new, {exist_ct} on iPod)" if num_files else "--")
        self._lbl_estimated_size.configure(text=f"~{format_size(self._estimated_size)} to transfer" if new_ct else ("0 B (all synced)" if num_files else "--"))
        self._lbl_playlist_count.configure(text=str(num_playlists) if num_files else "--")
        self._lbl_free_space.configure(text=format_size(free_disk) if total_disk else "--")
        self._lbl_remaining.configure(
            text=f"~{format_size(remaining)}" if (total_disk and num_files) else "--"
        )
        if total_disk and num_files:
            self._lbl_remaining.configure(fg=self.SUCCESS if fits else self.ERROR)

        # Space bar
        if total_disk > 0 and num_files > 0:
            used_pct = ((used_disk + self._estimated_size) / total_disk) * 100
            self.space_pct_var.set(min(used_pct, 100))
            self.space_bar.configure(style="space.Horizontal.TProgressbar" if fits else "spacewarn.Horizontal.TProgressbar")
            self.space_status_label.configure(
                text=f"{used_pct:.0f}% of iPod used after sync" if fits else "NOT ENOUGH SPACE!",
                fg=self.FG_DIM if fits else self.ERROR
            )
        else:
            self.space_pct_var.set(0)
            self.space_status_label.configure(text="", fg=self.FG_DIM)

        # Enable/disable sync button
        can_sync = num_files > 0 and ipod_path and fits
        self.sync_btn.configure(state=tk.NORMAL if can_sync else tk.DISABLED)

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_progress(self, current, total, phase=""):
        if total > 0:
            pct = (current / total) * 100
            self.progress_var.set(pct)
            self.progress_label.configure(text=f"{phase}: {current}/{total}")
        self.root.update_idletasks()

    # ── Sync Action ──────────────────────────────────────────────────────

    def _start_sync(self):
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showwarning("No Drive", "Please select a valid iPod drive.")
            return
        if not self._source_files:
            messagebox.showwarning("No Music", "Please select a music folder with audio files.")
            return

        new_ct = getattr(self, '_new_file_count', len(self._source_files))
        convert_all = self.convert_all_var.get()

        if convert_all:
            msg = (f"Full re-encode mode: this will clear all music on the iPod and "
                   f"re-sync {len(self._source_files)} tracks.\n\nContinue?")
        elif new_ct == 0:
            msg = "All files are already on the iPod. Only the database will be rebuilt.\n\nContinue?"
        else:
            msg = (f"{new_ct} new file(s) will be copied to the iPod "
                   f"({format_size(self._estimated_size)}).\n"
                   f"{getattr(self, '_existing_count', 0)} file(s) already on iPod will be kept.\n\nContinue?")

        if not messagebox.askyesno("Confirm Sync", msg):
            return

        self._clear_log()
        self.progress_var.set(0)
        self.sync_btn.configure(state=tk.DISABLED)
        self.rebuild_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Syncing...", style="Subtitle.TLabel")

        target_format = self.format_var.get()
        target_bitrate = int(self.bitrate_var.get())
        convert_all = self.convert_all_var.get()
        voiceover_enabled = self.voiceover_var.get()

        def run():
            def log_cb(msg):
                self.root.after(0, self._log, msg)

            def progress_cb(cur, tot, phase):
                self.root.after(0, self._set_progress, cur, tot, phase)

            success, summary = sync_to_ipod(
                ipod_path, self._source_files,
                target_format, target_bitrate, convert_all,
                self.ffmpeg_path, voiceover_enabled, log_cb, progress_cb
            )

            def finish():
                self.sync_btn.configure(state=tk.NORMAL)
                self.rebuild_btn.configure(state=tk.NORMAL)
                if success:
                    self.progress_var.set(100)
                    self._log("")
                    self._log("=" * 50)
                    self._log(f"  Sync complete!")
                    self._log(f"  Tracks: {summary['tracks']}")
                    self._log(f"  Playlists: {summary['playlists']}")
                    for name in summary.get("playlist_names", []):
                        self._log(f"    - {name}")
                    if summary["errors"]:
                        self._log(f"  Errors: {len(summary['errors'])}")
                        for e in summary["errors"][:5]:
                            self._log(f"    ! {e}")
                    self._log("=" * 50)
                    self.status_label.configure(
                        text="\u2713  READY TO EJECT \u2014 Safe to disconnect your iPod.",
                        style="Status.TLabel")
                    self.progress_label.configure(text="Complete!")
                    self._recalculate()
                else:
                    self.status_label.configure(text="\u2717  Sync failed. Check log for details.",
                                                style="Subtitle.TLabel")
                    self.progress_label.configure(text="Failed")

            self.root.after(0, finish)

        threading.Thread(target=run, daemon=True).start()

    # ── Rebuild DB Only ──────────────────────────────────────────────────

    def _start_rebuild_only(self):
        """Rebuild database from files already on the iPod (Part 2 behavior)."""
        drive_sel = self.drive_var.get()
        ipod_path = self._drive_paths.get(drive_sel, "")

        if not ipod_path or not os.path.isdir(ipod_path):
            messagebox.showwarning("No Drive", "Please select a valid iPod drive.")
            return

        self._clear_log()
        self.progress_var.set(0)
        self.sync_btn.configure(state=tk.DISABLED)
        self.rebuild_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Rebuilding database...", style="Subtitle.TLabel")

        def run():
            def log_cb(msg):
                self.root.after(0, self._log, msg)
            def progress_cb(cur, tot, phase=""):
                self.root.after(0, self._set_progress, cur, tot, phase)

            # Scan files already on iPod
            log_cb("Scanning iPod for existing audio files...")
            all_tracks = []
            playlists = {}
            scan_dirs = [
                os.path.join(ipod_path, "Music"),
                os.path.join(ipod_path, "iPod_Control", "Music"),
            ]
            for sd in scan_dirs:
                if not os.path.isdir(sd):
                    continue
                for root, dirs, files in os.walk(sd):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for fname in sorted(files, key=lambda x: x.lower()):
                        if fname.startswith('.'):
                            continue
                        ext = os.path.splitext(fname)[1].lower()
                        full = os.path.join(root, fname)
                        if ext in AUDIO_EXTENSIONS:
                            rel = os.path.relpath(full, ipod_path)
                            ipod_rel = "/" + rel.replace("\\", "/")
                            all_tracks.append(ipod_rel)
                            rel_to_sd = os.path.relpath(root, sd)
                            if rel_to_sd != ".":
                                folder = rel_to_sd.split(os.sep)[0]
                                if folder not in playlists:
                                    playlists[folder] = []
                                playlists[folder].append(ipod_rel)

            all_tracks.sort(key=lambda x: os.path.basename(x).lower())

            if not all_tracks:
                log_cb("ERROR: No audio files found on iPod.")
                self.root.after(0, lambda: self.sync_btn.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.rebuild_btn.configure(state=tk.NORMAL))
                return

            # Clean legacy files
            itunes_dir = os.path.join(ipod_path, "iPod_Control", "iTunes")
            os.makedirs(itunes_dir, exist_ok=True)
            for f in ["iTunesDB", "iTunesPrefs", "iTunesPrefs.plist", "iTunesControl", "iTunesStats", "iTunesPState"]:
                fp = os.path.join(itunes_dir, f)
                if os.path.exists(fp):
                    try: os.remove(fp)
                    except: pass

            # Build database
            num_tracks = len(all_tracks)
            num_playlists_total = 1 + len(playlists)
            log_cb(f"Found {num_tracks} tracks, {len(playlists)} folder playlist(s)")

            tunessd = TunesSD(num_tracks, num_playlists_total)
            track_header = TrackHeader(num_tracks)
            track_header_offset = 64
            track_header_bytes = track_header.construct()
            tracks_chunk = b""
            track_index_map = {}

            for i, ipod_path_str in enumerate(all_tracks):
                track = Track()
                track.populate(ipod_path_str)
                ptr_offset = track_header_offset + 20 + (num_tracks * 4) + len(tracks_chunk)
                track_header_bytes += struct.pack("<I", ptr_offset)
                tracks_chunk += track.construct()
                track_index_map[ipod_path_str] = i
                progress_cb(i + 1, num_tracks, "Indexing")

            full_track_segment = track_header_bytes + tracks_chunk
            playlist_header_offset = track_header_offset + len(full_track_segment)
            tunessd["playlist_header_offset"] = playlist_header_offset

            play_header = PlaylistHeader(num_playlists_total)
            play_header_base = play_header.construct()
            playlist_chunks = []

            master = Playlist(num_tracks, listtype=1)
            master_data = master.construct()
            for i in range(num_tracks):
                master_data += struct.pack("<I", i)
            playlist_chunks.append(master_data)

            for folder_name in sorted(playlists.keys()):
                indices = [track_index_map[t] for t in playlists[folder_name] if t in track_index_map]
                if not indices: continue
                dbid = hashlib.md5(folder_name.encode('utf-8')).digest()[:8]
                pl = Playlist(len(indices), listtype=2, dbid=dbid)
                pl_data = pl.construct()
                for idx in indices:
                    pl_data += struct.pack("<I", idx)
                playlist_chunks.append(pl_data)
                log_cb(f"  Playlist '{folder_name}': {len(indices)} tracks")

            actual_pl = len(playlist_chunks)
            if actual_pl != num_playlists_total:
                tunessd = TunesSD(num_tracks, actual_pl)
                tunessd["playlist_header_offset"] = playlist_header_offset
                play_header = PlaylistHeader(actual_pl)
                play_header_base = play_header.construct()

            ph_total_len = 20 + (actual_pl * 4)
            cur_off = playlist_header_offset + ph_total_len
            ph_with_ptrs = play_header_base
            for chunk in playlist_chunks:
                ph_with_ptrs += struct.pack("<I", cur_off)
                cur_off += len(chunk)

            full_pl_seg = ph_with_ptrs + b"".join(playlist_chunks)
            final_db = tunessd.construct() + full_track_segment + full_pl_seg

            try:
                with open(os.path.join(itunes_dir, "iTunesSD"), "wb") as f:
                    f.write(final_db)
                log_cb(f"Database written: {format_size(len(final_db))}")
            except Exception as e:
                log_cb(f"ERROR: {e}")
                self.root.after(0, lambda: self.sync_btn.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.rebuild_btn.configure(state=tk.NORMAL))
                return

            def finish():
                self.sync_btn.configure(state=tk.NORMAL)
                self.rebuild_btn.configure(state=tk.NORMAL)
                self.progress_var.set(100)
                self._log(f"\nDatabase rebuilt: {num_tracks} tracks, {actual_pl} playlists")
                self.status_label.configure(
                    text="\u2713  READY TO EJECT \u2014 Safe to disconnect your iPod.",
                    style="Status.TLabel")
                self.progress_label.configure(text="Complete!")

            self.root.after(0, finish)

        threading.Thread(target=run, daemon=True).start()

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = AntigravityApp()
    app.run()
