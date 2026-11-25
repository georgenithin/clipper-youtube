"""Stage 4: Multi-signal clip scoring (replay_heat, audio_energy, speech_pace, hook, llm)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

from app.config import (
    GEMINI_MODEL,
    SCORING_WEIGHTS,
    ScoringWeights,
    WORK_DIR,
    ensure_data_dirs,
    get_gemini_api_key,
)
from app.services.candidates import CandidateWindow

logger = logging.getLogger(__name__)

ProgressFn = Optional[Callable[[str, float, str], None]]

HOOK_PHRASES = (
    "secret",
    "mistake",
    "nobody tells you",
    "no one tells you",
    "how to",
    "why ",
    "the truth",
    "stop doing",
    "never ",
    "always ",
    "biggest ",
    "most people",
    "here's why",
    "heres why",
    "here is why",
    "the reason",
    "imagine ",
    "what if",
    "unpopular opinion",
    "did you know",
    "listen to this",
    "crazy",
    "surprising",
    "actually ",
)
NUMBER_RE = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million)\b", re.I)


@dataclass
class ScoredWindow:
    index: int
    start: float
    end: float
    text: str
    first_sentence: str
    final_score: float
    replay_heat: Optional[float]
    audio_energy: Optional[float]
    speech_pace: float
    hook_score: float
    llm_score: Optional[float]
    llm_reason: str
    hook_quality: str
    standalone: bool


def combine_scores(
    signals: dict[str, Optional[float]],
    weights: ScoringWeights = SCORING_WEIGHTS,
) -> float:
    """Weighted average of available signals (0-100), renormalizing if any signal is None."""
    w_map = weights.as_dict()
    total_w = 0.0
    weighted_sum = 0.0
    for key, weight in w_map.items():
        val = signals.get(key)
        if val is not None and weight > 0:
            clamped = max(0.0, min(100.0, float(val)))
            weighted_sum += clamped * weight
            total_w += weight
    if total_w <= 0:
        return 50.0
    return round(weighted_sum / total_w, 2)


def compute_replay_heat_scores(
    windows: Sequence[CandidateWindow],
    heatmap: Sequence[dict[str, float]],
) -> list[Optional[float]]:
    """Average YouTube 'Most replayed' heatmap over each window, normalized to 0-100."""
    if not heatmap:
        return [None] * len(windows)

    raw_vals: list[float] = []
    for win in windows:
        weighted = 0.0
        covered = 0.0
        for pt in heatmap:
            ov_start = max(win.start, pt["start_time"])
            ov_end = min(win.end, pt["end_time"])
            if ov_end > ov_start:
                dur = ov_end - ov_start
                weighted += pt["value"] * dur
                covered += dur
        raw_vals.append((weighted / covered) if covered > 0 else 0.0)

    max_v = max(raw_vals) if raw_vals else 0.0
    min_v = min(raw_vals) if raw_vals else 0.0
    if max_v <= 0:
        return [None] * len(windows)

    out: list[Optional[float]] = []
    span = max_v - min_v
    for v in raw_vals:
        if span > 1e-6:
            norm = 15.0 + ((v - min_v) / span) * 85.0
        else:
            norm = min(100.0, v * 100.0)
        out.append(round(max(0.0, min(100.0, norm)), 2))
    return out


def compute_audio_energy_scores(
    windows: Sequence[CandidateWindow],
    audio_wav_path: Optional[Path],
) -> tuple[list[Optional[float]], Optional[np.ndarray], Optional[np.ndarray]]:
    """Compute RMS peaks, variance, and onset/excitement proxy via librosa.

    Returns (scores, rms_envelope, rms_times) so selector can reuse the RMS envelope.
    """
    if not audio_wav_path or not audio_wav_path.exists():
        return [None] * len(windows), None, None

    try:
        import librosa  # type: ignore

        y, sr = librosa.load(str(audio_wav_path), sr=16000, mono=True)
        if y.size == 0:
            return [None] * len(windows), None, None

        hop_length = 512
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
        times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

        raw_scores: list[float] = []
        for win in windows:
            mask = (times >= win.start) & (times <= win.end)
            if not np.any(mask):
                raw_scores.append(0.0)
                continue
            seg_rms = rms[mask]
            seg_onset = onset_env[mask]
            peak_p95 = float(np.percentile(seg_rms, 95))
            mean_rms = float(np.mean(seg_rms))
            std_rms = float(np.std(seg_rms))
            excitement = float(np.percentile(seg_onset, 90))
            # Combined energy + dynamic range + vocal excitement
            raw_scores.append(0.40 * peak_p95 + 0.25 * mean_rms + 0.20 * std_rms + 0.15 * (excitement / 10.0))

        arr = np.array(raw_scores, dtype=float)
        lo, hi = float(np.min(arr)), float(np.max(arr))
        scores: list[Optional[float]] = []
        for v in arr:
            if hi - lo > 1e-6:
                norm = 20.0 + ((v - lo) / (hi - lo)) * 80.0
            else:
                norm = 55.0
            scores.append(round(float(np.clip(norm, 0.0, 100.0)), 2))
        return scores, rms, times
    except Exception as exc:
        logger.warning("Audio energy analysis failed (%s); skipping audio_energy signal", exc)
        return [None] * len(windows), None, None


def compute_speech_pace_score(window: CandidateWindow) -> float:
    """Score 0-100 based on words per second (sweet spot ~2.3-3.6 wps) and minimal long silences."""
    dur = max(window.duration, 1.0)
    words = window.words
    if not words:
        return 20.0

    wps = len(words) / dur
    # Ideal conversational punchy pace is ~2.8 wps; score smoothly around it
    if wps < 1.0:
        pace_part = max(10.0, wps * 45.0)
    elif wps <= 3.5:
        pace_part = 45.0 + ((wps - 1.0) / 2.5) * 55.0
    else:
        # Very fast speech (>4.5 wps) slightly tapers
        pace_part = max(65.0, 100.0 - (wps - 3.5) * 12.0)

    # Penalize long dead-air gaps (> 1.5s)
    long_silence_sec = 0.0
    for a, b in zip(words, words[1:]):
        gap = b.start - a.end
        if gap > 1.5:
            long_silence_sec += gap - 1.5
    silence_penalty = min(40.0, (long_silence_sec / dur) * 120.0)

    return round(max(0.0, min(100.0, pace_part - silence_penalty)), 2)


def compute_hook_score(window: CandidateWindow) -> float:
    """Heuristic 0-100 score for how compelling the opening sentence is."""
    first = (window.first_sentence or "").strip()
    if not first:
        return 30.0
    low = first.lower()
    score = 35.0

    # Question hook
    if "?" in first:
        score += 22.0
    # Starts with interrogative or imperative hook
    if re.match(r"^(why|how|what|when|stop|never|don't|dont|here's|heres|imagine|did you)\b", low):
        score += 14.0
    # Numbers / listicles / specific claims
    if NUMBER_RE.search(first):
        score += 12.0
    # High-curiosity keywords
    hits = sum(1 for kw in HOOK_PHRASES if kw in low)
    score += min(24.0, hits * 10.0)
    # Punchy opening length (5-20 words)
    wc = len(first.split())
    if 5 <= wc <= 20:
        score += 8.0
    elif wc > 32:
        score -= 8.0
    # Exclamation / emphasis
    if "!" in first:
        score += 6.0

    return round(max(0.0, min(100.0, score)), 2)


def _heuristic_reason(window: CandidateWindow, hook: float, pace: float, energy: Optional[float]) -> str:
    reasons: list[str] = []
    if hook >= 65:
        reasons.append("strong opening hook")
    if energy is not None and energy >= 70:
        reasons.append("high vocal energy")
    if pace >= 75:
        reasons.append("tight, engaging pacing")
    if not reasons:
        reasons.append("self-contained segment with clear delivery")
    return "Selected for " + ", ".join(reasons) + "."


def _batch_score_with_gemini(
    windows: Sequence[CandidateWindow],
    video_title: str,
    channel: str,
    progress: ProgressFn = None,
) -> dict[int, dict[str, Any]]:
    """Batch candidate windows to Google Gemini with a strict HTTP timeout and parse JSON scores."""
    api_key = get_gemini_api_key()
    if not api_key or not windows:
        return {}

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:
        logger.warning("google-genai SDK unavailable (%s); using heuristic scoring", exc)
        return {}

    http_opts = types.HttpOptions(
        timeout=45_000,
        retry_options=types.HttpRetryOptions(attempts=2),
    )
    client = genai.Client(api_key=api_key, http_options=http_opts)
    results: dict[int, dict[str, Any]] = {}
    batch_size = 12
    total_batches = max(1, (len(windows) + batch_size - 1) // batch_size)

    for b_idx, start_idx in enumerate(range(0, len(windows), batch_size), start=1):
        if progress:
            pct = 52.0 + ((b_idx - 1) / total_batches) * 8.0
            progress("scoring", round(pct, 1), f"Scoring batch {b_idx}/{total_batches} with Gemini...")
        batch = windows[start_idx : start_idx + batch_size]
        items_payload = [
            {
                "index": w.index,
                "duration_sec": round(w.duration, 1),
                "transcript": w.text[:1200],
            }
            for w in batch
        ]

        prompt = (
            "You are a viral short-form video editor (YouTube Shorts & Instagram Reels). "
            f"Evaluate these candidate clip excerpts from the video \"{video_title}\" by \"{channel}\".\n"
            "For EACH candidate, return a JSON array of objects with exact keys:\n"
            '  - "index": integer matching input index\n'
            '  - "score": integer 0-100 (virality, emotional punch, curiosity, clarity, retention)\n'
            '  - "reason": concise one-line explanation (max 120 chars)\n'
            '  - "hook_quality": one of "strong", "moderate", "weak"\n'
            '  - "standalone": boolean (true if understandable without seeing the rest of the video)\n'
            "Return ONLY valid JSON (an array), no markdown fences or extra commentary.\n\n"
            f"Candidates:\n{json.dumps(items_payload, ensure_ascii=False)}"
        )

        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            raw_text = (resp.text or "").strip()
            # Strip accidental markdown fences if present
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.S).strip()
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and "items" in parsed:
                parsed = parsed["items"]
            if isinstance(parsed, list):
                for entry in parsed:
                    if isinstance(entry, dict) and "index" in entry:
                        idx = int(entry["index"])
                        results[idx] = {
                            "score": max(0.0, min(100.0, float(entry.get("score", 50)))),
                            "reason": str(entry.get("reason", ""))[:160],
                            "hook_quality": str(entry.get("hook_quality", "moderate")).lower(),
                            "standalone": bool(entry.get("standalone", True)),
                        }
        except Exception as exc:
            logger.warning("Gemini batch %d/%d failed (%s); falling back to heuristics for batch", b_idx, total_batches, exc)
            if progress:
                progress("scoring", round(52.0 + (b_idx / total_batches) * 8.0, 1), f"Gemini batch {b_idx} timed out/failed; using heuristic fallback")

    return results


def score_candidates(
    video_id: str,
    windows: Sequence[CandidateWindow],
    heatmap: Sequence[dict[str, float]],
    audio_wav_path: Optional[Path],
    video_title: str = "",
    channel: str = "",
    weights: ScoringWeights = SCORING_WEIGHTS,
    use_cache: bool = True,
    progress: ProgressFn = None,
) -> tuple[list[ScoredWindow], Optional[np.ndarray], Optional[np.ndarray]]:
    """Score all candidate windows across 5 signals with caching and automatic renormalization."""
    ensure_data_dirs()
    if not windows:
        return [], None, None

    if progress:
        progress("analyzing", 45.0, f"Analyzing audio energy & pacing for {len(windows)} candidates...")

    # Compute audio energy (also gives RMS envelope for silence-refinement later)
    energy_scores, rms_env, rms_times = compute_audio_energy_scores(windows, audio_wav_path)

    # Cache key based on window boundaries + weights + whether Gemini key is present
    has_gemini = bool(get_gemini_api_key())
    sig_payload = json.dumps(
        {
            "spans": [(round(w.start, 2), round(w.end, 2)) for w in windows],
            "weights": weights.as_dict(),
            "gemini": has_gemini,
            "model": GEMINI_MODEL if has_gemini else "heuristic",
        },
        sort_keys=True,
    )
    cache_hash = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()[:12]
    cache_path = WORK_DIR / video_id / f"scores_{cache_hash}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if use_cache and cache_path.exists():
        if progress:
            progress("scoring", 60.0, "Loaded cached candidate scores")
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        scored = [ScoredWindow(**item) for item in cached]
        return scored, rms_env, rms_times

    replay_scores = compute_replay_heat_scores(windows, heatmap)
    pace_scores = [compute_speech_pace_score(w) for w in windows]
    hook_scores = [compute_hook_score(w) for w in windows]

    # Pre-rank heuristically so if there are >36 candidates, we only send the top 36 to Gemini
    llm_map: dict[int, dict[str, Any]] = {}
    if has_gemini:
        if progress:
            progress("scoring", 52.0, "Scoring top candidate moments with Gemini...")
        prelim = []
        for i, w in enumerate(windows):
            pre_score = combine_scores(
                {
                    "llm": None,
                    "replay_heat": replay_scores[i],
                    "audio_energy": energy_scores[i],
                    "hook": hook_scores[i],
                    "pace": pace_scores[i],
                },
                weights=weights,
            )
            prelim.append((pre_score, w))
        prelim.sort(key=lambda x: x[0], reverse=True)
        to_llm = [w for _, w in prelim[:36]]
        llm_map = _batch_score_with_gemini(to_llm, video_title, channel, progress=progress)
    else:
        if progress:
            progress("scoring", 55.0, "No GEMINI_API_KEY set; scoring with local heuristics...")

    scored_list: list[ScoredWindow] = []
    for i, w in enumerate(windows):
        llm_entry = llm_map.get(w.index)
        llm_score = llm_entry["score"] if llm_entry else None
        final = combine_scores(
            {
                "llm": llm_score,
                "replay_heat": replay_scores[i],
                "audio_energy": energy_scores[i],
                "hook": hook_scores[i],
                "pace": pace_scores[i],
            },
            weights=weights,
        )
        reason = (
            llm_entry["reason"]
            if (llm_entry and llm_entry.get("reason"))
            else _heuristic_reason(w, hook_scores[i], pace_scores[i], energy_scores[i])
        )
        hook_q = (
            llm_entry["hook_quality"]
            if llm_entry
            else ("strong" if hook_scores[i] >= 65 else ("moderate" if hook_scores[i] >= 45 else "weak"))
        )
        standalone = llm_entry["standalone"] if llm_entry else True

        scored_list.append(
            ScoredWindow(
                index=w.index,
                start=w.start,
                end=w.end,
                text=w.text,
                first_sentence=w.first_sentence,
                final_score=final,
                replay_heat=replay_scores[i],
                audio_energy=energy_scores[i],
                speech_pace=pace_scores[i],
                hook_score=hook_scores[i],
                llm_score=llm_score,
                llm_reason=reason,
                hook_quality=hook_q,
                standalone=standalone,
            )
        )

    scored_list.sort(key=lambda s: s.final_score, reverse=True)
    if use_cache:
        cache_path.write_text(
            json.dumps([asdict(s) for s in scored_list], indent=2),
            encoding="utf-8",
        )

    if progress:
        progress("scoring", 62.0, f"Scored {len(scored_list)} candidate windows")

    return scored_list, rms_env, rms_times
