# Human Walkthrough: iPod Music Manager

Welcome to the internal guide for operating, building, and developing your iPod Music Manager application! This document is built for you (the human) to clearly understand what resources are operating under the hood, how standard tasks are executed, and how to safely trigger GitHub processes.

## 📁 Repository Map

The repository is organized professionally to keep your workspace clean:

- `/src/`: **The Application Brain.** 
   - Operations have been modularized. Instead of thousands of lines bunched into one file, your database writing (`database.py`), user interface (`ui_app.py`), the sync logic (`sync_engine.py`), TTS Voiceovers (`voiceover.py`), and the core styling palettes (`ui_theme.py`) are cleanly separated.
   - `main.py` is the trigger that turns the application on.
- `/scripts/`: **The Toolbox.**
   - These scripts aren't needed by the application itself but they help you setup your local workspace or automate GitHub. (`download_ffmpeg.py`, `convert_logo.py`).
- `ipod_music_manager.spec`: **The Builder Configuration.**
   - PyInstaller looks at this map to accurately bind all of the scripts inside `/src/` into the single `.exe` file you rely on.

## 🎵 How It Works

1. **State Tracking:** Every time you check, uncheck, or collapse a playlist within the UI list, that event dynamically writes out a hidden list (`collapsed_folders`) bound inside a locally persisted `.json` config file found at `~/.ipod_manager_config.json`. The next time you launch the `exe`, the system inherently remembers how your view was styled previously.
2. **The Sync Pipeline (`sync_engine.py`)**: 
   - The engine automatically checks your iPod for files already in its domain.
   - It invokes a parallel threaded routine to copy new files or convert FLAC/incompatibles using the bundled FFmpeg.
   - It then stitches `database.py` together to inject the cryptic data payloads directly into your iPod's `iTunesSD` data partition.
   - Finally, your playlist names textually compile via the `voiceover.py` gTTS bindings so the hardware voice correctly plays audio tags.

## 🚀 How to Manage GitHub Releases

I created a fully automated CI/CD pipeline using **GitHub Actions**. Here is exactly how to interact with it:

### 1. Generating a Silent Build (Checking errors)
If you just want to run a dry test to ensure PyInstaller compiles it appropriately without officially broadcasting it to the world:
1. Open the repository on GitHub.
2. Click the **"Actions"** tab.
3. Click **"Build and Release"** on the left menu.
4. On the right side, invoke **"Run workflow"** against the `main` branch.
5. In 3 minutes, click into the workflow report and you will see the `.exe` bound to the bottom as an "Artifact" you can download!

### 2. Publishing an Official Download Release (For the Public)
Let's say you added a brand new feature and want it stamped as an official release on your repository's homepage. The CI pipeline operates securely off **Git Tags**.
Instead of using the UI, go to your local terminal and simply increment your version code and aggressively push it up to GitHub.

```bash
git tag v1.1.0
git push origin v1.1.0
```

GitHub intercepts the tag, triggers the identical PyInstaller compilation pipeline, but this time redirects the output artifact perfectly to your **Releases** tab as `v1.1.0` allowing people to immediately download your new executable securely updated straight from source tracking!

## 🔧 Ignoring and Cleaning
The system strictly operates with `.gitignore` barriers. `ffmpeg.exe` is nearly 100MB and thus fundamentally barred from being tracked to GitHub. Furthermore, any `.exe` exports outputting locally in your `dist/` or `releases/` folder are hidden away by Git. Keep `ffmpeg.exe` living securely at the root layer for your local environment to test successfully.
