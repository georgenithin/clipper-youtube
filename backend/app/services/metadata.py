"""Stage 7: Generate platform-ready metadata (Shorts, Reels, TikTok, hook, alt titles)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import GEMINI_MODEL, get_gemini_api_key

logger = logging.getLogger(__name__)


def _fallback_metadata(
    clip_text: str,
    first_sentence: str,
    video_title: str,
    channel: str,
) -> dict[str, Any]:
    """Generate clean, non-clickbait template metadata when GEMINI_API_KEY is absent."""
    base_hook = (first_sentence or clip_text[:80]).strip().rstrip(".")
    if len(base_hook) > 85:
        base_hook = base_hook[:82].rsplit(" ", 1)[0] + "..."
    yt_title = (base_hook if len(base_hook) >= 14 else f"{video_title[:75]} (Highlight)")[:100]

    # Extract simple topic words for hashtags
    words = re.findall(r"[A-Za-z]{4,}", f"{video_title} {clip_text[:200]}")
    stop = {
        "this", "that", "with", "from", "have", "they", "what", "when", "where", "which",
        "about", "there", "their", "would", "could", "should", "really", "actually",
        "going", "think", "because", "people", "something", "just", "like", "know", "your",
    }
    tags: list[str] = []
    for w in words:
        lw = w.lower()
        if lw not in stop and f"#{lw}" not in tags:
            tags.append(f"#{lw}")
        if len(tags) >= 6:
            break
    if not tags:
        tags = ["#insights", "#highlights", "#clips"]

    yt_hashtags = ["#Shorts"] + tags[:3]
    ig_hashtags = ["#reels", "#explore", "#shortform"] + tags[:5]

    on_screen_hook = " ".join((first_sentence or yt_title).split()[:7]).strip()

    return {
        "youtube_shorts": {
            "title": yt_title,
            "description": (
                f"{base_hook}.\n"
                f"Clip from \"{video_title}\" by {channel}.\n\n"
                f"{' '.join(yt_hashtags)}"
            ),
            "hashtags": yt_hashtags,
        },
        "instagram_reels": {
            "caption": (
                f"{base_hook}\n\n"
                f"A standout moment from {channel}'s \"{video_title}\".\n"
                "Save this for later and share your take in the comments."
            ),
            "hashtags": ig_hashtags,
        },
        "tiktok": {
            "caption": f"{base_hook} {' '.join(tags[:3])} #fyp",
        },
        "on_screen_hook": on_screen_hook[:60],
        "alternative_titles": [
            yt_title,
            f"The key takeaway from {video_title[:65]}"[:100],
            f"{channel}: {base_hook[:75]}"[:100],
        ],
    }


def generate_clip_metadata(
    clip_text: str,
    first_sentence: str,
    video_title: str,
    channel: str,
    clip_dir: Path,
) -> dict[str, Any]:
    """Call Gemini for Shorts/Reels/TikTok JSON metadata (or heuristic fallback) and save files."""
    api_key = get_gemini_api_key()
    meta: dict[str, Any] | None = None

    if api_key:
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore

            http_opts = types.HttpOptions(
                timeout=45_000,
                retry_options=types.HttpRetryOptions(attempts=2),
            )
            client = genai.Client(api_key=api_key, http_options=http_opts)
            prompt = (
                "Generate short-form social media metadata for this video clip.\n"
                f"Source Video: \"{video_title}\" by \"{channel}\"\n"
                f"Clip Transcript:\n\"\"\"{clip_text[:2000]}\"\"\"\n\n"
                "Return ONLY valid JSON with this exact structure:\n"
                "{\n"
                '  "youtube_shorts": {\n'
                '    "title": "max 100 chars, hook-driven, accurate, no clickbait lies",\n'
                '    "description": "2-3 concise lines plus 3-5 hashtags including #Shorts",\n'
                '    "hashtags": ["#Shorts", "#tag2", "#tag3"]\n'
                "  },\n"
                '  "instagram_reels": {\n'
                '    "caption": "hook line, short body, clear call to action",\n'
                '    "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]\n'
                "  },\n"
                '  "tiktok": {\n'
                '    "caption": "short punchy caption with 3-4 hashtags"\n'
                "  },\n"
                '  "on_screen_hook": "3-7 punchy words to overlay at the start of the clip",\n'
                '  "alternative_titles": ["alt 1", "alt 2", "alt 3"]\n'
                "}\n"
            )
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.5,
                ),
            )
            raw = (resp.text or "").strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "youtube_shorts" in parsed and "instagram_reels" in parsed:
                # Enforce YouTube title <= 100 chars & #Shorts presence
                yt = parsed["youtube_shorts"]
                yt["title"] = str(yt.get("title", ""))[:100]
                yt_tags = [str(t) for t in yt.get("hashtags", [])]
                if not any(t.lower() == "#shorts" for t in yt_tags):
                    yt_tags.insert(0, "#Shorts")
                yt["hashtags"] = yt_tags[:6]
                meta = parsed
        except Exception as exc:
            logger.warning("Gemini metadata generation failed (%s); using template fallback", exc)

    if meta is None:
        meta = _fallback_metadata(clip_text, first_sentence, video_title, channel)

    save_metadata_files(clip_dir, meta)
    return meta


def format_metadata_txt(meta: dict[str, Any]) -> str:
    """Format metadata dict as a copy-friendly plain text file."""
    yt = meta.get("youtube_shorts") or {}
    ig = meta.get("instagram_reels") or {}
    tt = meta.get("tiktok") or {}
    alts = meta.get("alternative_titles") or []
    hook = meta.get("on_screen_hook") or ""

    ig_tags = " ".join(ig.get("hashtags") or [])
    yt_tags = " ".join(yt.get("hashtags") or [])

    lines = [
        "=== ON-SCREEN HOOK ===",
        hook,
        "",
        "=== YOUTUBE SHORTS ===",
        f"Title: {yt.get('title', '')}",
        "",
        "Description:",
        str(yt.get("description", "")),
        f"Hashtags: {yt_tags}",
        "",
        "=== ALTERNATIVE TITLES ===",
        *[f"- {t}" for t in alts],
        "",
        "=== INSTAGRAM REELS ===",
        str(ig.get("caption", "")),
        "",
        ig_tags,
        "",
        "=== TIKTOK (OPTIONAL) ===",
        str(tt.get("caption", "")),
        "",
    ]
    return "\n".join(lines)


def save_metadata_files(clip_dir: Path, meta: dict[str, Any]) -> tuple[Path, Path]:
    """Write metadata.json and copy-friendly metadata.txt into clip_dir."""
    clip_dir.mkdir(parents=True, exist_ok=True)
    json_path = clip_dir / "metadata.json"
    txt_path = clip_dir / "metadata.txt"
    json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(format_metadata_txt(meta), encoding="utf-8")
    return json_path, txt_path
