"""Stage 2: Local word-level transcription using faster-whisper with per-video caching."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from app.config import DEFAULT_WHISPER_MODEL, WORK_DIR, ensure_data_dirs

logger = logging.getLogger(__name__)

ProgressFn = Optional[Callable[[str, float, str], None]]

_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


@dataclass
class WordTiming:
    word: str
    start: float
    end: float
    probability: float = 1.0


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[WordTiming]


@dataclass
class TranscriptResult:
    video_id: str
    language: str
    model_size: str
    segments: list[TranscriptSegment]
    words: list[WordTiming]


def _detect_device() -> tuple[str, str]:
    """Auto-detect CUDA GPU if available; otherwise use CPU with int8 quantization."""
    try:
        import ctranslate2  # type: ignore

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _get_whisper_model(model_size: str):
    from faster_whisper import WhisperModel  # type: ignore

    device, compute_type = _detect_device()
    key = (model_size, device, compute_type)
    if key not in _MODEL_CACHE:
        logger.info(
            "Loading faster-whisper model=%s device=%s compute_type=%s",
            model_size,
            device,
            compute_type,
        )
        # 1. Try local cache first so already-downloaded models never hit the network
        try:
            _MODEL_CACHE[key] = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
            return _MODEL_CACHE[key]
        except Exception:
            pass

        # 2. Download from Hugging Face Hub with retries (handles transient Errno 54 resets)
        last_exc: Optional[Exception] = None
        for attempt in range(1, 5):
            try:
                if attempt >= 2:
                    os.environ["HF_HUB_DISABLE_XET"] = "1"
                _MODEL_CACHE[key] = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                )
                return _MODEL_CACHE[key]
            except Exception as exc:
                last_exc = exc
                wait = attempt * 2
                logger.warning(
                    "Whisper model '%s' download attempt %d/4 failed (%s); retrying in %ds...",
                    model_size,
                    attempt,
                    exc,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"Could not download Whisper '{model_size}' model from Hugging Face Hub after 4 attempts: {last_exc}"
        ) from last_exc
    return _MODEL_CACHE[key]


def transcribe_audio(
    video_id: str,
    audio_wav_path: Path,
    model_size: str = DEFAULT_WHISPER_MODEL,
    language: Optional[str] = None,
    duration: float = 0.0,
    progress: ProgressFn = None,
) -> TranscriptResult:
    """Transcribe 16kHz WAV with word-level timestamps, caching results per video ID."""
    ensure_data_dirs()
    work_dir = WORK_DIR / video_id
    work_dir.mkdir(parents=True, exist_ok=True)
    lang_tag = (language or "auto").lower().strip()
    cache_path = work_dir / f"transcript_{model_size}_{lang_tag}.json"

    if cache_path.exists():
        if progress:
            progress("transcribing", 40.0, f"Loaded cached transcript ({model_size})")
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        segments = [
            TranscriptSegment(
                start=float(s["start"]),
                end=float(s["end"]),
                text=str(s["text"]),
                words=[
                    WordTiming(
                        word=str(w["word"]),
                        start=float(w["start"]),
                        end=float(w["end"]),
                        probability=float(w.get("probability", 1.0)),
                    )
                    for w in s.get("words", [])
                ],
            )
            for s in raw.get("segments", [])
        ]
        all_words = [w for seg in segments for w in seg.words]
        return TranscriptResult(
            video_id=video_id,
            language=str(raw.get("language", "en")),
            model_size=model_size,
            segments=segments,
            words=all_words,
        )

    if progress:
        progress("transcribing", 22.0, f"Loading Whisper '{model_size}' model...")
    model = _get_whisper_model(model_size)

    if progress:
        progress("transcribing", 25.0, "Transcribing audio with word-level timestamps...")

    seg_iter, info = model.transcribe(
        str(audio_wav_path),
        language=language if language and language != "auto" else None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    segments: list[TranscriptSegment] = []
    all_words: list[WordTiming] = []
    detected_lang = getattr(info, "language", None) or (language or "en")

    for seg in seg_iter:
        seg_words: list[WordTiming] = []
        if seg.words:
            for w in seg.words:
                txt = (w.word or "").strip()
                if not txt:
                    continue
                wt = WordTiming(
                    word=txt,
                    start=round(float(w.start), 3),
                    end=round(float(w.end), 3),
                    probability=round(float(getattr(w, "probability", 1.0)), 3),
                )
                seg_words.append(wt)
                all_words.append(wt)
        else:
            # Fallback if a segment lacks per-word timestamps
            tokens = (seg.text or "").strip().split()
            if tokens:
                step = max((float(seg.end) - float(seg.start)) / len(tokens), 0.05)
                for i, tok in enumerate(tokens):
                    wt = WordTiming(
                        word=tok,
                        start=round(float(seg.start) + i * step, 3),
                        end=round(float(seg.start) + (i + 1) * step, 3),
                        probability=1.0,
                    )
                    seg_words.append(wt)
                    all_words.append(wt)

        ts = TranscriptSegment(
            start=round(float(seg.start), 3),
            end=round(float(seg.end), 3),
            text=(seg.text or "").strip(),
            words=seg_words,
        )
        segments.append(ts)

        if progress and duration > 0:
            pct = 25.0 + min(seg.end / duration, 1.0) * 15.0
            progress(
                "transcribing",
                round(pct, 1),
                f"Transcribed {seg.end:.0f}s / {duration:.0f}s",
            )

    payload = {
        "video_id": video_id,
        "language": detected_lang,
        "model_size": model_size,
        "segments": [asdict(s) for s in segments],
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if progress:
        progress(
            "transcribing",
            40.0,
            f"Transcription complete ({len(all_words)} words, language={detected_lang})",
        )

    return TranscriptResult(
        video_id=video_id,
        language=detected_lang,
        model_size=model_size,
        segments=segments,
        words=all_words,
    )
