"""Stage 1: YouTube URL validation, metadata + heatmap extraction, download, and 16kHz WAV."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from app.config import DOWNLOADS_DIR, MAX_DURATION_MINUTES, ensure_data_dirs, get_yt_dlp_cookie_candidates
from app.services.checks import find_executable

logger = logging.getLogger(__name__)

ProgressFn = Optional[Callable[[str, float, str], None]]

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class IngestError(RuntimeError):
    """User-facing error raised during video ingestion."""


@dataclass
class IngestResult:
    video_id: str
    url: str
    title: str
    channel: str
    duration: float
    thumbnail_url: str
    heatmap: list[dict[str, float]]
    video_path: Path
    audio_wav_path: Path
    info_path: Path
    width: int
    height: int
    fps: float
    has_youtube_captions: bool


def extract_video_id(url: str) -> str:
    """Validate a YouTube URL and return its 11-character video ID."""
    raw = (url or "").strip()
    if not raw:
        raise IngestError("Please enter a YouTube URL.")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise IngestError(
            "Unsupported URL. Please provide a valid YouTube link (youtube.com or youtu.be)."
        )

    video_id: Optional[str] = None
    if host.endswith("youtu.be"):
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            video_id = parts[0]
    else:
        path_parts = [p for p in parsed.path.split("/") if p]
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "live", "embed", "v"}:
            video_id = path_parts[1]

    if not video_id or not VIDEO_ID_RE.match(video_id):
        raise IngestError("Could not extract a valid YouTube video ID from that link.")
    return video_id


def _map_ytdlp_error(stderr: str) -> str:
    low = stderr.lower()
    if "private video" in low:
        return "This video is private and cannot be downloaded."
    if "sign in to confirm your age" in low or "age-restricted" in low:
        return (
            "This video is age-restricted. Set YT_DLP_COOKIES_FROM_BROWSER=chrome (or safari/firefox) "
            "or YT_DLP_COOKIES_FILE=/path/to/cookies.txt in .env and click Retry."
        )
    if "sign in to confirm" in low and "bot" in low:
        return (
            "YouTube requires signed-in browser cookies for this video. Set "
            "YT_DLP_COOKIES_FROM_BROWSER=chrome (or safari/firefox/brave) or "
            "YT_DLP_COOKIES_FILE=/path/to/cookies.txt in .env, then click Retry."
        )
    if "video unavailable" in low or "has been removed" in low:
        return "This YouTube video is unavailable or has been removed."
    if "live event will begin" in low or "is live" in low:
        return "Live streams cannot be processed until they finish."
    if "unable to download webpage" in low or "failed to resolve" in low or "timed out" in low:
        return "Network error while contacting YouTube. Check your connection and try again."
    last_line = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    detail = last_line[-1] if last_line else "Unknown yt-dlp error."
    return f"yt-dlp failed: {detail}"


def _probe_video(ffprobe_path: str, video_path: Path) -> tuple[int, int, float]:
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-of",
        "json",
        str(video_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        return 1920, 1080, 30.0
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        return 1920, 1080, 30.0
    s = streams[0]
    w = int(s.get("width") or 1920)
    h = int(s.get("height") or 1080)
    rate = str(s.get("r_frame_rate") or "30/1")
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            fps = float(num) / max(float(den), 1.0)
        else:
            fps = float(rate)
    except Exception:
        fps = 30.0
    return w, h, round(fps, 3)


def ingest_video(
    url: str,
    quality: str = "1080p",
    max_duration_minutes: int = MAX_DURATION_MINUTES,
    progress: ProgressFn = None,
) -> IngestResult:
    """Fetch metadata/heatmap, download MP4 (cached), and extract 16kHz mono WAV (cached)."""
    ensure_data_dirs()
    video_id = extract_video_id(url)
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    yt_dlp = find_executable("yt-dlp")
    ffmpeg = find_executable("ffmpeg")
    ffprobe = find_executable("ffprobe")
    if not yt_dlp or not ffmpeg or not ffprobe:
        raise IngestError(
            "Missing ffmpeg or yt-dlp. Run ClipForge startup check for installation instructions."
        )

    max_height = 720 if str(quality).startswith("720") else 1080
    video_dir = DOWNLOADS_DIR / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    info_path = video_dir / "info.json"
    video_path = video_dir / f"video_{max_height}p.mp4"
    audio_wav_path = video_dir / "audio_16k.wav"

    cookie_candidates = get_yt_dlp_cookie_candidates()
    active_cookie_args: list[str] = cookie_candidates[0]

    # 1. Fetch or load cached metadata
    info: dict[str, Any]
    if info_path.exists():
        if progress:
            progress("downloading", 5.0, f"Loaded cached metadata for {video_id}")
        info = json.loads(info_path.read_text(encoding="utf-8"))
    else:
        if progress:
            progress("downloading", 3.0, "Fetching YouTube metadata and replay heatmap...")
        res = None
        for c_args in cookie_candidates:
            cmd = [
                yt_dlp,
                *c_args,
                "--dump-single-json",
                "--no-playlist",
                "--no-warnings",
                canonical_url,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if res.returncode == 0:
                active_cookie_args = c_args
                break
            logger.warning("yt-dlp metadata attempt with %s failed: %s", c_args or "no-cookies", (res.stderr or "").strip().splitlines()[-1:] )
        assert res is not None
        if res.returncode != 0:
            raise IngestError(_map_ytdlp_error(res.stderr or res.stdout))
        try:
            info = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise IngestError(f"Failed to parse YouTube metadata: {exc}") from exc
        info_path.write_text(json.dumps(info, indent=2), encoding="utf-8")

    duration = float(info.get("duration") or 0.0)
    if duration <= 0:
        raise IngestError("Could not determine video duration (live or invalid video).")
    if duration > max_duration_minutes * 60:
        raise IngestError(
            f"Video is {duration / 60:.1f} minutes long, exceeding the {max_duration_minutes}-minute limit."
        )

    title = str(info.get("title") or f"YouTube Video {video_id}")
    channel = str(info.get("channel") or info.get("uploader") or "Unknown Channel")
    thumbnail_url = str(info.get("thumbnail") or "")
    raw_heatmap = info.get("heatmap") or []
    heatmap: list[dict[str, float]] = []
    if isinstance(raw_heatmap, list):
        for pt in raw_heatmap:
            if isinstance(pt, dict) and "start_time" in pt and "end_time" in pt and "value" in pt:
                heatmap.append(
                    {
                        "start_time": float(pt["start_time"]),
                        "end_time": float(pt["end_time"]),
                        "value": float(pt["value"]),
                    }
                )
    has_yt_captions = bool(info.get("subtitles") or info.get("automatic_captions"))

    # 2. Download video MP4 if not cached
    if video_path.exists() and video_path.stat().st_size > 0:
        if progress:
            progress("downloading", 15.0, f"Using cached {max_height}p video")
    else:
        if progress:
            progress("downloading", 8.0, f"Downloading video at up to {max_height}p...")
        tmp_template = str(video_dir / f"tmp_{max_height}p.%(ext)s")
        fmt = (
            f"bestvideo[height<={max_height}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={max_height}]+bestaudio/"
            f"best[height<={max_height}]"
        )
        dl_order = [active_cookie_args] + [c for c in cookie_candidates if c != active_cookie_args]
        res = None
        for c_args in dl_order:
            cmd = [
                yt_dlp,
                *c_args,
                "--no-playlist",
                "--no-warnings",
                "-f",
                fmt,
                "--merge-output-format",
                "mp4",
                "-o",
                tmp_template,
                canonical_url,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if res.returncode == 0:
                break
        assert res is not None
        if res.returncode != 0:
            raise IngestError(_map_ytdlp_error(res.stderr or res.stdout))

        # Find merged mp4 and atomically move into place
        candidates = sorted(video_dir.glob(f"tmp_{max_height}p*.mp4"))
        if not candidates:
            candidates = sorted(video_dir.glob(f"tmp_{max_height}p.*"))
        if not candidates:
            raise IngestError("Download finished but output video file was not found.")
        candidates[0].rename(video_path)
        # Clean leftover temp files
        for extra in video_dir.glob(f"tmp_{max_height}p*"):
            extra.unlink(missing_ok=True)
        if progress:
            progress("downloading", 16.0, "Video downloaded successfully")

    # 3. Extract 16kHz mono WAV for Whisper + librosa analysis
    if audio_wav_path.exists() and audio_wav_path.stat().st_size > 0:
        if progress:
            progress("downloading", 20.0, "Using cached 16 kHz mono audio")
    else:
        if progress:
            progress("downloading", 17.0, "Extracting 16 kHz mono WAV for analysis...")
        tmp_wav = video_dir / "audio_16k.tmp.wav"
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(tmp_wav),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0 or not tmp_wav.exists():
            tmp_wav.unlink(missing_ok=True)
            raise IngestError(f"ffmpeg failed to extract audio: {res.stderr.strip()}")
        tmp_wav.rename(audio_wav_path)
        if progress:
            progress("downloading", 20.0, "Audio extraction complete")

    width, height, fps = _probe_video(ffprobe, video_path)
    return IngestResult(
        video_id=video_id,
        url=canonical_url,
        title=title,
        channel=channel,
        duration=duration,
        thumbnail_url=thumbnail_url,
        heatmap=heatmap,
        video_path=video_path,
        audio_wav_path=audio_wav_path,
        info_path=info_path,
        width=width,
        height=height,
        fps=fps,
        has_youtube_captions=has_yt_captions,
    )
