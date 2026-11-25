"""SQLite database initialization and helper functions."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, SQLModel, create_engine, select

from app.config import (
    DB_PATH,
    DEFAULT_AUTO_DELETE_DAYS,
    DEFAULT_WHISPER_MODEL,
    ensure_data_dirs,
)
from app.models import AppSetting, Clip, Job

ensure_data_dirs()
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "whisper_model": DEFAULT_WHISPER_MODEL,
    "default_format": "9:16",
    "default_caption_style": "bold_yellow",
    "auto_delete_days": DEFAULT_AUTO_DELETE_DAYS,
}


def init_db() -> None:
    ensure_data_dirs()
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


def get_app_settings(session: Session) -> dict[str, Any]:
    rows = session.exec(select(AppSetting)).all()
    data = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            data[row.key] = json.loads(row.value)
        except Exception:
            data[row.key] = row.value
    return data


def save_app_settings(session: Session, updates: dict[str, Any]) -> dict[str, Any]:
    for key, val in updates.items():
        if val is None or key not in DEFAULT_SETTINGS:
            continue
        encoded = json.dumps(val)
        existing = session.get(AppSetting, key)
        if existing:
            existing.value = encoded
        else:
            session.add(AppSetting(key=key, value=encoded))
    session.commit()
    return get_app_settings(session)


def serialize_clip(clip: Clip) -> dict[str, Any]:
    try:
        meta = json.loads(clip.metadata_json or "{}")
    except Exception:
        meta = {}
    return {
        "id": clip.id,
        "job_id": clip.job_id,
        "rank": clip.rank,
        "start_sec": clip.start_sec,
        "end_sec": clip.end_sec,
        "duration_sec": clip.duration_sec,
        "final_score": clip.final_score,
        "scores": {
            "final": clip.final_score,
            "llm": clip.llm_score,
            "replay_heat": clip.replay_heat,
            "audio_energy": clip.audio_energy,
            "hook": clip.hook_score,
            "pace": clip.speech_pace,
        },
        "llm_reason": clip.llm_reason,
        "hook_quality": clip.hook_quality,
        "standalone": clip.standalone,
        "transcript_text": clip.transcript_text,
        "output_format": clip.output_format,
        "crop_mode": clip.crop_mode,
        "caption_style": clip.caption_style,
        "captions_enabled": clip.captions_enabled,
        "denoise_audio": clip.denoise_audio,
        "render_version": clip.render_version,
        "metadata": meta,
        "created_at": clip.created_at.isoformat(),
        "updated_at": clip.updated_at.isoformat(),
    }


def serialize_job(job: Job, clips: list[Clip] | None = None, clip_count: int | None = None) -> dict[str, Any]:
    try:
        options = json.loads(job.options_json or "{}")
    except Exception:
        options = {}
    try:
        logs = json.loads(job.logs_json or "[]")
    except Exception:
        logs = []

    out: dict[str, Any] = {
        "id": job.id,
        "url": job.url,
        "video_id": job.video_id,
        "video_title": job.video_title,
        "channel": job.channel,
        "duration": job.duration,
        "thumbnail_url": job.thumbnail_url,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "status_message": job.status_message,
        "error_message": job.error_message,
        "options": options,
        "logs": logs,
        "clip_count": len(clips) if clips is not None else (clip_count or 0),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
    if clips is not None:
        out["clips"] = [serialize_clip(c) for c in sorted(clips, key=lambda x: x.rank)]
    return out
