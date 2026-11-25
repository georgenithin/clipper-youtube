"""Single-concurrency background job queue with live SSE event broadcasting."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import shutil
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import select

from app.config import DOWNLOADS_DIR
from app.db import get_app_settings, get_session
from app.models import Clip, Job, utcnow
from app.services.pipeline import PipelineOptions, run_pipeline
from app.services.storage import (
    is_auto_clean_enabled,
    is_supabase_enabled,
    upload_clip_directory,
)

logger = logging.getLogger(__name__)


class JobWorker:
    """Runs ClipForge jobs one at a time in a background thread and broadcasts SSE events."""

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._sub_lock = threading.Lock()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="clipforge-worker", daemon=True)
        self._thread.start()
        self._requeue_pending_jobs()
        logger.info("ClipForge background worker started (concurrency=1)")

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put("")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _requeue_pending_jobs(self) -> None:
        """Resume any jobs that were queued or interrupted before a server restart."""
        with get_session() as session:
            pending = session.exec(
                select(Job).where(Job.status.in_(["queued", "running"]))
            ).all()
            for job in pending:
                job.status = "queued"
                job.stage = "queued"
                job.status_message = "Resuming after server restart..."
                job.updated_at = utcnow()
                session.add(job)
                self._queue.put(job.id)
            session.commit()

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        with self._sub_lock:
            self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._sub_lock:
            subs = self._subscribers.get(job_id)
            if subs and q in subs:
                subs.remove(q)
                if not subs:
                    self._subscribers.pop(job_id, None)

    def _publish(self, job_id: str, event: dict[str, Any]) -> None:
        if not self._loop or self._loop.is_closed():
            return
        with self._sub_lock:
            targets = list(self._subscribers.get(job_id, ()))
        for q in targets:
            def _put(target_q: asyncio.Queue[dict[str, Any]] = q) -> None:
                try:
                    target_q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

            self._loop.call_soon_threadsafe(_put)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if not job_id or self._stop_event.is_set():
                continue
            try:
                self._process_job(job_id)
            except Exception as exc:
                logger.exception("Unhandled error processing job %s: %s", job_id, exc)
            finally:
                self._queue.task_done()

    def _process_job(self, job_id: str) -> None:
        with get_session() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            settings = get_app_settings(session)
            raw_opts = json.loads(job.options_json or "{}")
            logs: list[dict[str, Any]] = json.loads(job.logs_json or "[]")

            # Remove any old clips from a prior attempt of the same job
            old_clips = session.exec(select(Clip).where(Clip.job_id == job_id)).all()
            for oc in old_clips:
                session.delete(oc)

            job.status = "running"
            job.stage = "downloading"
            job.progress = 1.0
            job.error_message = None
            job.status_message = "Starting pipeline..."
            job.updated_at = utcnow()
            session.add(job)
            session.commit()
            url = job.url

        opts = PipelineOptions(
            clip_count=int(raw_opts.get("clip_count", 5)),
            min_clip_sec=float(raw_opts.get("min_clip_sec", 20.0)),
            max_clip_sec=float(raw_opts.get("max_clip_sec", 60.0)),
            output_format=str(raw_opts.get("output_format", settings["default_format"])),
            crop_mode=str(raw_opts.get("crop_mode", "center")),
            caption_style=str(raw_opts.get("caption_style", settings["default_caption_style"])),
            captions_enabled=bool(raw_opts.get("captions_enabled", True)),
            quality=str(raw_opts.get("quality", "1080p")),
            whisper_model=str(raw_opts.get("whisper_model") or settings["whisper_model"]),
            language=raw_opts.get("language") or None,
            denoise_audio=bool(raw_opts.get("denoise_audio", False)),
        )

        def on_progress(stage: str, pct: float, msg: str) -> None:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            log_entry = {"time": ts, "stage": stage, "message": msg}
            logs.append(log_entry)
            if len(logs) > 200:
                del logs[:-200]

            with get_session() as s:
                j = s.get(Job, job_id)
                if not j:
                    return
                j.stage = stage
                j.progress = round(pct, 1)
                j.status_message = msg
                j.logs_json = json.dumps(logs)
                j.updated_at = utcnow()
                s.add(j)
                s.commit()

            self._publish(
                job_id,
                {
                    "type": "progress",
                    "status": "running",
                    "stage": stage,
                    "progress": round(pct, 1),
                    "message": msg,
                    "log": log_entry,
                },
            )

        try:
            result = run_pipeline(job_id=job_id, url=url, options=opts, progress=on_progress)

            # Optional: offload rendered clips to Supabase Storage and clean local heavy files
            if is_supabase_enabled() and result.clips:
                all_uploaded = True
                for idx, produced in enumerate(result.clips, start=1):
                    on_progress(
                        "metadata",
                        round(95.0 + (idx / max(len(result.clips), 1)) * 4.0, 1),
                        f"Uploading clip {idx}/{len(result.clips)} to Supabase Storage...",
                    )
                    if not upload_clip_directory(job_id, produced.clip_dir):
                        all_uploaded = False
                if all_uploaded and is_auto_clean_enabled():
                    on_progress("metadata", 99.5, "Cleaning local source & rendered media to free disk space...")
                    for produced in result.clips:
                        for heavy in ("clip.mp4", "clip_nocaptions.mp4", "audio.mp3", "thumbnail.jpg"):
                            (produced.clip_dir / heavy).unlink(missing_ok=True)
                    dl_dir = DOWNLOADS_DIR / result.ingest.video_id
                    if dl_dir.exists():
                        shutil.rmtree(dl_dir, ignore_errors=True)

            with get_session() as session:
                job = session.get(Job, job_id)
                if not job:
                    return
                job.video_id = result.ingest.video_id
                job.video_title = result.ingest.title
                job.channel = result.ingest.channel
                job.duration = result.ingest.duration
                job.thumbnail_url = result.ingest.thumbnail_url
                job.status = "completed"
                job.stage = "completed"
                job.progress = 100.0
                job.status_message = f"Generated {len(result.clips)} clips"
                job.updated_at = utcnow()
                session.add(job)

                for produced in result.clips:
                    sc = produced.scored
                    clip_row = Clip(
                        id=f"clip_{uuid.uuid4().hex[:10]}",
                        job_id=job_id,
                        rank=produced.rank,
                        start_sec=sc.start,
                        end_sec=sc.end,
                        duration_sec=round(sc.end - sc.start, 2),
                        final_score=sc.final_score,
                        replay_heat=sc.replay_heat,
                        audio_energy=sc.audio_energy,
                        speech_pace=sc.speech_pace,
                        hook_score=sc.hook_score,
                        llm_score=sc.llm_score,
                        llm_reason=sc.llm_reason,
                        hook_quality=sc.hook_quality,
                        standalone=sc.standalone,
                        transcript_text=sc.text,
                        output_format=opts.output_format,
                        crop_mode=opts.crop_mode,
                        caption_style=opts.caption_style,
                        captions_enabled=opts.captions_enabled,
                        denoise_audio=opts.denoise_audio,
                        clip_dir=str(produced.clip_dir),
                        metadata_json=json.dumps(produced.metadata, ensure_ascii=False),
                    )
                    session.add(clip_row)
                session.commit()

            self._publish(
                job_id,
                {
                    "type": "completed",
                    "status": "completed",
                    "stage": "completed",
                    "progress": 100.0,
                    "message": f"Generated {len(result.clips)} clips",
                },
            )
        except Exception as exc:
            err_msg = str(exc) or "Pipeline failed unexpectedly."
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            log_entry = {"time": ts, "stage": "failed", "message": f"ERROR: {err_msg}"}
            logs.append(log_entry)
            with get_session() as session:
                job = session.get(Job, job_id)
                if job:
                    job.status = "failed"
                    job.stage = "failed"
                    job.error_message = err_msg
                    job.status_message = "Job failed"
                    job.logs_json = json.dumps(logs)
                    job.updated_at = utcnow()
                    session.add(job)
                    session.commit()
            self._publish(
                job_id,
                {
                    "type": "failed",
                    "status": "failed",
                    "stage": "failed",
                    "progress": 0.0,
                    "message": err_msg,
                    "error": err_msg,
                    "log": log_entry,
                },
            )


worker = JobWorker()
