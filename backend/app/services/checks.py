"""Startup dependency checks for ffmpeg, ffprobe, yt-dlp, and hardware encoders."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DependencyReport:
    ffmpeg_path: Optional[str]
    ffprobe_path: Optional[str]
    yt_dlp_path: Optional[str]
    ffmpeg_version: Optional[str]
    yt_dlp_version: Optional[str]
    hw_encoder: str

    @property
    def ok(self) -> bool:
        return bool(self.ffmpeg_path and self.ffprobe_path and self.yt_dlp_path)


def find_executable(name: str) -> Optional[str]:
    """Locate an executable on PATH or alongside the active Python interpreter."""
    venv_bin = Path(sys.executable).parent / name
    if venv_bin.exists() and venv_bin.is_file():
        return str(venv_bin)
    venv_exe = Path(sys.executable).parent / f"{name}.exe"
    if venv_exe.exists() and venv_exe.is_file():
        return str(venv_exe)
    return shutil.which(name)


def _get_first_line(cmd: list[str]) -> Optional[str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            out = (res.stdout or res.stderr).strip()
            return out.splitlines()[0] if out else None
    except Exception:
        return None
    return None


@lru_cache(maxsize=1)
def detect_video_encoder(ffmpeg_path: Optional[str] = None) -> str:
    """Detect a working H.264 hardware encoder, falling back to libx264."""
    exe = ffmpeg_path or find_executable("ffmpeg")
    if not exe:
        return "libx264"

    system = platform.system().lower()
    candidates: list[str] = []
    if system == "darwin":
        candidates.append("h264_videotoolbox")
    else:
        candidates.append("h264_nvenc")

    for enc in candidates:
        try:
            probe = subprocess.run(
                [
                    exe,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=256x256:d=0.1",
                    "-frames:v",
                    "1",
                    "-c:v",
                    enc,
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if probe.returncode == 0:
                logger.info("Hardware H.264 encoder available: %s", enc)
                return enc
        except Exception:
            continue

    logger.info("Using software H.264 encoder: libx264")
    return "libx264"


@lru_cache(maxsize=1)
def has_ass_filter(ffmpeg_path: Optional[str] = None) -> bool:
    """Return True if this ffmpeg build includes the libass 'ass' video filter."""
    exe = ffmpeg_path or find_executable("ffmpeg")
    if not exe:
        return False
    try:
        res = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "ass":
                    return True
    except Exception:
        pass
    return False


def check_dependencies(raise_on_missing: bool = False) -> DependencyReport:
    """Verify ffmpeg, ffprobe, and yt-dlp are installed and print OS-specific help if missing."""
    ffmpeg_path = find_executable("ffmpeg")
    ffprobe_path = find_executable("ffprobe")
    yt_dlp_path = find_executable("yt-dlp")

    ffmpeg_ver = _get_first_line([ffmpeg_path, "-version"]) if ffmpeg_path else None
    yt_dlp_ver = _get_first_line([yt_dlp_path, "--version"]) if yt_dlp_path else None
    hw_enc = detect_video_encoder(ffmpeg_path) if ffmpeg_path else "libx264"

    report = DependencyReport(
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        yt_dlp_path=yt_dlp_path,
        ffmpeg_version=ffmpeg_ver,
        yt_dlp_version=yt_dlp_ver,
        hw_encoder=hw_enc,
    )

    if not report.ok:
        missing = []
        if not ffmpeg_path or not ffprobe_path:
            missing.append("ffmpeg (and ffprobe)")
        if not yt_dlp_path:
            missing.append("yt-dlp")

        instructions = (
            f"\n[ClipForge Startup Check] Missing required tools: {', '.join(missing)}\n"
            "Install instructions:\n"
            "  macOS (Homebrew):  brew install ffmpeg yt-dlp\n"
            "  Windows (winget):  winget install Gyan.FFmpeg yt-dlp.yt-dlp\n"
            "  Linux (Debian/Ubuntu): sudo apt update && sudo apt install -y ffmpeg && pip install -U yt-dlp\n"
        )
        logger.warning(instructions)
        print(instructions, file=sys.stderr)
        if raise_on_missing:
            raise RuntimeError(instructions.strip())
    else:
        logger.info(
            "Dependencies OK | ffmpeg=%s | yt-dlp=%s | encoder=%s",
            ffmpeg_ver,
            yt_dlp_ver,
            hw_enc,
        )

    return report
