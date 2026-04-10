# AI Memory Bank: iPod Music Manager

> **Purpose:** This file acts as persistent context and architectural mapping for AI agents (like Claude, GPT, or Gemini) working on this codebase. Read this before making structural changes.

## System Architecture

The project is a standalone Python application that synchronizes music to an iPod Shuffle 4G, building the internal `iTunesSD` binary database and generating `.wav` VoiceOver files so the device operates exactly as if synced with iTunes, but cross-platform and without iTunes.

### Code Organization (`src/`)

- **`main.py`**: The application entry point. Simply initializes `AntigravityApp` from `ui_app.py` and runs it.
- **`database.py`**: Parses and constructs the Little-Endian binary structs (`iTunesSD`, `Track`, `Playlist`) used by the iPod database.
- **`sync_engine.py`**: The core orchestration pipeline. Handles multithreaded scanning of new/existing files, routes audio conversions via `ffmpeg`, copies valid files to `iPod_Control/Music/`, and triggers database regeneration.
- **`voiceover.py`**: Connects to `gTTS` to lazily generate localized text-to-speech WAV tracks for song and playlist names. Falls back to Windows SAPI (PowerShell) if offline.
- **`ui_app.py`**: The Tkinter-based monolithic UI. Controls view state (List vs. Grid), configuration loading/saving, asynchronous thumbnail generation, and debounced window resizing to prevent lag.
- **`ui_theme.py`**: Contains the `COLORS` definitions (neon-purple glassmorphism theme) and initializes custom `ttk` styling tokens. Also houses the `ToolTip` class.
- **`utils.py`**: Statics, external executable fetchers (like finding `ffmpeg.exe`), safe file name generators, and size conversion math.

### File Locations & External Deps
- **`ffmpeg.exe`**: We rely on this executable for transcoding (FLAC/MP3/M4A) and extracting album thumbnails (PPM format) directly into memory. It is heavily ignored in source control (due to ~100MB size) but fetched automatically in deployment actions via `scripts/download_ffmpeg.py`.
- **`.ipod_manager_config.json`**: Generated automatically in the user's home directory (`~/.ipod_manager_config.json`). It persists the last used music path, bitrate selections, and an array of `collapsed_folders` for the UI state.

## CI/CD Pipeline (GitHub Actions)

We utilize a GitHub Action in `.github/workflows/build-release.yml` with two primary trigger paths:
1. `workflow_dispatch`: Manually triggered from the action tab. Builds the `.exe` via PyInstaller (using `ipod_music_manager.spec`) and attaches it securely as a temporary workflow artifact for testing.
2. `push (tags: v*)`: When a git tag is explicitly pushed (e.g. `git tag v1.0.0`), it compiles and forcefully pushes it into a hardened **GitHub Release**.

### Important Git & PyInstaller Notes
- The PyInstaller target is explicitly set to `src/main.py`.
- `ffmpeg.exe`, `build/`, `dist/`, and compiled caches are rigidly excluded from Git tracking via `.gitignore`.
- PyInstaller `.spec` files (with the exception of our configuration specification script `ipod_music_manager.spec`) are blocked.

### GUI Guidelines (Glassmorphism)
- The application implements debounce wrappers around its `<Configure>` Tkinter events (throttled locally near 16ms delays) for Canvas objects because cascading redraws in Python Tkinter are intensely slow natively. DO NOT bind standard `<Configure>` actions directly to scale logic without clearing the previous resize schedule pointer via `root.after_cancel()`.
- The theme emphasizes absolute `#0D0B14` bounds with `#B388FF` high voltage elements. Keep aesthetic adherence strict.
