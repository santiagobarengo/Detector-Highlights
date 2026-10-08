# 🎮 Gaming Highlight Detector (FFmpeg + Python)

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Required-green.svg)](https://ffmpeg.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Spanish Version](https://img.shields.io/badge/Language-Español-red.svg)](README_ES.md)

> [!NOTE]
> Para leer la versión en español, consulta el archivo [README_ES.md](README_ES.md).

A high-performance, lightweight CLI tool written in Python to automatically scan long gaming video recordings (1–2+ hour MP4/MKV files) and extract highlights based on audio volume spikes (screams, clutch plays, hype reactions, and victory cheers).

---

## ⚡ Highlights & Key Features

- **Blazing Fast Scanning (300x – 600x realtime):** Analyzes the audio stream without decoding video frames (`-vn` in FFmpeg). A 2-hour gameplay video is processed in just a few seconds.
- **$\mathcal{O}(1)$ Constant Memory Footprint:** Streams FFmpeg output line-by-line via `subprocess.Popen`. It will never overload RAM, even when dealing with 50+ GB 4K/60fps raw recordings.
- **Dual Volume Analysis Engines:**
  - **`astats` (Default):** Exact RMS volume level in dBFS per second.
  - **`ebur128`:** Perceived momentary loudness in LUFS adhering to ITU-R BS.1770 / EBU R128 broadcast standards.
- **Smart Anti-Spam & Cooldown Logic:** Merges sustained screaming/hype into a single highlight event by tracking the true peak dBFS and timestamp, preventing duplicated clips for the same play.
- **OBS Multi-Track Audio Support (`--audio-track`):** Easily target isolated microphone tracks (e.g., track 1) to prevent in-game explosions or gunshot sound effects from triggering false highlights.
- **Lossless Stream-Copy Clipping (`--cut-clips`):** Slices highlight clips in seconds using FFmpeg `-c copy` with zero quality degradation and no video re-encoding.
- **Multi-Format Export:** Generates clean console tables and exports metadata to **JSON**, **TXT** (editor-friendly timeline format), and **CSV** (for spreadsheets).
- **Zero External Dependencies:** Built 100% on Python's Standard Library (`argparse`, `subprocess`, `json`, `csv`, `pathlib`). No `pip install` required.

---

## 📋 Prerequisites

1. **Python 3.8+**
2. **FFmpeg** installed and accessible in your system's `PATH`.

### Verify FFmpeg Installation
```bash
ffmpeg -version
```

If not installed:
- **Windows:** `winget install Gyan.FFmpeg` or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
- **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install ffmpeg`
- **macOS:** `brew install ffmpeg`

---

## 🚀 Quick Start & Usage

### 1. Basic Detection (Default Settings)
Scans video with default threshold (`-18.0 dBFS`), 15s cooldown, and 10s pre-play buffer:
```bash
python highlight_detector.py "path/to/gameplay.mp4"
```

### 2. OBS Multi-Track Recordings (Target Microphone)
If OBS was configured to record game audio on Track 1 (index 0) and microphone on Track 2 (index 1):
```bash
python highlight_detector.py "stream_recording.mkv" --audio-track 1 --threshold -16.0
```

### 3. Automatic Clip Extraction (`--cut-clips`)
Automatically cuts and saves highlighted video clips without re-encoding:
```bash
python highlight_detector.py "gameplay.mp4" --cut-clips --output-dir "./my_highlights"
```

### 4. Customizing Buffers & Cooldown
Add a 15-second buffer before the scream to capture the entire play buildup, and a 7-second buffer after the peak:
```bash
python highlight_detector.py "cs2_clutch.mp4" --threshold -15.0 --cooldown 20 --pre-buffer 15 --post-buffer 7
```

### 5. Exporting Timestamps & Metadata
Export detection results to JSON, TXT, and CSV simultaneously:
```bash
python highlight_detector.py "match.mp4" --export-json --export-txt --export-csv
```

### 6. Perceptual Loudness Mode (`ebur128`)
```bash
python highlight_detector.py "match.mp4" --method ebur128 --threshold -16.0
```

---

## 📊 CLI Options & Arguments

| Argument | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `video` | `str` | *Required* | Path to video file (`.mp4`, `.mkv`, `.avi`, `.mov`, etc.). |
| `-t, --threshold` | `float` | `-18.0` | Audio volume threshold in dBFS (or LUFS). Typical range: -18.0 to -12.0. |
| `-c, --cooldown` | `float` | `15.0` | Cooldown period in seconds to prevent spam from long cheers. |
| `--pre-buffer` | `float` | `10.0` | Seconds subtracted before the peak to capture play buildup. |
| `--post-buffer` | `float` | `5.0` | Seconds added after the peak to capture reaction. |
| `-a, --audio-track`| `int` | `0` | Audio stream index to analyze (0 = first audio stream). |
| `-m, --method` | `str` | `astats` | Detection method: `astats` (RMS dBFS) or `ebur128` (LUFS). |
| `--step` | `float` | `1.0` | Sampling step size in seconds for `astats` filter. |
| `--cut-clips` | `flag` | `False` | Automatically slice highlight clips with FFmpeg (`-c copy`). |
| `-o, --output-dir`| `str` | `./highlights` | Output folder for clips and exported reports (customizable via `.env`). |
| `--export-json` | `str?` | `None` | Export complete metadata to JSON format. |
| `--export-txt` | `str?` | `None` | Export human-readable timestamps to TXT format. |
| `--export-csv` | `str?` | `None` | Export timestamps to CSV spreadsheet format. |
| `--no-progress` | `flag` | `False` | Disable live progress bar (useful for CI/CD or logging). |

---

## ⚙️ Configuration File (`.env`)

You can create an optional `.env` file in the project root to set a custom default output folder without typing `--output-dir` every time:

```env
HIGHLIGHTS_OUTPUT_DIR=C:/Users/username/Videos/Highlights
```

*(Note: `.env` is automatically ignored by Git to keep your local folders private).*

---

## 🧪 Automated Testing

The repository includes a comprehensive test suite in `test_detector.py` that generates synthetic video files with mathematically modulated sine waves (`aevalsrc`) to test:
1. Exact RMS dBFS peak detection.
2. Anti-spam cooldown suppression.
3. Lossless video cutting with FFmpeg stream copy.
4. JSON, TXT, and CSV export integrity.
5. `ebur128` perceptual loudness engine.
6. Multi-track audio stream isolation.

To run tests:
```bash
python test_detector.py
```

---

## 💡 Calibration Tips for Gamers & Streamers

- **Normal voice level:** Regular speaking voices typically sit between `-24 dBFS` and `-20 dBFS`.
- **Screams & loud hype:** Genuine reactions usually peak between `-16 dBFS` and `-6 dBFS`.
- **Recommended setup:** Start with `-16.0 dBFS`. If your game has loud gunfire that triggers false positives, either raise the threshold to `-14.0 dBFS` or configure OBS with a dedicated microphone track and use `--audio-track 1`.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
