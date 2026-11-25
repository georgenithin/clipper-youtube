"""Unit tests for candidate window generation, multi-signal scoring, and top-N selection."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ScoringWeights
from app.services.candidates import CandidateWindow, generate_candidate_windows
from app.services.scoring import (
    ScoredWindow,
    combine_scores,
    compute_hook_score,
    compute_replay_heat_scores,
    compute_speech_pace_score,
)
from app.services.selector import select_top_clips
from app.services.transcribe import TranscriptResult, TranscriptSegment, WordTiming


def _make_fake_transcript(num_sentences: int = 12, sec_per_sentence: float = 8.0) -> TranscriptResult:
    words: list[WordTiming] = []
    segments: list[TranscriptSegment] = []
    t = 0.0
    for s_idx in range(num_sentences):
        text_tokens = [
            "Why" if s_idx % 3 == 0 else "Here",
            "is",
            "the",
            "secret" if s_idx % 3 == 0 else "point",
            f"number",
            f"{s_idx + 1}",
            "nobody",
            "tells",
            "you",
            "about",
            "building",
            "great",
            "software" + ("?" if s_idx % 3 == 0 else "."),
        ]
        seg_words: list[WordTiming] = []
        dt = (sec_per_sentence - 0.5) / len(text_tokens)
        for tok in text_tokens:
            wt = WordTiming(word=tok, start=round(t, 3), end=round(t + dt * 0.85, 3))
            seg_words.append(wt)
            words.append(wt)
            t += dt
        segments.append(
            TranscriptSegment(
                start=seg_words[0].start,
                end=seg_words[-1].end,
                text=" ".join(text_tokens),
                words=seg_words,
            )
        )
        t += 0.5
    return TranscriptResult(
        video_id="test1234567",
        language="en",
        model_size="tiny",
        segments=segments,
        words=words,
    )


def test_candidate_windows_snap_to_sentences() -> None:
    tr = _make_fake_transcript(num_sentences=12, sec_per_sentence=8.0)
    windows = generate_candidate_windows(tr, min_sec=20.0, max_sec=45.0, overlap_ratio=0.5)
    assert len(windows) >= 3
    for w in windows:
        assert w.duration >= 20.0
        assert w.duration <= 53.0
        # Every window should end with terminal punctuation from a complete sentence
        assert w.text[-1] in ".!?"


def test_combine_scores_renormalizes_missing_signals() -> None:
    weights = ScoringWeights(llm=0.35, replay_heat=0.25, audio_energy=0.20, hook=0.10, pace=0.10)

    # All signals present
    full = combine_scores(
        {"llm": 80.0, "replay_heat": 60.0, "audio_energy": 100.0, "hook": 50.0, "pace": 70.0},
        weights=weights,
    )
    assert full == round(0.35 * 80 + 0.25 * 60 + 0.20 * 100 + 0.10 * 50 + 0.10 * 70, 2)

    # Missing llm and replay_heat -> remaining weights (0.20 + 0.10 + 0.10 = 0.40) renormalized
    partial = combine_scores(
        {"llm": None, "replay_heat": None, "audio_energy": 80.0, "hook": 60.0, "pace": 40.0},
        weights=weights,
    )
    expected = round((0.20 * 80.0 + 0.10 * 60.0 + 0.10 * 40.0) / 0.40, 2)
    assert partial == expected


def test_hook_and_pace_and_replay_scoring() -> None:
    strong_words = [
        WordTiming(word=w, start=i * 0.35, end=i * 0.35 + 0.28)
        for i, w in enumerate("Why do 90% of people make this huge mistake?".split())
    ]
    weak_words = [
        WordTiming(word=w, start=i * 1.8, end=i * 1.8 + 0.25)
        for i, w in enumerate("Um well okay so yeah.".split())
    ]
    strong_win = CandidateWindow(0, 0.0, 3.5, "Why do 90% of people make this huge mistake?", "Why do 90% of people make this huge mistake?", strong_words)
    weak_win = CandidateWindow(1, 10.0, 19.0, "Um well okay so yeah.", "Um well okay so yeah.", weak_words)

    assert compute_hook_score(strong_win) > compute_hook_score(weak_win) + 20.0
    assert compute_speech_pace_score(strong_win) > compute_speech_pace_score(weak_win)

    heatmap = [
        {"start_time": 0.0, "end_time": 10.0, "value": 0.9},
        {"start_time": 10.0, "end_time": 20.0, "value": 0.1},
    ]
    replays = compute_replay_heat_scores([strong_win, weak_win], heatmap)
    assert replays[0] is not None and replays[1] is not None
    assert replays[0] > replays[1]


def test_select_top_clips_non_overlapping_and_silence_refined() -> None:
    scored = [
        ScoredWindow(0, 10.0, 35.0, "a", "a", 92.0, 80.0, 85.0, 80.0, 75.0, 95.0, "great", "strong", True),
        # Overlaps with #0 -> must be skipped
        ScoredWindow(1, 20.0, 45.0, "b", "b", 88.0, 75.0, 80.0, 75.0, 70.0, 90.0, "overlap", "strong", True),
        # Non-overlapping -> should be chosen second
        ScoredWindow(2, 50.0, 80.0, "c", "c", 84.0, 70.0, 75.0, 75.0, 65.0, 85.0, "solid", "moderate", True),
        # Non-overlapping -> should be chosen third
        ScoredWindow(3, 95.0, 125.0, "d", "d", 79.0, 65.0, 70.0, 70.0, 60.0, 80.0, "good", "moderate", True),
    ]

    # Create synthetic RMS envelope with clear silence valleys near boundaries
    rms_times = np.linspace(0.0, 140.0, 1401)
    rms_env = np.ones_like(rms_times) * 0.5
    rms_env[np.abs(rms_times - 9.6) < 0.06] = 0.01  # Silence just before 10.0
    rms_env[np.abs(rms_times - 35.4) < 0.06] = 0.01  # Silence just after 35.0

    selected = select_top_clips(
        scored_windows=scored,
        top_n=3,
        video_duration=140.0,
        rms_env=rms_env,
        rms_times=rms_times,
        padding_sec=0.3,
    )
    assert len(selected) == 3
    assert [c.index for c in selected] == [0, 2, 3]
    # Verify non-overlap across final padded bounds
    by_start = sorted(selected, key=lambda c: c.start)
    for a, b in zip(by_start, by_start[1:]):
        assert a.end <= b.start
    # Clip 0 should have snapped near 9.6 - 0.3 = 9.3 and 35.4 + 0.3 = 35.7
    assert abs(selected[0].start - 9.3) < 0.15
    assert abs(selected[0].end - 35.7) < 0.15
