"""SQLModel database tables and Pydantic API request/response schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field as PydField
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    url: str
    video_id: str = ""
    video_title: str = ""
    channel: str = ""
    duration: float = 0.0
    thumbnail_url: str = ""
    status: str = "queued"  # queued | running | completed | failed
    stage: str = "queued"   # queued | downloading | transcribing | analyzing | scoring | rendering | metadata | completed | failed
    progress: float = 0.0
    status_message: str = "Queued for processing"
    error_message: Optional[str] = None
    options_json: str = "{}"
    logs_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Clip(SQLModel, table=True):
    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    rank: int
    start_sec: float
    end_sec: float
    duration_sec: float
    final_score: float
    replay_heat: Optional[float] = None
    audio_energy: Optional[float] = None
    speech_pace: float = 0.0
    hook_score: float = 0.0
    llm_score: Optional[float] = None
    llm_reason: str = ""
    hook_quality: str = "moderate"
    standalone: bool = True
    transcript_text: str = ""
    output_format: str = "9:16"
    crop_mode: str = "center"
    caption_style: str = "bold_yellow"
    captions_enabled: bool = True
    denoise_audio: bool = False
    clip_dir: str = ""
    metadata_json: str = "{}"
    render_version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


# --- API Request Schemas ---


class JobOptionsIn(BaseModel):
    clip_count: int = PydField(default=5, ge=1, le=15)
    min_clip_sec: float = PydField(default=20.0, ge=5.0, le=120.0)
    max_clip_sec: float = PydField(default=60.0, ge=10.0, le=180.0)
    output_format: str = PydField(default="9:16", pattern=r"^(9:16|1:1|16:9)$")
    crop_mode: str = PydField(default="center", pattern=r"^(center|smart|blur_fit)$")
    caption_style: str = PydField(default="bold_yellow", pattern=r"^(bold_yellow|clean|minimal)$")
    captions_enabled: bool = True
    quality: str = PydField(default="1080p", pattern=r"^(720p|1080p)$")
    whisper_model: Optional[str] = None
    language: Optional[str] = None
    denoise_audio: bool = False


class CreateJobRequest(BaseModel):
    url: str
    options: JobOptionsIn = PydField(default_factory=JobOptionsIn)


class ZipRequest(BaseModel):
    clip_ids: list[str] = PydField(default_factory=list)


class RerenderRequest(BaseModel):
    output_format: Optional[str] = PydField(default=None, pattern=r"^(9:16|1:1|16:9)$")
    crop_mode: Optional[str] = PydField(default=None, pattern=r"^(center|smart|blur_fit)$")
    caption_style: Optional[str] = PydField(default=None, pattern=r"^(bold_yellow|clean|minimal)$")
    captions_enabled: Optional[bool] = None
    start_sec: Optional[float] = PydField(default=None, ge=0.0)
    end_sec: Optional[float] = PydField(default=None, gt=0.0)
    denoise_audio: Optional[bool] = None


class UpdateMetadataRequest(BaseModel):
    youtube_shorts: Optional[dict[str, Any]] = None
    instagram_reels: Optional[dict[str, Any]] = None
    tiktok: Optional[dict[str, Any]] = None
    on_screen_hook: Optional[str] = None
    alternative_titles: Optional[list[str]] = None


class UpdateSettingsRequest(BaseModel):
    whisper_model: Optional[str] = None
    default_format: Optional[str] = None
    default_caption_style: Optional[str] = None
    auto_delete_days: Optional[int] = PydField(default=None, ge=0, le=365)


class CleanupRequest(BaseModel):
    older_than_days: Optional[int] = PydField(default=None, ge=0, le=365)
    clear_cache: bool = False


class InstagramConfigRequest(BaseModel):
    access_token: str
    ig_user_id: str


class PublishClipsRequest(BaseModel):
    clip_ids: list[str]
    platforms: list[str]  # ["youtube", "instagram"]
    privacy_status: str = PydField(default="private", pattern=r"^(private|unlisted|public)$")
    use_nocaptions: bool = False
    youtube_account_id: Optional[str] = None
