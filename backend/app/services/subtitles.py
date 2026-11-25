"""Stage 6a: Generate word-highlighted .ass subtitles and standard .srt files."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from app.config import CAPTION_PRESETS
from app.services.transcribe import WordTiming


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _chunk_words(
    words: Sequence[WordTiming],
    clip_start: float,
    clip_end: float,
    min_words: int = 2,
    max_words: int = 4,
) -> list[list[WordTiming]]:
    """Slice clip words into 2-4 word display chunks, respecting sentence breaks & pauses."""
    clipped: list[WordTiming] = []
    for w in words:
        if w.end <= clip_start or w.start >= clip_end:
            continue
        clipped.append(
            WordTiming(
                word=w.word,
                start=max(0.0, w.start - clip_start),
                end=max(0.05, min(clip_end - clip_start, w.end - clip_start)),
                probability=w.probability,
            )
        )

    chunks: list[list[WordTiming]] = []
    cur: list[WordTiming] = []
    for i, w in enumerate(clipped):
        cur.append(w)
        is_last = i == len(clipped) - 1
        next_gap = (clipped[i + 1].start - w.end) if not is_last else 0.0
        ends_punct = w.word.endswith((".", "!", "?", ","))
        total_chars = sum(len(x.word) for x in cur) + len(cur)

        if (
            is_last
            or len(cur) >= max_words
            or total_chars >= 24
            or (len(cur) >= min_words and (ends_punct or next_gap >= 0.45))
        ):
            chunks.append(cur)
            cur = []

    return chunks


def _resolution_for_format(output_format: str) -> tuple[int, int, str]:
    if output_format == "1:1":
        return 1080, 1080, "margin_v_1_1"
    if output_format == "16:9":
        return 1920, 1080, "margin_v_16_9"
    return 1080, 1920, "margin_v_9_16"


def generate_subtitles(
    words: Sequence[WordTiming],
    clip_start: float,
    clip_end: float,
    ass_path: Path,
    srt_path: Path,
    caption_style: str = "bold_yellow",
    output_format: str = "9:16",
) -> tuple[Path, Path]:
    """Write word-highlighted .ass and clean .srt subtitle files for a clip."""
    preset = CAPTION_PRESETS.get(caption_style, CAPTION_PRESETS["bold_yellow"])
    play_x, play_y, margin_key = _resolution_for_format(output_format)
    margin_v = int(preset[margin_key])  # type: ignore[arg-type]
    uppercase = bool(preset.get("uppercase", False))

    font = str(preset["font"])
    fontsize = int(preset["fontsize"])  # type: ignore[arg-type]
    bold = int(preset["bold"])  # type: ignore[arg-type]
    primary = str(preset["primary_color"])
    highlight = str(preset["highlight_color"])
    outline_col = str(preset["outline_color"])
    back_col = str(preset["back_color"])
    outline = int(preset["outline"])  # type: ignore[arg-type]
    shadow = int(preset["shadow"])  # type: ignore[arg-type]
    border_style = int(preset["border_style"])  # type: ignore[arg-type]

    chunks = _chunk_words(words, clip_start, clip_end, min_words=2, max_words=4)

    # --- Build ASS ---
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_x}",
        f"PlayResY: {play_y}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{fontsize},{primary},{highlight},{outline_col},{back_col},"
        f"{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,64,64,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for chunk in chunks:
        chunk_end = chunk[-1].end
        for active_idx, active_w in enumerate(chunk):
            ev_start = active_w.start
            ev_end = chunk[active_idx + 1].start if active_idx + 1 < len(chunk) else chunk_end
            if ev_end <= ev_start:
                ev_end = ev_start + 0.08

            rendered_tokens: list[str] = []
            for j, w in enumerate(chunk):
                token = _escape_ass(w.word.upper() if uppercase else w.word)
                if j == active_idx:
                    if caption_style == "bold_yellow":
                        rendered_tokens.append(f"{{\\c{highlight}\\fscx108\\fscy108}}{token}{{\\c{primary}\\fscx100\\fscy100}}")
                    else:
                        rendered_tokens.append(f"{{\\c{highlight}}}{token}{{\\c{primary}}}")
                else:
                    rendered_tokens.append(token)
            line_text = " ".join(rendered_tokens)
            ass_lines.append(
                f"Dialogue: 0,{_ass_time(ev_start)},{_ass_time(ev_end)},Default,,0,0,0,,{line_text}"
            )

    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")

    # --- Build SRT ---
    srt_blocks: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        c_start = chunk[0].start
        c_end = max(c_start + 0.1, chunk[-1].end)
        text = " ".join(w.word for w in chunk).strip()
        srt_blocks.append(f"{idx}\n{_srt_time(c_start)} --> {_srt_time(c_end)}\n{text}\n")

    srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")
    return ass_path, srt_path
