"""Stage 5: Top-N non-overlapping window selection + silence-boundary refinement + padding."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

import numpy as np

from app.services.scoring import ScoredWindow


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float, min_gap: float = 0.0) -> bool:
    return not (a_end + min_gap <= b_start or b_end + min_gap <= a_start)


def _snap_to_silence(
    target_time: float,
    rms_env: Optional[np.ndarray],
    rms_times: Optional[np.ndarray],
    search_before: float,
    search_after: float,
    video_duration: float,
) -> float:
    """Find the lowest-energy (quietest) frame near target_time within [target-before, target+after]."""
    if rms_env is None or rms_times is None or rms_env.size == 0:
        return max(0.0, min(video_duration, target_time))

    lo = max(0.0, target_time - search_before)
    hi = min(video_duration, target_time + search_after)
    mask = (rms_times >= lo) & (rms_times <= hi)
    if not np.any(mask):
        return max(0.0, min(video_duration, target_time))

    sub_rms = rms_env[mask]
    sub_times = rms_times[mask]
    quiet_idx = int(np.argmin(sub_rms))
    return float(sub_times[quiet_idx])


def select_top_clips(
    scored_windows: Sequence[ScoredWindow],
    top_n: int = 5,
    video_duration: float = 3600.0,
    rms_env: Optional[np.ndarray] = None,
    rms_times: Optional[np.ndarray] = None,
    padding_sec: float = 0.3,
    min_gap_sec: float = 1.0,
) -> list[ScoredWindow]:
    """Select top N non-overlapping windows, refine boundaries to silence, and add ~0.3s padding."""
    if not scored_windows or top_n <= 0:
        return []

    ordered = sorted(scored_windows, key=lambda w: w.final_score, reverse=True)
    chosen: list[ScoredWindow] = []

    for cand in ordered:
        if len(chosen) >= top_n:
            break
        if any(_overlaps(cand.start, cand.end, c.start, c.end, min_gap=min_gap_sec) for c in chosen):
            continue

        # Refine start (look slightly before start for silence) and end (look slightly after end)
        refined_start = _snap_to_silence(
            cand.start,
            rms_env,
            rms_times,
            search_before=0.7,
            search_after=0.15,
            video_duration=video_duration,
        )
        refined_end = _snap_to_silence(
            cand.end,
            rms_env,
            rms_times,
            search_before=0.15,
            search_after=0.7,
            video_duration=video_duration,
        )

        # Apply ~0.3s breathing padding clamped to video bounds
        padded_start = max(0.0, round(refined_start - padding_sec, 3))
        padded_end = min(video_duration, round(refined_end + padding_sec, 3))

        if padded_end <= padded_start + 3.0:
            padded_start = max(0.0, round(cand.start, 3))
            padded_end = min(video_duration, round(cand.end, 3))

        # Ensure padding didn't introduce overlap with already-chosen clips
        if any(_overlaps(padded_start, padded_end, c.start, c.end, min_gap=0.0) for c in chosen):
            padded_start = max(0.0, round(cand.start, 3))
            padded_end = min(video_duration, round(cand.end, 3))

        chosen.append(replace(cand, start=padded_start, end=padded_end))

    return chosen
