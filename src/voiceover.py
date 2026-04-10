"""
iPod Music Manager — VoiceOver Generation
Generates spoken WAV files for track/playlist names using gTTS or Windows SAPI fallback.
"""

import os
import sys
import subprocess
import wave

from utils import format_size


def dbid_to_hex_filename(dbid_bytes):
    """Convert 8-byte dbid to the hex filename format used by iPod Speakable."""
    return ''.join(format(b, '02X') for b in reversed(dbid_bytes))


def generate_voiceover_wav(out_path, text, ffmpeg_path=None):
    """Generate a spoken WAV file using gTTS (if online) or Windows SAPI via PowerShell."""
    if os.path.isfile(out_path):
        return "cached"

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
            if result.returncode == 0 and os.path.isfile(out_path):
                return "generated"
        except Exception:
            pass
        return "failed"

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
    return "generated"


def generate_silent_wav(out_path):
    """Generate a minimal silent WAV file as fallback."""
    if os.path.isfile(out_path):
        return
    # Minimal WAV: 44 byte header + 8000 samples of silence (0.5s at 16kHz mono)
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
    cached = 0
    failed = 0

    # Generate playlist voiceovers
    for folder_name, dbid in playlist_dbids.items():
        hex_name = dbid_to_hex_filename(dbid)
        wav_path = os.path.join(playlists_dir, hex_name + ".wav")
        res = generate_voiceover_wav(wav_path, folder_name, ffmpeg_path)
        if res == "cached": cached += 1
        elif res == "generated": generated += 1
        else:
            generate_silent_wav(wav_path)
            failed += 1
        if os.path.isfile(wav_path):
            total_size += os.path.getsize(wav_path)

    # Generate track voiceovers
    for ipod_rel, (dbid, display_name) in track_dbids.items():
        hex_name = dbid_to_hex_filename(dbid)
        wav_path = os.path.join(tracks_dir, hex_name + ".wav")
        res = generate_voiceover_wav(wav_path, display_name, ffmpeg_path)
        if res == "cached": cached += 1
        elif res == "generated": generated += 1
        else:
            generate_silent_wav(wav_path)
            failed += 1
        if os.path.isfile(wav_path):
            total_size += os.path.getsize(wav_path)

    log(f"  VoiceOver: {generated} generated, {cached} cached, {failed} fallback silent ({format_size(total_size)} total)")

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

