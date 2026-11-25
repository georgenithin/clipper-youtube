#!/usr/bin/env python3
"""ClipForge CLI — test the full 7-stage pipeline on a YouTube video from the terminal."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

# Ensure `app` is importable when running `python backend/cli.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.checks import check_dependencies
from app.services.pipeline import PipelineOptions, run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ClipForge pipeline on a YouTube URL.")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--clips", type=int, default=3, help="Number of clips to generate (default: 3)")
    parser.add_argument("--min-sec", type=float, default=20.0, help="Minimum clip length in seconds")
    parser.add_argument("--max-sec", type=float, default=60.0, help="Maximum clip length in seconds")
    parser.add_argument("--format", dest="output_format", default="9:16", choices=["9:16", "1:1", "16:9"])
    parser.add_argument("--crop", dest="crop_mode", default="center", choices=["center", "smart", "blur_fit"])
    parser.add_argument("--style", dest="caption_style", default="bold_yellow", choices=["bold_yellow", "clean", "minimal"])
    parser.add_argument("--no-captions", action="store_true", help="Disable burned-in captions")
    parser.add_argument("--quality", default="720p", choices=["720p", "1080p"])
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model size (tiny/base/small/medium)")
    parser.add_argument("--language", default=None, help="Optional language code (e.g. en)")
    parser.add_argument("--denoise", action="store_true", help="Apply light audio denoising")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    report = check_dependencies(raise_on_missing=True)
    print(f"Dependencies OK | encoder={report.hw_encoder}")

    job_id = f"cli_{uuid.uuid4().hex[:8]}"
    opts = PipelineOptions(
        clip_count=args.clips,
        min_clip_sec=args.min_sec,
        max_clip_sec=args.max_sec,
        output_format=args.output_format,
        crop_mode=args.crop_mode,
        caption_style=args.caption_style,
        captions_enabled=not args.no_captions,
        quality=args.quality,
        whisper_model=args.whisper_model,
        language=args.language,
        denoise_audio=args.denoise,
    )

    def _on_progress(stage: str, pct: float, msg: str) -> None:
        print(f"[{pct:5.1f}%] ({stage}) {msg}", flush=True)

    result = run_pipeline(job_id=job_id, url=args.url, options=opts, progress=_on_progress)

    print("\n=== ClipForge CLI Results ===")
    print(f"Video: {result.ingest.title} ({result.ingest.duration:.1f}s) — {result.ingest.channel}")
    print(f"Generated {len(result.clips)} clips:")
    for c in result.clips:
        yt_title = c.metadata.get("youtube_shorts", {}).get("title", "")
        print(
            f"  #{c.rank} [{c.scored.final_score:5.1f}] "
            f"{c.scored.start:6.1f}s -> {c.scored.end:6.1f}s ({c.scored.end - c.scored.start:.1f}s) | "
            f"{yt_title}\n"
            f"     Output: {c.outputs.mp4_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
