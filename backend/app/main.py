"""ClipForge FastAPI application — REST API, SSE progress stream, downloads, and settings."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlmodel import select

from app.config import (
    CAPTION_PRESETS,
    DATA_DIR,
    DOWNLOADS_DIR,
    GEMINI_MODEL,
    OUTPUTS_DIR,
    SCORING_WEIGHTS,
    WORK_DIR,
    ensure_data_dirs,
    get_gemini_api_key,
    get_relay_secret,
    is_relay_download_mode,
    sanitize_filename,
)
from app.db import (
    get_app_settings,
    get_session,
    init_db,
    save_app_settings,
    serialize_clip,
    serialize_job,
)
from app.models import (
    CleanupRequest,
    Clip,
    CreateJobRequest,
    Job,
    RerenderRequest,
    UpdateMetadataRequest,
    UpdateSettingsRequest,
    ZipRequest,
    utcnow,
)
from app.services.checks import check_dependencies
from app.services.ingest import IngestError, extract_video_id
from app.services.metadata import save_metadata_files
from app.services.render import RenderError, render_clip
from app.services.storage import (
    create_signed_url,
    get_supabase_bucket,
    is_auto_clean_enabled,
    is_supabase_enabled,
    iter_object_chunks,
    upload_clip_directory,
    upload_file,
)
from app.services.transcribe import WordTiming
from app.worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    init_db()
    check_dependencies(raise_on_missing=False)
    loop = asyncio.get_running_loop()
    worker.start(loop)
    yield
    worker.stop()


app = FastAPI(
    title="ClipForge API",
    version="1.0.0",
    description="Local YouTube-to-Short-Form clipping, captioning, and metadata studio.",
    lifespan=lifespan,
)

_extra_origins = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        *_extra_origins,
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _load_cached_words(video_id: str) -> list[WordTiming]:
    """Load word timings from the most recent cached transcript for a video_id."""
    work_dir = WORK_DIR / video_id
    if not work_dir.exists():
        return []
    candidates = sorted(work_dir.glob("transcript_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return []
    raw = json.loads(candidates[0].read_text(encoding="utf-8"))
    words: list[WordTiming] = []
    for seg in raw.get("segments", []):
        for w in seg.get("words", []):
            words.append(
                WordTiming(
                    word=str(w["word"]),
                    start=float(w["start"]),
                    end=float(w["end"]),
                    probability=float(w.get("probability", 1.0)),
                )
            )
    return words


def _find_cached_video(video_id: str) -> Path:
    dl_dir = DOWNLOADS_DIR / video_id
    for name in ("video_1080p.mp4", "video_720p.mp4"):
        p = dl_dir / name
        if p.exists():
            return p
    mp4s = sorted(dl_dir.glob("*.mp4")) if dl_dir.exists() else []
    if mp4s:
        return mp4s[0]
    raise HTTPException(status_code=404, detail="Cached source video not found on disk. Re-run the job to re-download.")


# --- Health ---


@app.get("/api/health")
def health() -> dict[str, Any]:
    dep = check_dependencies(raise_on_missing=False)
    return {
        "status": "ok" if dep.ok else "missing_dependencies",
        "ffmpeg": dep.ffmpeg_version,
        "yt_dlp": dep.yt_dlp_version,
        "hw_encoder": dep.hw_encoder,
        "gemini_configured": bool(get_gemini_api_key()),
    }


# --- Jobs ---


@app.post("/api/jobs")
def create_job(req: CreateJobRequest) -> dict[str, Any]:
    try:
        video_id = extract_video_id(req.url)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.options.min_clip_sec >= req.options.max_clip_sec:
        raise HTTPException(status_code=400, detail="min_clip_sec must be less than max_clip_sec.")

    max_h = 720 if str(req.options.quality).startswith("720") else 1080
    cached_ready = (DOWNLOADS_DIR / video_id / f"video_{max_h}p.mp4").exists() and (
        DOWNLOADS_DIR / video_id / "info.json"
    ).exists()
    use_relay = is_relay_download_mode() and not video_id.startswith("up_") and not cached_ready

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = Job(
        id=job_id,
        url=req.url.strip(),
        video_id=video_id,
        status="awaiting_upload" if use_relay else "queued",
        stage="downloading" if use_relay else "queued",
        progress=2.0 if use_relay else 0.0,
        status_message="Waiting for Mac downloader relay..." if use_relay else "Queued for processing",
        options_json=req.options.model_dump_json(),
        logs_json="[]",
    )
    with get_session() as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        data = serialize_job(job, clips=[])

    if not use_relay:
        worker.enqueue(job_id)
    return data


@app.post("/api/jobs/upload")
async def create_upload_job(
    file: UploadFile = File(...),
    options: str = Form("{}"),
    title: str = Form(""),
) -> dict[str, Any]:
    """Create a job from an uploaded video file (works without any YouTube access)."""
    import subprocess

    from app.config import MAX_DURATION_MINUTES
    from app.models import JobOptionsIn
    from app.services.checks import find_executable

    try:
        opts = JobOptionsIn.model_validate_json(options or "{}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid options: {exc}") from exc
    if opts.min_clip_sec >= opts.max_clip_sec:
        raise HTTPException(status_code=400, detail="min_clip_sec must be less than max_clip_sec.")

    uid = f"up_{uuid.uuid4().hex[:12]}"
    video_dir = DOWNLOADS_DIR / uid
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / "source.mp4"

    try:
        with video_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as exc:
        shutil.rmtree(video_dir, ignore_errors=True)
        raise HTTPException(status_code=507, detail=f"Could not save upload: {exc}") from exc

    ffprobe = find_executable("ffprobe")
    duration = 0.0
    if ffprobe:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video_path)],
            capture_output=True, text=True, timeout=60,
        )
        try:
            duration = float(res.stdout.strip())
        except ValueError:
            duration = 0.0
    if duration <= 0:
        shutil.rmtree(video_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="That file is not a readable video.")
    if duration > MAX_DURATION_MINUTES * 60:
        shutil.rmtree(video_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Video exceeds the {MAX_DURATION_MINUTES}-minute limit.")

    display_title = (title or "").strip() or Path(file.filename or "Uploaded video").stem
    (video_dir / "info.json").write_text(
        json.dumps({"title": display_title, "channel": "Uploaded file", "duration": duration}),
        encoding="utf-8",
    )

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = Job(
        id=job_id,
        url=f"upload://{uid}",
        video_id=uid,
        video_title=display_title,
        channel="Uploaded file",
        duration=duration,
        status="queued",
        stage="queued",
        status_message="Queued for processing",
        options_json=opts.model_dump_json(),
        logs_json="[]",
    )
    with get_session() as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        data = serialize_job(job, clips=[])
    worker.enqueue(job_id)
    return data


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    with get_session() as session:
        jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()  # type: ignore[arg-type]
        result = []
        for j in jobs:
            clips = session.exec(select(Clip).where(Clip.job_id == j.id)).all()
            result.append(serialize_job(j, clip_count=len(clips)))
        return result


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        clips = session.exec(select(Clip).where(Clip.job_id == job_id)).all()
        return serialize_job(job, clips=list(clips))


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status == "running":
            raise HTTPException(status_code=409, detail="Job is already running")
        raw_opts = json.loads(job.options_json or "{}")
        max_h = 720 if str(raw_opts.get("quality", "1080p")).startswith("720") else 1080
        cached_ready = (DOWNLOADS_DIR / job.video_id / f"video_{max_h}p.mp4").exists() and (
            DOWNLOADS_DIR / job.video_id / "info.json"
        ).exists()
        use_relay = is_relay_download_mode() and not job.video_id.startswith("up_") and not cached_ready

        job.status = "awaiting_upload" if use_relay else "queued"
        job.stage = "downloading" if use_relay else "queued"
        job.progress = 2.0 if use_relay else 0.0
        job.error_message = None
        job.status_message = (
            "Waiting for Mac downloader relay..." if use_relay else "Re-queued for processing"
        )
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
        session.refresh(job)
        data = serialize_job(job, clips=[])

    if not use_relay:
        worker.enqueue(job_id)
    return data


# --- Mac Downloader Relay Endpoints ---


def _check_relay_auth(x_relay_secret: Optional[str]) -> None:
    expected = get_relay_secret()
    if expected and (x_relay_secret or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid relay secret")


@app.get("/api/relay/pending")
def relay_pending(x_relay_secret: Optional[str] = Header(default=None)) -> list[dict[str, Any]]:
    _check_relay_auth(x_relay_secret)
    with get_session() as session:
        jobs = session.exec(
            select(Job).where(Job.status == "awaiting_upload").order_by(Job.created_at.asc())  # type: ignore[arg-type]
        ).all()
        return [serialize_job(j, clip_count=0) for j in jobs]


@app.post("/api/relay/jobs/{job_id}/claim")
def relay_claim(
    job_id: str,
    message: str = Form("Downloading video on Mac..."),
    progress: float = Form(6.0),
    x_relay_secret: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_relay_auth(x_relay_secret)
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in ("awaiting_upload", "running"):
            raise HTTPException(status_code=409, detail=f"Job is {job.status}")
        job.status = "running"
        job.stage = "downloading"
        job.progress = progress
        job.status_message = message
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
    worker.publish_event(
        job_id,
        {
            "type": "progress",
            "status": "running",
            "stage": "downloading",
            "progress": progress,
            "message": message,
        },
    )
    return {"ok": True}


@app.post("/api/relay/jobs/{job_id}/upload")
async def relay_upload(
    job_id: str,
    file: UploadFile = File(...),
    info_json: str = Form("{}"),
    quality: str = Form("1080p"),
    x_relay_secret: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_relay_auth(x_relay_secret)
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        video_id = job.video_id

    max_h = 720 if str(quality).startswith("720") else 1080
    video_dir = DOWNLOADS_DIR / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = video_dir / f"video_{max_h}p.tmp.mp4"
    final_path = video_dir / f"video_{max_h}p.mp4"

    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        tmp_path.rename(final_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=507, detail=f"Failed to save relayed video: {exc}") from exc

    info: dict[str, Any] = {}
    try:
        info = json.loads(info_json or "{}")
    except Exception:
        info = {}
    (video_dir / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if info.get("title"):
            job.video_title = str(info["title"])
        if info.get("channel") or info.get("uploader"):
            job.channel = str(info.get("channel") or info.get("uploader"))
        if info.get("duration"):
            job.duration = float(info["duration"])
        if info.get("thumbnail"):
            job.thumbnail_url = str(info["thumbnail"])
        job.status = "queued"
        job.stage = "downloading"
        job.progress = 16.0
        job.status_message = "Uploaded from Mac — starting Oracle processing..."
        job.updated_at = utcnow()
        session.add(job)
        session.commit()

    worker.publish_event(
        job_id,
        {
            "type": "progress",
            "status": "running",
            "stage": "downloading",
            "progress": 16.0,
            "message": "Uploaded from Mac — starting Oracle processing...",
        },
    )
    worker.enqueue(job_id)
    return {"ok": True, "job_id": job_id}


@app.post("/api/relay/jobs/{job_id}/fail")
def relay_fail(
    job_id: str,
    error: str = Form("Mac downloader failed"),
    x_relay_secret: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_relay_auth(x_relay_secret)
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job.status = "failed"
        job.stage = "failed"
        job.error_message = error
        job.status_message = "Download failed on Mac"
        job.updated_at = utcnow()
        session.add(job)
        session.commit()
    worker.publish_event(
        job_id,
        {
            "type": "failed",
            "status": "failed",
            "stage": "failed",
            "progress": 0.0,
            "message": error,
            "error": error,
        },
    )
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    """Server-Sent Events stream for live job progress and logs."""
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        initial = {
            "type": "snapshot",
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "message": job.status_message,
            "error": job.error_message,
        }

    async def event_generator():
        q = worker.subscribe(job_id)
        try:
            yield f"data: {json.dumps(initial)}\n\n"
            if initial["status"] in ("completed", "failed"):
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("status") in ("completed", "failed"):
                        return
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            worker.unsubscribe(job_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        clips = session.exec(select(Clip).where(Clip.job_id == job_id)).all()
        for c in clips:
            session.delete(c)
        session.delete(job)
        session.commit()

    out_dir = OUTPUTS_DIR / sanitize_filename(job_id)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    return {"deleted": True, "job_id": job_id}


# --- Clips: Download, Bulk ZIP, Re-render, Metadata ---


DOWNLOAD_MAP: dict[str, tuple[str, str, str]] = {
    "mp4": ("clip.mp4", "video/mp4", ".mp4"),
    "mp4_nocaptions": ("clip_nocaptions.mp4", "video/mp4", "_nocaptions.mp4"),
    "mp3": ("audio.mp3", "audio/mpeg", ".mp3"),
    "srt": ("captions.srt", "text/plain; charset=utf-8", ".srt"),
    "thumb": ("thumbnail.jpg", "image/jpeg", ".jpg"),
    "meta": ("metadata.txt", "text/plain; charset=utf-8", "_metadata.txt"),
}


@app.get("/api/clips/{clip_id}/download")
def download_clip(
    clip_id: str,
    type: str = Query(default="mp4", pattern=r"^(mp4|mp4_nocaptions|mp3|srt|thumb|meta)$"),
    inline: bool = Query(default=False),
):
    with get_session() as session:
        clip = session.get(Clip, clip_id)
        if not clip:
            raise HTTPException(status_code=404, detail="Clip not found")
        job_id = clip.job_id
        clip_dir = Path(clip.clip_dir)
        meta = json.loads(clip.metadata_json or "{}")
        rank = clip.rank

    fname, media_type, ext = DOWNLOAD_MAP[type]
    file_path = clip_dir / fname

    title = (meta.get("youtube_shorts") or {}).get("title") or f"clip_{rank:02d}"
    safe = sanitize_filename(f"clip_{rank:02d}_{title}", max_len=64)
    download_name = f"{safe}{ext}"

    if file_path.exists():
        if inline:
            return FileResponse(path=str(file_path), media_type=media_type)
        return FileResponse(path=str(file_path), media_type=media_type, filename=download_name)

    if is_supabase_enabled():
        object_path = f"{job_id}/{clip_dir.name}/{fname}"
        signed = create_signed_url(
            object_path,
            expires_in=3600,
            download_name=None if inline else download_name,
        )
        if signed:
            return RedirectResponse(url=signed, status_code=307)

    raise HTTPException(status_code=404, detail=f"Requested asset ({type}) not found locally or in Supabase Storage")


class _StreamBuffer(io.RawIOBase):
    """Non-seekable writable buffer that yields chunks for zero-disk streaming ZIPs."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        self._buf.extend(b)
        return len(b)

    def drain(self) -> bytes:
        if not self._buf:
            return b""
        data = bytes(self._buf)
        self._buf.clear()
        return data


@app.post("/api/jobs/{job_id}/zip")
def download_job_zip(job_id: str, req: ZipRequest):
    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        clips = list(session.exec(select(Clip).where(Clip.job_id == job_id)).all())

    if req.clip_ids:
        wanted = set(req.clip_ids)
        clips = [c for c in clips if c.id in wanted]
    clips.sort(key=lambda c: c.rank)

    if not clips:
        raise HTTPException(status_code=400, detail="No clips selected for ZIP export.")

    entries: list[tuple[Optional[Path], Optional[str], str]] = []
    use_cloud = is_supabase_enabled()
    for clip in clips:
        cdir = Path(clip.clip_dir)
        meta = json.loads(clip.metadata_json or "{}")
        title = (meta.get("youtube_shorts") or {}).get("title") or f"clip_{clip.rank:02d}"
        folder = sanitize_filename(f"clip_{clip.rank:02d}_{title}", max_len=56)
        for fname in ("clip.mp4", "captions.srt", "thumbnail.jpg", "metadata.txt"):
            fpath = cdir / fname
            if fpath.exists():
                entries.append((fpath, None, f"{folder}/{fname}"))
            elif use_cloud:
                entries.append((None, f"{job_id}/{cdir.name}/{fname}", f"{folder}/{fname}"))

    if not entries:
        raise HTTPException(status_code=404, detail="No clip files found for ZIP export.")

    def _zip_stream():
        stream = _StreamBuffer()
        with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_STORED) as zf:
            for fpath, obj_path, arcname in entries:
                if fpath is not None:
                    zinfo = zipfile.ZipInfo.from_file(fpath, arcname)
                    zinfo.compress_type = zipfile.ZIP_STORED
                    with zf.open(zinfo, "w") as dest, fpath.open("rb") as src:
                        while True:
                            chunk = src.read(256 * 1024)
                            if not chunk:
                                break
                            dest.write(chunk)
                            out = stream.drain()
                            if out:
                                yield out
                elif obj_path is not None:
                    zinfo = zipfile.ZipInfo(arcname)
                    zinfo.compress_type = zipfile.ZIP_STORED
                    try:
                        with zf.open(zinfo, "w") as dest:
                            for chunk in iter_object_chunks(obj_path):
                                dest.write(chunk)
                                out = stream.drain()
                                if out:
                                    yield out
                    except Exception as exc:
                        logger.warning("Skipping missing cloud object %s in ZIP: %s", obj_path, exc)
                out = stream.drain()
                if out:
                    yield out
        tail = stream.drain()
        if tail:
            yield tail

    zip_name = f"{sanitize_filename(job.video_title or job_id, max_len=48)}_clips.zip"
    return StreamingResponse(
        _zip_stream(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@app.post("/api/clips/{clip_id}/rerender")
def rerender_single_clip(clip_id: str, req: RerenderRequest) -> dict[str, Any]:
    with get_session() as session:
        clip = session.get(Clip, clip_id)
        if not clip:
            raise HTTPException(status_code=404, detail="Clip not found")
        job = session.get(Job, clip.job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Parent job not found")

        new_format = req.output_format or clip.output_format
        new_crop = req.crop_mode or clip.crop_mode
        new_style = req.caption_style or clip.caption_style
        new_captions = clip.captions_enabled if req.captions_enabled is None else req.captions_enabled
        new_denoise = clip.denoise_audio if req.denoise_audio is None else req.denoise_audio
        new_start = clip.start_sec if req.start_sec is None else float(req.start_sec)
        new_end = clip.end_sec if req.end_sec is None else float(req.end_sec)

        if job.duration > 0:
            new_start = max(0.0, min(new_start, job.duration - 1.0))
            new_end = max(new_start + 1.0, min(new_end, job.duration))
        if new_end <= new_start + 1.0:
            raise HTTPException(status_code=400, detail="End time must be at least 1 second after start time.")

        video_id = job.video_id
        clip_dir = Path(clip.clip_dir)

    video_path = _find_cached_video(video_id)
    words = _load_cached_words(video_id)

    try:
        render_clip(
            video_path=video_path,
            clip_dir=clip_dir,
            start_sec=new_start,
            end_sec=new_end,
            words=words,
            output_format=new_format,
            crop_mode=new_crop,
            caption_style=new_style,
            captions_enabled=new_captions,
            denoise_audio=new_denoise,
        )
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    with get_session() as session:
        clip = session.get(Clip, clip_id)
        assert clip is not None
        clip.start_sec = round(new_start, 3)
        clip.end_sec = round(new_end, 3)
        clip.duration_sec = round(new_end - new_start, 2)
        clip.output_format = new_format
        clip.crop_mode = new_crop
        clip.caption_style = new_style
        clip.captions_enabled = new_captions
        clip.denoise_audio = new_denoise
        clip.render_version += 1
        clip.updated_at = utcnow()
        session.add(clip)
        session.commit()
        session.refresh(clip)
        if is_supabase_enabled():
            upload_clip_directory(clip.job_id, Path(clip.clip_dir))
        return serialize_clip(clip)


@app.patch("/api/clips/{clip_id}/metadata")
def update_clip_metadata(clip_id: str, req: UpdateMetadataRequest) -> dict[str, Any]:
    with get_session() as session:
        clip = session.get(Clip, clip_id)
        if not clip:
            raise HTTPException(status_code=404, detail="Clip not found")
        meta = json.loads(clip.metadata_json or "{}")

        if req.youtube_shorts is not None:
            meta["youtube_shorts"] = req.youtube_shorts
        if req.instagram_reels is not None:
            meta["instagram_reels"] = req.instagram_reels
        if req.tiktok is not None:
            meta["tiktok"] = req.tiktok
        if req.on_screen_hook is not None:
            meta["on_screen_hook"] = req.on_screen_hook
        if req.alternative_titles is not None:
            meta["alternative_titles"] = req.alternative_titles

        clip.metadata_json = json.dumps(meta, ensure_ascii=False)
        clip.updated_at = utcnow()
        session.add(clip)
        session.commit()
        session.refresh(clip)

        json_path, txt_path = save_metadata_files(Path(clip.clip_dir), meta)
        if is_supabase_enabled():
            folder = Path(clip.clip_dir).name
            upload_file(json_path, f"{clip.job_id}/{folder}/metadata.json")
            upload_file(txt_path, f"{clip.job_id}/{folder}/metadata.txt")
        return serialize_clip(clip)


# --- Settings & Storage ---


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    with get_session() as session:
        settings = get_app_settings(session)
    dep = check_dependencies(raise_on_missing=False)
    dl_bytes = _dir_size_bytes(DOWNLOADS_DIR)
    work_bytes = _dir_size_bytes(WORK_DIR)
    out_bytes = _dir_size_bytes(OUTPUTS_DIR)

    return {
        **settings,
        "gemini_key_configured": bool(get_gemini_api_key()),
        "gemini_model": GEMINI_MODEL,
        "supabase_configured": is_supabase_enabled(),
        "supabase_bucket": get_supabase_bucket(),
        "supabase_auto_clean": is_auto_clean_enabled(),
        "storage_path": str(DATA_DIR),
        "scoring_weights": SCORING_WEIGHTS.as_dict(),
        "caption_presets": {k: v["label"] for k, v in CAPTION_PRESETS.items()},
        "dependencies": {
            "ok": dep.ok,
            "ffmpeg": dep.ffmpeg_version,
            "yt_dlp": dep.yt_dlp_version,
            "hw_encoder": dep.hw_encoder,
        },
        "disk_usage": {
            "downloads_bytes": dl_bytes,
            "work_bytes": work_bytes,
            "outputs_bytes": out_bytes,
            "total_bytes": dl_bytes + work_bytes + out_bytes,
        },
    }


@app.patch("/api/settings")
def patch_settings(req: UpdateSettingsRequest) -> dict[str, Any]:
    updates = req.model_dump(exclude_none=True)
    with get_session() as session:
        save_app_settings(session, updates)
    return get_settings()


@app.post("/api/storage/cleanup")
def cleanup_storage(req: CleanupRequest) -> dict[str, Any]:
    before = _dir_size_bytes(DATA_DIR)
    with get_session() as session:
        settings = get_app_settings(session)
        days = req.older_than_days if req.older_than_days is not None else int(settings["auto_delete_days"])
        cutoff = time.time() - (days * 86400)

        removed_jobs = 0
        if days == 0:
            # Delete all completed/failed job outputs
            jobs = session.exec(select(Job).where(Job.status.in_(["completed", "failed"]))).all()
        else:
            jobs = [
                j
                for j in session.exec(select(Job).where(Job.status.in_(["completed", "failed"]))).all()
                if j.updated_at.timestamp() < cutoff
            ]

        for j in jobs:
            clips = session.exec(select(Clip).where(Clip.job_id == j.id)).all()
            for c in clips:
                session.delete(c)
            session.delete(j)
            out_dir = OUTPUTS_DIR / sanitize_filename(j.id)
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            removed_jobs += 1
        session.commit()

    if req.clear_cache:
        for cache_root in (DOWNLOADS_DIR, WORK_DIR):
            if cache_root.exists():
                for child in cache_root.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)

    after = _dir_size_bytes(DATA_DIR)
    return {
        "removed_jobs": removed_jobs,
        "freed_bytes": max(0, before - after),
        "remaining_bytes": after,
    }
