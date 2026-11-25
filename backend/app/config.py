"""ClipForge configuration, paths, scoring weights, and caption presets."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

# Project root is the parent of /backend
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_data_dir() -> Path:
    raw = os.getenv("CLIPFORGE_DATA_DIR", "./data")
    p = Path(raw)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


DATA_DIR = _resolve_data_dir()
DOWNLOADS_DIR = DATA_DIR / "downloads"
WORK_DIR = DATA_DIR / "work"
OUTPUTS_DIR = DATA_DIR / "outputs"
DB_PATH = DATA_DIR / "clipforge.db"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
MAX_DURATION_MINUTES = int(os.getenv("CLIPFORGE_MAX_DURATION_MINUTES", "90"))
DEFAULT_WHISPER_MODEL = os.getenv("CLIPFORGE_WHISPER_MODEL", "small").strip() or "small"
DEFAULT_AUTO_DELETE_DAYS = int(os.getenv("CLIPFORGE_AUTO_DELETE_DAYS", "30"))


@dataclass(frozen=True)
class ScoringWeights:
    """Configurable weights for the 5 clip-scoring signals (must sum to 1.0)."""

    llm: float = 0.35
    replay_heat: float = 0.25
    audio_energy: float = 0.20
    hook: float = 0.10
    pace: float = 0.10

    def as_dict(self) -> Dict[str, float]:
        return {
            "llm": self.llm,
            "replay_heat": self.replay_heat,
            "audio_energy": self.audio_energy,
            "hook": self.hook,
            "pace": self.pace,
        }


SCORING_WEIGHTS = ScoringWeights()


# ASS subtitle style presets (ASS color format is &HAABBGGRR)
CAPTION_PRESETS: Dict[str, Dict[str, object]] = {
    "bold_yellow": {
        "label": "Bold Yellow Highlight",
        "font": "Arial",
        "fontsize": 68,
        "bold": -1,
        "primary_color": "&H00FFFFFF",      # White base text
        "highlight_color": "&H0000E5FF",    # Vivid warm yellow (&HBBGGRR -> 00E5FF = #FFE500)
        "outline_color": "&H00000000",      # Crisp black outline
        "back_color": "&H96000000",         # Subtle shadow
        "outline": 5,
        "shadow": 2,
        "border_style": 1,
        "margin_v_9_16": 420,               # Lower-middle safe area (above bottom 15% of 1920)
        "margin_v_1_1": 150,
        "margin_v_16_9": 120,
        "uppercase": True,
    },
    "clean": {
        "label": "Clean",
        "font": "Helvetica Neue",
        "fontsize": 58,
        "bold": -1,
        "primary_color": "&H00F8FAFC",
        "highlight_color": "&H00FFAA4D",    # Soft sky/indigo highlight
        "outline_color": "&H00141418",
        "back_color": "&H80000000",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "margin_v_9_16": 410,
        "margin_v_1_1": 140,
        "margin_v_16_9": 110,
        "uppercase": False,
    },
    "minimal": {
        "label": "Minimal",
        "font": "Arial",
        "fontsize": 48,
        "bold": 0,
        "primary_color": "&H00EAECEF",
        "highlight_color": "&H00FFFFFF",
        "outline_color": "&H00101014",
        "back_color": "&H70000000",
        "outline": 2,
        "shadow": 0,
        "border_style": 1,
        "margin_v_9_16": 390,
        "margin_v_1_1": 130,
        "margin_v_16_9": 100,
        "uppercase": False,
    },
}


def ensure_data_dirs() -> None:
    """Create local data directories if they do not exist."""
    for d in (DATA_DIR, DOWNLOADS_DIR, WORK_DIR, OUTPUTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str, max_len: int = 72) -> str:
    """Sanitize a string into a safe, cross-platform filename component."""
    cleaned = re.sub(r"[^\w\s-]", "", name, flags=re.ASCII)
    cleaned = re.sub(r"[-\s]+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "clip"
    return cleaned[:max_len]


def get_gemini_api_key() -> str:
    """Read current GEMINI_API_KEY from environment (reloading .env if needed)."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        key = os.getenv("GEMINI_API_KEY", "").strip()
    return key


def get_yt_dlp_cookie_candidates() -> list[list[str]]:
    """Return ordered list of yt-dlp cookie arg lists to try (file, or comma-separated browsers)."""
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    candidates: list[list[str]] = []

    cookies_file = os.getenv("YT_DLP_COOKIES_FILE", "").strip()
    if cookies_file:
        p = Path(cookies_file)
        if not p.is_absolute():
            p = (PROJECT_ROOT / p).resolve()
        if p.exists():
            candidates.append(["--cookies", str(p)])

    browsers = os.getenv("YT_DLP_COOKIES_FROM_BROWSER", "").strip()
    if browsers:
        for b in browsers.split(","):
            b = b.strip()
            if b:
                candidates.append(["--cookies-from-browser", b])

    if not candidates:
        candidates.append([])
    return candidates


def get_yt_dlp_cookie_args() -> list[str]:
    """Return the first configured yt-dlp cookie argument list."""
    return get_yt_dlp_cookie_candidates()[0]
