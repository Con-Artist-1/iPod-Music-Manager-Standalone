"""
iPod Music Manager — Sync Engine
Incremental sync pipeline: scan, transcode/copy, build database, generate VoiceOver.
"""

import os
import sys
import hashlib
import shutil
import subprocess
import tempfile
import threading
import queue
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
    2. Pre-sweep orphaned files to reclaim space
    3. Transcode NEW files locally on PC (parallel), then copy to iPod
    4. Rebuild iTunesSD database with ALL files (existing + new)
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

    # ── Phase 1.5: Pre-sweep orphaned files to free space before writing ──

    def is_ascii(s):
        return all(ord(c) < 128 for c in s)

    planned_matches = set()
    if not convert_all:
        for finfo in source_files:
            folder = finfo["folder"]
            basename = os.path.splitext(os.path.basename(finfo["path"]))[0]
            f_safe = folder if folder else "_root"
            if not is_ascii(f_safe):
                f_safe = "F_" + hashlib.md5(f_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
            b_safe = basename
            if not is_ascii(b_safe):
                b_safe = "T_" + hashlib.md5(b_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
            planned_matches.add((f_safe.lower(), b_safe.lower()))

    pre_sweep_count = 0
    for key, rel_path in list(existing_on_ipod.items()):
        if key not in planned_matches:
            full_remove = os.path.normpath(os.path.join(ipod_path, rel_path.lstrip("/")))
            try:
                os.remove(full_remove)
                pre_sweep_count += 1
                del existing_on_ipod[key]
            except Exception:
                pass
    if pre_sweep_count > 0:
        log(f"  Cleaned up {pre_sweep_count} obsolete track(s) from iPod to free space.")

    # Sweep empty folders
    try:
        if os.path.isdir(music_dest):
            for d in os.listdir(music_dest):
                dp = os.path.join(music_dest, d)
                if os.path.isdir(dp) and not os.listdir(dp):
                    os.rmdir(dp)
    except Exception:
        pass

    # ── Phase 2: Transcode locally on PC, then copy to iPod ──────────────

    out_ext = ".mp3" if "MP3" in target_format else ".m4a"
    total = len(source_files)
    all_ipod_tracks = []
    playlists = {}
    errors = []
    copied_count = 0
    skipped_count = 0
    disk_full = False

    lock = threading.Lock()
    processed_count = 0

    # Calculate copy targets upfront for accurate progress
    copy_target = 0
    for finfo in source_files:
        f = finfo["folder"] if finfo["folder"] else "_root"
        b = os.path.splitext(os.path.basename(finfo["path"]))[0]
        f_s = f if is_ascii(f) else "F_" + hashlib.md5(f.encode('utf-8', 'ignore')).hexdigest()[:8]
        b_s = b if is_ascii(b) else "T_" + hashlib.md5(b.encode('utf-8', 'ignore')).hexdigest()[:8]
        if convert_all or (f_s.lower(), b_s.lower()) not in existing_on_ipod:
            copy_target += 1

    copy_queue = queue.Queue()

    # Create a temporary directory on the local PC for transcoding
    temp_dir = tempfile.mkdtemp(prefix="ipod_sync_")
    log(f"  Using local temp directory for transcoding: {temp_dir}")
    
    original_titles = {}

    def copy_worker():
        nonlocal copied_count, disk_full
        copies_done = 0
        while True:
            item = copy_queue.get()
            if item is None:
                copy_queue.task_done()
                break

            temp_path, ipod_rel, folder, basename = item
            
            try:
                free = shutil.disk_usage(ipod_path).free
                file_size = os.path.getsize(temp_path)
                if free < file_size + (5 * 1024 * 1024):
                    with lock:
                        errors.append(f"{basename}: Not enough space on iPod")
                    log(f"  \u2718 Skipped (disk full): {basename}")
                    disk_full = True
                    copy_queue.task_done()
                    continue
            except Exception:
                pass

            dest_full = os.path.normpath(os.path.join(ipod_path, ipod_rel.lstrip("/")))
            os.makedirs(os.path.dirname(dest_full), exist_ok=True)

            try:
                shutil.copy2(temp_path, dest_full)
                with lock:
                    copied_count += 1
                    all_ipod_tracks.append(ipod_rel)
                    original_titles[ipod_rel] = basename
                    if folder:
                        if folder not in playlists:
                            playlists[folder] = []
                        playlists[folder].append(ipod_rel)
            except Exception as e:
                with lock:
                    errors.append(f"{basename}: {e}")
                log(f"  \u2718 Error copying to iPod: {basename}")

            copies_done += 1
            if copy_target > 0:
                progress(copies_done, copy_target, "Copying")
                
            copy_queue.task_done()

    # Start independent consumer thread for USB stream
    consumer = threading.Thread(target=copy_worker, daemon=True)
    consumer.start()

    def process_file(finfo):
        """Transcode/prepare a single file and feed to copy queue."""
        nonlocal processed_count, skipped_count
        src = finfo["path"]
        ext = finfo["ext"]
        folder = finfo["folder"]
        basename = os.path.splitext(os.path.basename(src))[0]

        dest_subfolder = folder if folder else "_root"
        if not is_ascii(dest_subfolder):
            dest_subfolder = "F_" + hashlib.md5(dest_subfolder.encode('utf-8', 'ignore')).hexdigest()[:8]

        safe_basename = basename
        if not is_ascii(safe_basename):
            safe_basename = "T_" + hashlib.md5(safe_basename.encode('utf-8', 'ignore')).hexdigest()[:8]

        needs_transcode = bool(ffmpeg_path)

        if needs_transcode:
            out_filename = safe_basename + out_ext
        else:
            if ext not in IPOD_COMPATIBLE:
                with lock:
                    errors.append(f"{basename}: No ffmpeg to transcode {ext}")
                log(f"  \u2718 Skipped (no ffmpeg): {basename}{ext}")
                return
            out_filename = safe_basename + ext

        ipod_rel = "/iPod_Control/Music/" + dest_subfolder + "/" + out_filename

        match_key = (dest_subfolder.lower(), safe_basename.lower())
        if match_key in existing_on_ipod and not convert_all:
            with lock:
                processed_count += 1
                skipped_count += 1
                all_ipod_tracks.append(existing_on_ipod[match_key])
                original_titles[existing_on_ipod[match_key]] = basename
                if folder:
                    if folder not in playlists:
                        playlists[folder] = []
                    playlists[folder].append(existing_on_ipod[match_key])
                progress(processed_count, total, "Transcoding")
            return

        if needs_transcode:
            temp_subfolder = os.path.join(temp_dir, dest_subfolder)
            os.makedirs(temp_subfolder, exist_ok=True)
            temp_file = os.path.join(temp_subfolder, out_filename)

            with lock:
                if total < 100 or processed_count % max(1, total // 50) == 0:
                    log(f"  \u2699 Transcoding \u2192 {out_filename}")

            try:
                cmd = [ffmpeg_path, "-y", "-i", src]
                if target_format == "MP3 (CBR)":
                    cmd += ["-codec:a", "libmp3lame", "-ar", "44100", "-ac", "2", "-b:a", f"{target_bitrate}k"]
                elif target_format == "AAC (CBR)":
                    cmd += ["-codec:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", f"{target_bitrate}k"]
                else:
                    vbr_target = str(min(round(int(target_bitrate) / 140.0, 2), 2.0))
                    cmd += ["-codec:a", "aac", "-ar", "44100", "-ac", "2", "-q:a", vbr_target]
                cmd += ["-map_metadata", "-1", "-vn", temp_file]

                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace',
                                        timeout=7200, creationflags=creationflags)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr[:200])

                copy_queue.put((temp_file, ipod_rel, folder, basename))

            except Exception as e:
                with lock:
                    errors.append(f"{basename}: {e}")
                log(f"  \u2718 Error transcoding: {basename}")
        else:
            copy_queue.put((src, ipod_rel, folder, basename))

        with lock:
            processed_count += 1
            progress(processed_count, total, "Transcoding")

    # Run transcoding in parallel using all available CPU cores
    cpu_count = os.cpu_count() or 4
    workers = max(2, min(cpu_count, 8))
    log(f"Phase 2: Streaming Transcodes ({workers} threads) \u2192 iPod Writer...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_file, f) for f in source_files]
        for future in futures:
            try:
                future.result()
            except Exception as e:
                with lock:
                    errors.append(str(e))

    # Send poison pill and wait for remaining copies to flush to iPod
    copy_queue.put(None)
    consumer.join()

    # Clean up temp directory
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    log(f"  Copied {copied_count} new, skipped {skipped_count} existing ({len(errors)} errors)")

    if disk_full:
        log("  \u26a0 Some files were skipped because the iPod ran out of space.")

    if not all_ipod_tracks:
        log("ERROR: No tracks available for database.")
        return False, {"tracks": 0, "playlists": 0, "errors": errors}

    # Post-sweep: remove files on iPod that are no longer in the track list
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

    # ── Phase 3: Build database ──────────────────────────────────────────

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

    # ── Phase 4: VoiceOver generation ────────────────────────────────────

    vo_size = 0
    if voiceover_enabled:
        log("Phase 4: Generating VoiceOver audio...")
        vo_size = build_voiceover(ipod_path, track_dbids, playlist_dbids, ffmpeg_path=ffmpeg_path, log_cb=log, progress_cb=progress)

    summary = {
        "tracks": num_tracks,
        "playlists": num_playlists,
        "errors": errors,
        "playlist_names": sorted(playlists.keys()),
        "db_size": len(final_db),
        "voiceover_size": vo_size,
    }
    return True, summary
