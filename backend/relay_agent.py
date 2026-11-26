#!/usr/bin/env python3
"""ClipForge Mac Downloader Relay Agent.

Polls your Oracle Cloud backend for jobs in `awaiting_upload` state, downloads the
YouTube video + metadata/heatmap on your Mac (home IP + browser cookies) into a
temporary directory, uploads them to Oracle, and immediately deletes local temp files.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import MAX_DURATION_MINUTES, PROJECT_ROOT, get_yt_dlp_cookie_candidates
from app.services.checks import find_executable
from app.services.ingest import _map_ytdlp_error, extract_video_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RELAY] %(message)s")
logger = logging.getLogger("clipforge.relay")


def _run_ytdlp_with_cookies(yt_dlp: str, base_args: list[str], url: str, timeout: int) -> subprocess.CompletedProcess[str]:
    js_args = ["--js-runtimes", "node"] if find_executable("node") else []
    candidates = get_yt_dlp_cookie_candidates()
    last_res: subprocess.CompletedProcess[str] | None = None
    for c_args in candidates:
        cmd = [yt_dlp, *js_args, *c_args, *base_args, url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        last_res = res
        if res.returncode == 0:
            return res
        logger.warning("yt-dlp attempt with %s failed; trying next fallback...", c_args or "no-cookies")
    assert last_res is not None
    return last_res


def process_relay_job(client: httpx.Client, api_url: str, headers: dict[str, str], job: dict) -> None:
    job_id = job["id"]
    url = job["url"]
    opts = job.get("options") or {}
    quality = str(opts.get("quality", "1080p"))
    max_height = 720 if quality.startswith("720") else 1080

    yt_dlp = find_executable("yt-dlp")
    if not yt_dlp:
        raise RuntimeError("yt-dlp not found on Mac.")

    def _claim(msg: str, pct: float) -> None:
        try:
            client.post(
                f"{api_url}/api/relay/jobs/{job_id}/claim",
                headers=headers,
                data={"message": msg, "progress": str(pct)},
                timeout=20.0,
            )
        except Exception:
            pass

    def _fail(err: str) -> None:
        logger.error("Job %s failed: %s", job_id, err)
        try:
            client.post(
                f"{api_url}/api/relay/jobs/{job_id}/fail",
                headers=headers,
                data={"error": err},
                timeout=20.0,
            )
        except Exception:
            pass

    try:
        video_id = extract_video_id(url)
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        _claim("Mac relay: fetching YouTube metadata & heatmap...", 4.0)
        logger.info("[%s] Fetching metadata for %s", job_id, canonical_url)

        info_res = _run_ytdlp_with_cookies(
            yt_dlp,
            ["--dump-single-json", "--no-playlist", "--no-warnings"],
            canonical_url,
            timeout=90,
        )
        if info_res.returncode != 0:
            _fail(_map_ytdlp_error(info_res.stderr or info_res.stdout))
            return

        info = json.loads(info_res.stdout)
        duration = float(info.get("duration") or 0.0)
        if duration <= 0:
            _fail("Could not determine video duration (live or invalid video).")
            return
        if duration > MAX_DURATION_MINUTES * 60:
            _fail(f"Video is {duration / 60:.1f} min, exceeding the {MAX_DURATION_MINUTES}-min limit.")
            return

        title = str(info.get("title") or video_id)
        logger.info("[%s] Downloading '%s' (%.0fs, %dp) to temp folder...", job_id, title, duration, max_height)
        _claim(f"Mac relay: downloading '{title[:50]}' ({max_height}p)...", 8.0)

        with tempfile.TemporaryDirectory(prefix="cf_relay_") as tmp:
            tmp_dir = Path(tmp)
            out_tmpl = str(tmp_dir / "video.%(ext)s")
            fmt = (
                f"bestvideo[height<={max_height}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]"
            )
            dl_res = _run_ytdlp_with_cookies(
                yt_dlp,
                ["--no-playlist", "--no-warnings", "-f", fmt, "--merge-output-format", "mp4", "-o", out_tmpl],
                canonical_url,
                timeout=1800,
            )
            if dl_res.returncode != 0:
                _fail(_map_ytdlp_error(dl_res.stderr or dl_res.stdout))
                return

            mp4s = sorted(tmp_dir.glob("video*.mp4")) or sorted(tmp_dir.glob("video.*"))
            if not mp4s:
                _fail("Download finished on Mac but output video file was missing.")
                return

            video_file = mp4s[0]
            size_mb = video_file.stat().st_size / (1024 * 1024)
            logger.info("[%s] Uploading %.1f MB to Oracle Cloud (%s)...", job_id, size_mb, api_url)
            _claim(f"Mac relay: uploading {size_mb:.1f} MB to Oracle Cloud...", 12.0)

            # Keep only essential metadata fields (including heatmap!) so form field stays small
            slim_info = {
                "id": info.get("id"),
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
                "uploader": info.get("uploader"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "heatmap": info.get("heatmap") or [],
            }

            with video_file.open("rb") as f:
                up_resp = client.post(
                    f"{api_url}/api/relay/jobs/{job_id}/upload",
                    headers=headers,
                    files={"file": (f"video_{max_height}p.mp4", f, "video/mp4")},
                    data={
                        "info_json": json.dumps(slim_info),
                        "quality": f"{max_height}p",
                    },
                    timeout=httpx.Timeout(connect=30.0, read=120.0, write=1800.0, pool=30.0),
                )
            if up_resp.status_code != 200:
                _fail(f"Oracle upload rejected ({up_resp.status_code}): {up_resp.text[:200]}")
                return

            logger.info("[%s] Uploaded to Oracle! Local temp file deleted automatically.", job_id)
    except Exception as exc:
        _fail(f"Mac relay error: {exc}")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    api_url = os.getenv("ORACLE_API_URL", "https://168-107-88-109.sslip.io").strip().rstrip("/")
    secret = os.getenv("RELAY_SECRET", "").strip()
    poll_sec = float(os.getenv("RELAY_POLL_SECONDS", "3"))

    headers = {"X-Relay-Secret": secret} if secret else {}
    logger.info("ClipForge Mac Downloader Relay started -> polling %s every %.0fs", api_url, poll_sec)
    logger.info("Paste any YouTube link on your Vercel app and this Mac will download & relay it to Oracle.")

    with httpx.Client() as client:
        while True:
            try:
                resp = client.get(f"{api_url}/api/relay/pending", headers=headers, timeout=15.0)
                if resp.status_code == 200:
                    pending = resp.json()
                    for job in pending:
                        process_relay_job(client, api_url, headers, job)
                elif resp.status_code == 401:
                    logger.error("Relay secret mismatch (401). Check RELAY_SECRET in local and Oracle .env.")
                    time.sleep(10)
            except KeyboardInterrupt:
                logger.info("Relay stopped.")
                return 0
            except Exception as exc:
                logger.warning("Poll error (%s); retrying...", exc)
            time.sleep(poll_sec)


if __name__ == "__main__":
    raise SystemExit(main())
