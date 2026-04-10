"""
iPod Music Manager — Sync Engine
Incremental sync pipeline: scan, transcode/copy, build database, generate VoiceOver.
"""

import os
import sys
import hashlib
import shutil
import subprocess
import threading
import concurrent.futures

from utils import (IPOD_COMPATIBLE, format_size,
                   scan_ipod_existing)
from database import build_itunes_db
from voiceover import build_voiceover


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
            except Exception:
                pass

    log("Scanning iPod for existing files...")
    existing_on_ipod = scan_ipod_existing(ipod_path)
    log(f"  Found {len(existing_on_ipod)} existing track(s) on iPod")

    if convert_all:
        log("Re-encode all: all files will be transcoded (existing files will be overwritten)...")

    # Phase 2: Copy/Transcode only NEW files
    out_ext = ".mp3" if "MP3" in target_format else ".m4a"
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
            dest_subfolder = "F_" + hashlib.md5(dest_subfolder.encode('utf-8', 'ignore')).hexdigest()[:8]
            
        dest_dir = os.path.join(music_dest, dest_subfolder)

        needs_transcode = bool(ffmpeg_path)
        
        safe_basename = basename
        if not is_ascii(safe_basename):
            safe_basename = "T_" + hashlib.md5(safe_basename.encode('utf-8', 'ignore')).hexdigest()[:8]
            
        if needs_transcode:
            out_filename = safe_basename + out_ext
        else:
            # No ffmpeg: can only copy iPod-compatible files as-is
            if ext not in IPOD_COMPATIBLE:
                with lock:
                    errors.append(f"{basename}: No ffmpeg to transcode {ext}")
                log(f"  \u2718 Skipped (no ffmpeg): {basename}{ext}")
                return None, folder, basename
            out_filename = safe_basename + ext

        ipod_rel = "/iPod_Control/Music/" + dest_subfolder + "/" + out_filename

        match_key = (dest_subfolder.lower(), safe_basename.lower())
        if match_key in existing_on_ipod and not convert_all:
            with lock:
                skipped_count += 1
            res_ipod_rel = existing_on_ipod[match_key]
        else:
            os.makedirs(dest_dir, exist_ok=True)
            if needs_transcode:
                dest_file = os.path.join(dest_dir, out_filename)
                
                # Show what's happening
                with lock:
                    if total < 100 or processed_count % max(1, total // 50) == 0:
                        log(f"  \u2699 Transcoding \u2192 {out_filename}")

                try:
                    cmd = [ffmpeg_path, "-y", "-i", src]
                    if target_format == "MP3 (CBR)":
                        cmd += ["-codec:a", "libmp3lame", "-ar", "44100", "-ac", "2", "-b:a", f"{target_bitrate}k", "-threads", "1"]
                    elif target_format == "AAC (CBR)":
                        cmd += ["-codec:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", f"{target_bitrate}k", "-threads", "1"]
                    else:
                        # AAC (VBR Optimized): map kbps to ffmpeg AAC -q:a target (0.1 to 2.0).
                        vbr_target = str(round(int(target_bitrate) / 100.0, 2))
                        cmd += ["-codec:a", "aac", "-ar", "44100", "-ac", "2", "-q:a", vbr_target, "-threads", "1"]
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

    workers = min(4, os.cpu_count() or 4)
    original_titles = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_file, f) for f in source_files]
        for future in futures:
            try:
                result = future.result()
            except Exception as e:
                log(f"  ✘ Unexpected error: {e}")
                errors.append(str(e))
                continue
            if result is None:
                continue
            res_ipod_rel, folder, basename = result
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
    final_db, num_tracks, num_playlists, track_dbids, playlist_dbids = build_itunes_db(
        all_ipod_tracks, playlists, voiceover_enabled=voiceover_enabled,
        original_titles=original_titles, progress_cb=progress, log_cb=log
    )

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
        vo_size = build_voiceover(ipod_path, track_dbids, playlist_dbids, ffmpeg_path=ffmpeg_path, log_cb=log)

    summary = {
        "tracks": num_tracks,
        "playlists": num_playlists,
        "errors": errors,
        "playlist_names": sorted(playlists.keys()),
        "db_size": len(final_db),
        "voiceover_size": vo_size,
    }
    return True, summary

