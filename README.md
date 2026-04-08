# ⬡ Antigravity

**A standalone iPod Shuffle 4th Generation sync manager for Windows — no iTunes required.**

Antigravity is a zero-dependency, self-contained tool that lets you manage music on your iPod Shuffle 4G directly. It builds the proprietary `iTunesSD` database from scratch, handles audio transcoding, generates VoiceOver speech files, and provides a modern dark-themed GUI — all without iTunes, iCloud, or any Apple software.

---

## Features

- **Two Viewing Modes** — Toggle instantly between an ultra-fast raw List View and a rich, Spotify-style Grid View displaying 150x150 extracting album art thumbnails natively.
- **Multilingual VoiceOver AI** — Dynamically detects CJK (Chinese, Japanese, Korean) characters and flawlessly pipes them through Google's TTS API for high fidelity speech online. Falls back gracefully to Windows SAPI offline.
- **Universal Character Syncing** — Employs a deterministic path-obfuscator system (`F_<hash>`) to guarantee special characters sync flawlessly to the iPod Hardware without skips, while maintaining full text metadata.
- **Incremental Sync & Cache Sweeper** — Only copies new/missing files. Any unselected tracks or abandoned folders are automatically swept off the iPod physical disk completely, recovering wasted storage natively on every sync!
- **Zero-Dependency Core** — Packaged single-executable workflow using python. Drag & Drop folder workflows.
- **Audio Transcoding** — Built-in ffmpeg integration converts FLAC, OGG, OPUS, WMA, AIFF and more into MP3 or AAC seamlessly.
- **Parallel Processing** — Multi-threaded copy/transcode engine for significantly faster syncs.
- **Space Dashboard & Search** — Search playlists instantly, track outputs and verify exact final free sizes before pressing sync.

## Requirements

- **Windows 10/11** (uses Windows SAPI for VoiceOver, dark title bar API)
- **Python 3.10+** (if running from source)
- **ffmpeg** (optional, for transcoding — place `ffmpeg.exe` in PATH or alongside `antigravity.py`)

## Quick Start

### From the compiled executable
1. Run `antigravity_latest.exe` located inside the `releases/` folder
2. Plug in your iPod Shuffle 4G
3. Run `antigravity.exe`
4. Select your iPod drive and music folder
5. Check/uncheck tracks and playlists
6. Hit **▶ Sync to iPod**

### From source
```bash
pip install pyinstaller  # only needed for building
python antigravity.py
```

### Building the executable
```bash
pyinstaller --clean -y antigravity.spec
```
The output will natively compile into the `dist/` directory, which you can simply move into the `releases/` folder.

## Repository Structure

- `releases/`: Contains the pre-compiled standalone `.exe` binaries for immediate execution without Python.
- `archive/`: Stores the old prototype `.py` files and tests from previous development sessions.
- `tests/mock_data/`: Holds the mock iPod structures (like `iPod_Control`) used for local offline development.
- `antigravity.py`: The main, self-contained Python source code.

## How It Works

Antigravity reverse-engineers the iPod Shuffle 4G's `iTunesSD` binary database format:

| Component | Description |
|-----------|-------------|
| `bdhs` (TunesSD) | 64-byte header with track/playlist counts and VoiceOver flag |
| `hths` (TrackHeader) | Index of track pointers |
| `rths` (Track) | Per-track record with filename hash, start/stop times, and dbid |
| `hphs` (PlaylistHeader) | Index of playlist pointers |
| `lphs` (Playlist) | Playlist record with type, dbid, and track index list |

VoiceOver files are generated as WAV speech in `iPod_Control/Speakable/Tracks/` and `iPod_Control/Speakable/Playlists/`, keyed by the reversed hex of each item's 8-byte `dbid`.

## File Structure

```
iPod_Control/
├── iTunes/
│   └── iTunesSD          ← binary database (built by Antigravity)
├── Music/
│   ├── Mixed/            ← playlist folder = subfolder name
│   │   ├── song1.mp3
│   │   └── song2.mp3
│   └── ASMR/
│       └── track.m4a
└── Speakable/            ← VoiceOver audio (generated)
    ├── Tracks/
    │   └── <dbid_hex>.wav
    └── Playlists/
        └── <dbid_hex>.wav
```

## Configuration

Settings are saved to `~/.antigravity_config.json` and restored on launch:
- Music source folder path
- iPod drive selection
- Format (MP3/AAC) and bitrate
- VoiceOver toggle
- Per-file checked/unchecked state

## License

MIT License — free for personal and commercial use.

## Credits

Built with zero external dependencies beyond Python's standard library and optional ffmpeg.
