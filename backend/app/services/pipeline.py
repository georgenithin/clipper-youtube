"""End-to-end 7-stage ClipForge pipeline orchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.config import (
    DEFAULT_WHISPER_MODEL,
    MAX_DURATION_MINUTES,
    OUTPUTS_DIR,
    SCORING_WEIGHTS,
    ensure_data_dirs,
    sanitize_filename,
)
from app.services.candidates import generate_candidate_windows
from app.services.ingest import IngestResult, ingest_video
from app.services.metadata import generate_clip_metadata
from app.services.render import RenderOutputs, render_clip
from app.services.scoring import ScoredWindow, score_candidates
from app.services.selector import select_top_clips
from app.services.transcribe import TranscriptResult, transcribe_audio

logger = logging.getLogger(__name__)

ProgressFn = Optional[Callable[[str, float, str], None]]


@dataclass
class PipelineOptions:
    clip_count: int = 5
    min_clip_sec: float = 20.0
    max_clip_sec: float = 60.0
    output_format: str = "9:16"          # 9:16 | 1:1 | 16:9
    crop_mode: str = "center"            # center | smart | blur_fit
    caption_style: str = "bold_yellow"   # bold_yellow | clean | minimal
    captions_enabled: bool = True
    quality: str = "1080p"               # 720p | 1080p
    whisper_model: str = DEFAULT_WHISPER_MODEL
    language: Optional[str] = None
    denoise_audio: bool = False
    max_duration_minutes: int = MAX_DURATION_MINUTES


@dataclass
class ProducedClip:
    rank: int
    clip_dir: Path
    scored: ScoredWindow
    outputs: RenderOutputs
    metadata: dict[str, Any]


@dataclass
class PipelineResult:
    job_id: str
    ingest: IngestResult
    transcript: TranscriptResult
    clips: list[ProducedClip] = field(default_factory=list)


def run_pipeline(
    job_id: str,
    url: str,
    options: Optional[PipelineOptions] = None,
    progress: ProgressFn = None,
) -> PipelineResult:
    """Execute all 7 stages of ClipForge for a single YouTube URL."""
    ensure_data_dirs()
    opts = options or PipelineOptions()

    # Stage 1: Ingest
    ingest = ingest_video(
        url=url,
        quality=opts.quality,
        max_duration_minutes=opts.max_duration_minutes,
        progress=progress,
    )

    # Stage 2: Transcribe
    transcript = transcribe_audio(
        video_id=ingest.video_id,
        audio_wav_path=ingest.audio_wav_path,
        model_size=opts.whisper_model,
        language=opts.language,
        duration=ingest.duration,
        progress=progress,
    )

    if not transcript.words:
        raise RuntimeError(
            "No speech was detected in this video. ClipForge requires spoken audio to generate clips."
        )

    # Stage 3: Candidates
    if progress:
        progress("analyzing", 42.0, "Building sentence-aligned candidate windows...")
    windows = generate_candidate_windows(
        transcript=transcript,
        min_sec=opts.min_clip_sec,
        max_sec=opts.max_clip_sec,
    )
    if not windows:
        raise RuntimeError("Could not build any candidate clip windows from the transcript.")

    # Stage 4: Score
    scored_windows, rms_env, rms_times = score_candidates(
        video_id=ingest.video_id,
        windows=windows,
        heatmap=ingest.heatmap,
        audio_wav_path=ingest.audio_wav_path,
        video_title=ingest.title,
        channel=ingest.channel,
        weights=SCORING_WEIGHTS,
        progress=progress,
    )

    # Stage 5: Select top N non-overlapping clips
    if progress:
        progress("scoring", 65.0, f"Selecting top {opts.clip_count} non-overlapping clips...")
    selected = select_top_clips(
        scored_windows=scored_windows,
        top_n=opts.clip_count,
        video_duration=ingest.duration,
        rms_env=rms_env,
        rms_times=rms_times,
        padding_sec=0.3,
    )

    # Stage 6 & 7: Render + Metadata per clip
    job_out_dir = OUTPUTS_DIR / sanitize_filename(job_id)
    job_out_dir.mkdir(parents=True, exist_ok=True)
    produced: list[ProducedClip] = []
    total = max(len(selected), 1)

    for rank, sel in enumerate(selected, start=1):
        clip_span = 29.0 / total
        render_pct = 66.0 + (rank - 1) * clip_span
        if progress:
            progress(
                "rendering",
                round(render_pct, 1),
                f"Rendering clip {rank}/{total} ({sel.start:.1f}s - {sel.end:.1f}s)...",
            )

        clip_folder_name = f"clip_{rank:02d}"
        clip_dir = job_out_dir / clip_folder_name
        # Never overwrite an existing clip folder from a prior run
        suffix = 1
        while clip_dir.exists():
            clip_dir = job_out_dir / f"{clip_folder_name}_v{suffix}"
            suffix += 1

        outputs = render_clip(
            video_path=ingest.video_path,
            clip_dir=clip_dir,
            start_sec=sel.start,
            end_sec=sel.end,
            words=transcript.words,
            src_w=ingest.width,
            src_h=ingest.height,
            output_format=opts.output_format,
            crop_mode=opts.crop_mode,
            caption_style=opts.caption_style,
            captions_enabled=opts.captions_enabled,
            denoise_audio=opts.denoise_audio,
        )

        meta_pct = 66.0 + (rank - 0.2) * clip_span
        if progress:
            progress("metadata", round(meta_pct, 1), f"Generating metadata for clip {rank}/{total}...")
        metadata = generate_clip_metadata(
            clip_text=sel.text,
            first_sentence=sel.first_sentence,
            video_title=ingest.title,
            channel=ingest.channel,
            clip_dir=clip_dir,
        )

        produced.append(
            ProducedClip(
                rank=rank,
                clip_dir=clip_dir,
                scored=sel,
                outputs=outputs,
                metadata=metadata,
            )
        )

    if progress:
        progress("completed", 100.0, f"Finished generating {len(produced)} clips!")

    return PipelineResult(
        job_id=job_id,
        ingest=ingest,
        transcript=transcript,
        clips=produced,
    )
