# ClipForge

A locally-hosted personal YouTube-to-short-form clip studio. Paste a YouTube link, and ClipForge automatically finds the highest-retention moments, cuts vertical/square/landscape clips with word-highlighted captions and `-14 LUFS` normalized audio, generates editable YouTube Shorts / Instagram Reels / TikTok metadata, and lets you download clips individually or in bulk ZIP archives.

> **Personal Use & Copyright Notice**
> ClipForge is strictly for **personal use**. Respect copyright law and YouTube's Terms of Service—only process your own videos or content you have explicit permission to download and repurpose.

---

## Features
- **7-Stage Resumable Pipeline**: Caches downloads, 16 kHz WAV audio, Whisper transcripts, and window scores under `./data` so retries or re-runs never redo completed work.
- **5-Signal Moment Scoring**: Combines Google Gemini LLM evaluation (`0.35`), YouTube "Most replayed" heatmap (`0.25`), `librosa` audio energy/dynamics (`0.20`), opening hook heuristics (`0.10`), and speech pace (`0.10`), automatically renormalizing weights if any signal (like Gemini or heatmap) is unavailable.
- **Zero-Paid-Services Fallback**: Works out of the box without a `GEMINI_API_KEY` using local heuristics and clean template metadata.
- **Flexible Rendering**: 9:16 vertical (`1080×1920`), 1:1 square, or 16:9 original; center crop, optional MediaPipe smart face-aware crop, or blurred-background fit; hardware H.264 encoding (`h264_videotoolbox` / `h264_nvenc`) with automatic `libx264` fallback; word-highlighted `.ass` captions (with Pillow overlay fallback if `ffmpeg` lacks `libass`) + `.srt` export; `-14 LUFS` loudness normalization + optional denoise; MP3 audio export & best-frame JPG thumbnail.
- **Instant Single-Clip Trim & Re-render**: Adjust start/end sliders, aspect ratio, framing mode, or caption style for any clip without re-running the pipeline.
- **Live Dark Studio UI**: Real-time SSE progress stepper, live logs, score rings & signal breakdown bars, inline metadata editing, copy buttons, bulk ZIP export, and storage management.

---

## Prerequisites
- **Python 3.11+**
- **Node.js 20+** and **npm**
- **ffmpeg** (and `ffprobe`)
- **yt-dlp** (installed automatically in the Python virtualenv, or via your OS package manager)

### Installing `ffmpeg`
- **macOS**: `brew install ffmpeg` (or `brew install ffmpeg-full` for native `libass` subtitle burning)
- **Windows**: `winget install Gyan.FFmpeg`
- **Linux (Debian/Ubuntu)**: `sudo apt update && sudo apt install -y ffmpeg`

ClipForge runs a startup dependency check and prints OS-specific install instructions if `ffmpeg` or `yt-dlp` is missing.

---

## Setup & Running Locally

### 1. Environment Variables (Optional Gemini Free Tier)
```bash
cp .env.example .env
# Optionally set GEMINI_API_KEY=your_key_here (free tier from Google AI Studio)
```

### 2. Backend Setup
```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

# Optional: enable MediaPipe face-aware smart crop
# backend/.venv/bin/pip install opencv-python-headless mediapipe
```

### 3. Frontend & Root Runner Setup
```bash
npm install
npm --prefix frontend install
```

### 4. Start Both Backend & Frontend (One Command)
```bash
npm run dev
# or: ./scripts/start.sh
```
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000 (Swagger docs at http://localhost:8000/docs)

### 5. Run the Pipeline via CLI (Testing / Headless)
```bash
backend/.venv/bin/python backend/cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --clips 3 --quality 720p --whisper-model small
```

### 6. Keeping `yt-dlp` Up to Date
YouTube changes frequently; update `yt-dlp` anytime with:
```bash
npm run update:yt-dlp
# or: ./scripts/update-yt-dlp.sh
```

### 7. Running Backend Tests
```bash
npm run test:backend
```

---

## Backend API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Dependency check (`ffmpeg`, `yt-dlp`, detected HW encoder, Gemini status) |
| `POST` | `/api/jobs` | Create & enqueue a clipping job `{ "url": "...", "options": { ... } }` |
| `GET` | `/api/jobs` | List all jobs (newest first) |
| `GET` | `/api/jobs/{id}` | Get job details, logs, and generated clips |
| `GET` | `/api/jobs/{id}/events` | Server-Sent Events (SSE) stream of live stage, percent, and logs |
| `POST` | `/api/jobs/{id}/retry` | Retry a failed job (reuses cached download, transcript, and scores) |
| `DELETE` | `/api/jobs/{id}` | Delete job, clip records, and `./data/outputs/{id}` |
| `GET` | `/api/clips/{id}/download?type=...` | Download clip asset: `mp4`, `mp4_nocaptions`, `mp3`, `srt`, `thumb`, or `meta` (`inline=true` for preview) |
| `POST` | `/api/jobs/{id}/zip` | Download selected clips (`{"clip_ids": [...]}`) or all clips (`{"clip_ids": []}`) as a structured ZIP |
| `POST` | `/api/clips/{id}/rerender` | Re-render a single clip with new trim times, format, crop mode, or caption style |
| `PATCH` | `/api/clips/{id}/metadata` | Persist edited Shorts/Reels/TikTok metadata to SQLite, `metadata.json`, and `metadata.txt` |
| `GET` / `PATCH` | `/api/settings` | Read or update default Whisper model, format, caption style, auto-delete days, and disk usage |
| `POST` | `/api/storage/cleanup` | Remove old job outputs and optionally clear downloads/work caches |

---

## Project Structure
```text
clipper/
├── backend/
│   ├── cli.py                 # Standalone CLI pipeline runner
│   ├── requirements.txt
│   ├── app/
│   │   ├── config.py          # Paths, scoring weights, caption presets
│   │   ├── db.py              # SQLite / SQLModel setup & serializers
│   │   ├── models.py          # DB tables & Pydantic schemas
│   │   ├── worker.py          # Single-concurrency job queue + SSE broadcaster
│   │   ├── main.py            # FastAPI app & routes
│   │   └── services/          # ingest, transcribe, candidates, scoring, selector, subtitles, smart_crop, render, metadata, pipeline
│   └── tests/
│       └── test_scoring_and_selection.py
├── frontend/                  # Next.js App Router + TypeScript + Tailwind
├── scripts/
│   ├── start.sh
│   └── update-yt-dlp.sh
├── data/                      # Local storage (downloads, work, outputs, clipforge.db)
├── .env.example
└── package.json
```
# clipper-youtube
