"""Stage 3: Build sentence-snapped sliding candidate windows (20-60s) from transcript."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.services.transcribe import TranscriptResult, WordTiming

SENTENCE_END_RE = re.compile(r"[.!?]+[\"')\]]*$")


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: list[WordTiming]


@dataclass
class CandidateWindow:
    index: int
    start: float
    end: float
    text: str
    first_sentence: str
    words: list[WordTiming]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def build_sentences(
    words: Sequence[WordTiming],
    pause_threshold: float = 0.85,
    max_words_per_sentence: int = 35,
) -> list[Sentence]:
    """Group word timings into clean sentences using punctuation and natural pauses."""
    if not words:
        return []

    sentences: list[Sentence] = []
    buf: list[WordTiming] = []

    for i, w in enumerate(words):
        buf.append(w)
        is_last = i == len(words) - 1
        next_gap = (words[i + 1].start - w.end) if not is_last else 0.0
        ends_punct = bool(SENTENCE_END_RE.search(w.word))

        should_split = (
            is_last
            or ends_punct
            or next_gap >= pause_threshold
            or (len(buf) >= max_words_per_sentence and w.word.endswith(","))
            or len(buf) >= max_words_per_sentence + 10
        )
        if should_split and buf:
            text = " ".join(x.word for x in buf).strip()
            sentences.append(
                Sentence(
                    start=buf[0].start,
                    end=buf[-1].end,
                    text=text,
                    words=list(buf),
                )
            )
            buf = []

    return sentences


def generate_candidate_windows(
    transcript: TranscriptResult,
    min_sec: float = 20.0,
    max_sec: float = 60.0,
    overlap_ratio: float = 0.5,
) -> list[CandidateWindow]:
    """Create sliding windows snapped to sentence boundaries with configurable overlap."""
    sentences = build_sentences(transcript.words)
    if not sentences:
        return []

    min_sec = max(5.0, float(min_sec))
    max_sec = max(min_sec + 5.0, float(max_sec))
    target_sec = (min_sec + max_sec) / 2.0
    step_sec = max(5.0, target_sec * (1.0 - min(max(overlap_ratio, 0.1), 0.85)))

    windows: list[CandidateWindow] = []
    seen_spans: set[tuple[float, float]] = set()
    n = len(sentences)
    i = 0

    while i < n:
        start_time = sentences[i].start
        j = i
        collected: list[Sentence] = []

        # Grow window sentence-by-sentence up to max_sec
        while j < n:
            cand_end = sentences[j].end
            dur = cand_end - start_time
            if collected and dur > max_sec:
                break
            collected.append(sentences[j])
            j += 1
            if dur >= target_sec:
                # Emit a medium-length window around target_sec AND optionally keep growing to max_sec
                span_key = (round(start_time, 2), round(cand_end, 2))
                if dur >= min_sec and span_key not in seen_spans:
                    seen_spans.add(span_key)
                    w_words = [w for s in collected for w in s.words]
                    windows.append(
                        CandidateWindow(
                            index=len(windows),
                            start=start_time,
                            end=cand_end,
                            text=" ".join(s.text for s in collected).strip(),
                            first_sentence=collected[0].text,
                            words=w_words,
                        )
                    )
                # Continue adding one more sentence if still <= max_sec for variety
                if dur >= max_sec * 0.88:
                    break

        if collected:
            end_time = collected[-1].end
            dur = end_time - start_time
            span_key = (round(start_time, 2), round(end_time, 2))
            if dur >= min_sec and dur <= max_sec + 8.0 and span_key not in seen_spans:
                seen_spans.add(span_key)
                w_words = [w for s in collected for w in s.words]
                windows.append(
                    CandidateWindow(
                        index=len(windows),
                        start=start_time,
                        end=end_time,
                        text=" ".join(s.text for s in collected).strip(),
                        first_sentence=collected[0].text,
                        words=w_words,
                    )
                )

        # Advance i by at least step_sec (always advancing by >= 1 sentence)
        next_start_target = start_time + step_sec
        next_i = i + 1
        while next_i < n and sentences[next_i].start < next_start_target:
            next_i += 1
        i = next_i

    # Fallback for very short videos (< min_sec): include full transcript as one window
    if not windows and sentences:
        all_words = [w for s in sentences for w in s.words]
        windows.append(
            CandidateWindow(
                index=0,
                start=sentences[0].start,
                end=sentences[-1].end,
                text=" ".join(s.text for s in sentences).strip(),
                first_sentence=sentences[0].text,
                words=all_words,
            )
        )

    return windows
