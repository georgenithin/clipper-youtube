"""Stage 6c: ffmpeg clip rendering (9:16/1:1/16:9, smart/blur crop, captions, loudnorm, MP3, thumb)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from app.config import CAPTION_PRESETS
from app.services.checks import detect_video_encoder, find_executable, has_ass_filter
from app.services.smart_crop import estimate_smart_crop_x_ratio
from app.services.subtitles import _chunk_words, _resolution_for_format, generate_subtitles
from app.services.transcribe import WordTiming

logger = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Raised when ffmpeg fails to render a clip."""


@dataclass
class RenderOutputs:
    mp4_path: Path
    mp4_nocaptions_path: Path
    mp3_path: Path
    srt_path: Path
    ass_path: Path
    thumb_path: Path


def _escape_ffmpeg_sub_path(path: Path) -> str:
    """Escape file path for ffmpeg's ass= filter on macOS/Linux/Windows."""
    s = str(path.resolve()).replace("\\", "/")
    s = s.replace(":", r"\:").replace("'", r"\'")
    return s


def _build_video_filter(
    output_format: str,
    crop_mode: str,
    src_w: int,
    src_h: int,
    face_x_ratio: Optional[float],
    ass_path: Optional[Path],
) -> tuple[str, bool]:
    """Return (filter_expr, is_complex_filtergraph)."""
    sub_suffix = f",ass='{_escape_ffmpeg_sub_path(ass_path)}'" if ass_path else ""

    if output_format == "16:9":
        vf = f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black{sub_suffix}"
        return vf, False

    if output_format == "1:1":
        target_w, target_h = 1080, 1080
        if crop_mode == "blur_fit":
            fc = (
                f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},boxblur=24:4[bg];"
                f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
                f"{sub_suffix}[vout]"
            )
            return fc, True
        vf = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}{sub_suffix}"
        return vf, False

    # Default: 9:16 vertical (1080x1920)
    target_w, target_h = 1080, 1920
    if crop_mode == "blur_fit":
        fc = (
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=28:5[bg];"
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            f"{sub_suffix}[vout]"
        )
        return fc, True

    # Center or smart crop-to-fill
    cx = face_x_ratio if (crop_mode == "smart" and face_x_ratio is not None) else 0.5
    scaled_w = max(target_w, int(round(src_w * (target_h / max(src_h, 1)))))
    crop_x = int(round(cx * scaled_w - target_w / 2.0))
    crop_x = max(0, min(scaled_w - target_w, crop_x))
    vf = (
        f"scale=-2:{target_h}:flags=lanczos,"
        f"crop={target_w}:{target_h}:{crop_x}:0"
        f"{sub_suffix}"
    )
    return vf, False


def _encoder_args(encoder: str) -> list[str]:
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "4200k", "-maxrate", "5500k", "-pix_fmt", "yuv420p"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]


def _run_ffmpeg(cmd: list[str], label: str) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        err = res.stderr.strip().splitlines()[-4:] if res.stderr else ["unknown error"]
        raise RenderError(f"ffmpeg {label} failed: {' | '.join(err)}")


def _load_pil_font(size: int, bold: bool):
    from PIL import ImageFont  # type: ignore

    candidates = (
        [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    )
    for fp in candidates:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _burn_captions_pillow_overlay(
    ffmpeg: str,
    encoder: str,
    clean_mp4: Path,
    out_mp4: Path,
    words: Sequence[WordTiming],
    start_sec: float,
    end_sec: float,
    caption_style: str,
    output_format: str,
) -> None:
    """Fallback caption burn-in using Pillow RGBA banners + ffmpeg concat overlay (no libass required)."""
    from PIL import Image, ImageDraw  # type: ignore

    preset = CAPTION_PRESETS.get(caption_style, CAPTION_PRESETS["bold_yellow"])
    play_x, play_y, margin_key = _resolution_for_format(output_format)
    margin_v = int(preset[margin_key])  # type: ignore[arg-type]
    uppercase = bool(preset.get("uppercase", False))
    fontsize = int(preset["fontsize"])  # type: ignore[arg-type]
    bold = int(preset["bold"]) != 0  # type: ignore[arg-type]
    stroke_w = max(2, int(preset["outline"]))  # type: ignore[arg-type]

    hi_rgb = (255, 229, 0, 255) if caption_style == "bold_yellow" else (
        (129, 178, 255, 255) if caption_style == "clean" else (255, 255, 255, 255)
    )
    base_rgb = (255, 255, 255, 255)
    outline_rgb = (12, 12, 16, 240)

    chunks = _chunk_words(words, start_sec, end_sec, min_words=2, max_words=4)
    duration = max(0.5, end_sec - start_sec)
    banner_h = int(fontsize * 3.0)
    font = _load_pil_font(fontsize, bold=bold)

    with tempfile.TemporaryDirectory(prefix="cf_caps_") as tmp:
        tmp_dir = Path(tmp)
        blank_path = tmp_dir / "blank.png"
        Image.new("RGBA", (play_x, banner_h), (0, 0, 0, 0)).save(blank_path)

        concat_lines: list[str] = []
        cursor = 0.0
        frame_idx = 0

        for chunk in chunks:
            chunk_end = chunk[-1].end
            for active_idx, active_w in enumerate(chunk):
                ev_start = max(cursor, active_w.start)
                ev_end = chunk[active_idx + 1].start if active_idx + 1 < len(chunk) else chunk_end
                if ev_end <= ev_start:
                    ev_end = ev_start + 0.06

                if ev_start > cursor + 0.02:
                    concat_lines.append(f"file '{blank_path}'")
                    concat_lines.append(f"duration {ev_start - cursor:.3f}")

                img = Image.new("RGBA", (play_x, banner_h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                tokens = [w.word.upper() if uppercase else w.word for w in chunk]
                space_w = int(draw.textlength(" ", font=font))
                widths = [int(draw.textlength(t, font=font)) for t in tokens]
                total_w = sum(widths) + space_w * max(0, len(tokens) - 1)
                x = max(48, (play_x - total_w) // 2)
                y = max(12, (banner_h - fontsize) // 2)

                for j, tok in enumerate(tokens):
                    fill = hi_rgb if j == active_idx else base_rgb
                    draw.text(
                        (x, y),
                        tok,
                        font=font,
                        fill=fill,
                        stroke_width=stroke_w,
                        stroke_fill=outline_rgb,
                    )
                    x += widths[j] + space_w

                f_path = tmp_dir / f"cap_{frame_idx:04d}.png"
                img.save(f_path)
                frame_idx += 1
                concat_lines.append(f"file '{f_path}'")
                concat_lines.append(f"duration {ev_end - ev_start:.3f}")
                cursor = ev_end

        if cursor < duration:
            concat_lines.append(f"file '{blank_path}'")
            concat_lines.append(f"duration {max(0.05, duration - cursor):.3f}")
        # concat demuxer requires repeating last file without duration
        concat_lines.append(f"file '{blank_path}'")

        list_path = tmp_dir / "caps.ffconcat"
        list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        overlay_y = max(0, play_y - banner_h - max(0, margin_v - banner_h // 3))
        tmp_out = out_mp4.with_suffix(".tmp.mp4")

        def _build_cmd(enc: str) -> list[str]:
            return [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(clean_mp4),
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-filter_complex",
                f"[0:v][1:v]overlay=0:{overlay_y}:shortest=1[vout]",
                "-map",
                "[vout]",
                "-map",
                "0:a?",
                *_encoder_args(enc),
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(tmp_out),
            ]

        try:
            _run_ffmpeg(_build_cmd(encoder), f"pillow-caption-overlay ({encoder})")
        except RenderError:
            if encoder != "libx264":
                _run_ffmpeg(_build_cmd("libx264"), "pillow-caption-overlay (libx264)")
            else:
                raise
        tmp_out.rename(out_mp4)


def render_clip(
    video_path: Path,
    clip_dir: Path,
    start_sec: float,
    end_sec: float,
    words: Sequence[WordTiming],
    src_w: int = 1920,
    src_h: int = 1080,
    output_format: str = "9:16",
    crop_mode: str = "center",
    caption_style: str = "bold_yellow",
    captions_enabled: bool = True,
    denoise_audio: bool = False,
) -> RenderOutputs:
    """Render captioned MP4, clean (no-caption) MP4, MP3 audio, SRT/ASS, and thumbnail."""
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        raise RenderError("ffmpeg not found on PATH.")

    clip_dir.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, round(end_sec - start_sec, 3))

    ass_path = clip_dir / "captions.ass"
    srt_path = clip_dir / "captions.srt"
    mp4_path = clip_dir / "clip.mp4"
    mp4_nocaptions_path = clip_dir / "clip_nocaptions.mp4"
    mp3_path = clip_dir / "audio.mp3"
    thumb_path = clip_dir / "thumbnail.jpg"

    generate_subtitles(
        words=words,
        clip_start=start_sec,
        clip_end=end_sec,
        ass_path=ass_path,
        srt_path=srt_path,
        caption_style=caption_style,
        output_format=output_format,
    )

    face_x: Optional[float] = None
    if crop_mode == "smart" and output_format == "9:16":
        face_x = estimate_smart_crop_x_ratio(video_path, start_sec, end_sec)

    af_parts = []
    if denoise_audio:
        af_parts.append("afftdn=nf=-25")
    af_parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    af_filter = ",".join(af_parts)

    encoder = detect_video_encoder(ffmpeg)
    libass_ok = has_ass_filter(ffmpeg)

    def _encode_variant(out_file: Path, with_ass: Optional[Path]) -> None:
        tmp_out = out_file.with_suffix(".tmp.mp4")
        vfilter, is_complex = _build_video_filter(
            output_format=output_format,
            crop_mode=crop_mode,
            src_w=src_w,
            src_h=src_h,
            face_x_ratio=face_x,
            ass_path=with_ass,
        )

        def _build_cmd(enc: str) -> list[str]:
            cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_sec:.3f}",
                "-i",
                str(video_path),
                "-t",
                f"{duration:.3f}",
            ]
            if is_complex:
                cmd += ["-filter_complex", vfilter, "-map", "[vout]", "-map", "0:a?"]
            else:
                cmd += ["-vf", vfilter]
            cmd += _encoder_args(enc)
            cmd += [
                "-af",
                af_filter,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(tmp_out),
            ]
            return cmd

        try:
            _run_ffmpeg(_build_cmd(encoder), f"encode ({encoder})")
        except RenderError:
            if encoder != "libx264":
                logger.warning("Hardware encoder %s failed; retrying with libx264", encoder)
                _run_ffmpeg(_build_cmd("libx264"), "encode (libx264 fallback)")
            else:
                raise
        tmp_out.rename(out_file)

    # 1. Always render clean (no-captions) version
    _encode_variant(mp4_nocaptions_path, with_ass=None)

    # 2. Render captioned version
    if captions_enabled and words:
        if libass_ok:
            _encode_variant(mp4_path, with_ass=ass_path)
        else:
            logger.info("ffmpeg built without libass; burning captions via Pillow RGBA overlay")
            _burn_captions_pillow_overlay(
                ffmpeg=ffmpeg,
                encoder=encoder,
                clean_mp4=mp4_nocaptions_path,
                out_mp4=mp4_path,
                words=words,
                start_sec=start_sec,
                end_sec=end_sec,
                caption_style=caption_style,
                output_format=output_format,
            )
    else:
        shutil.copy2(mp4_nocaptions_path, mp4_path)

    # 3. Export loudness-normalized MP3 audio
    tmp_mp3 = mp3_path.with_suffix(".tmp.mp3")
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-af",
            af_filter,
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(tmp_mp3),
        ],
        "mp3 export",
    )
    tmp_mp3.rename(mp3_path)

    # 4. Generate best-frame thumbnail from the clean rendered clip
    tmp_thumb = thumb_path.with_suffix(".tmp.jpg")
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(mp4_nocaptions_path),
            "-vf",
            "thumbnail=n=60",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(tmp_thumb),
        ],
        "thumbnail",
    )
    tmp_thumb.rename(thumb_path)

    return RenderOutputs(
        mp4_path=mp4_path,
        mp4_nocaptions_path=mp4_nocaptions_path,
        mp3_path=mp3_path,
        srt_path=srt_path,
        ass_path=ass_path,
        thumb_path=thumb_path,
    )
