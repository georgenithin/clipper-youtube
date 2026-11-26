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

    def _claim(
        msg: str,
        pct: float,
        speed_bps: float | None = None,
        transferred_bytes: int | None = None,
        total_bytes: int | None = None,
        transfer_mode: str = "download",
    ) -> None:
        payload: dict[str, str] = {
            "message": msg,
            "progress": str(round(pct, 1)),
            "transfer_mode": transfer_mode,
        }
        if speed_bps is not None:
            payload["speed_bps"] = str(round(speed_bps, 1))
        if transferred_bytes is not None:
            payload["transferred_bytes"] = str(int(transferred_bytes))
        if total_bytes is not None:
            payload["total_bytes"] = str(int(total_bytes))
        try:
            client.post(
                f"{api_url}/api/relay/jobs/{job_id}/claim",
                headers=headers,
                data=payload,
                timeout=15.0,
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
            # Stream yt-dlp progress so we report real-time MB/s to the dashboard
            js_args = ["--js-runtimes", "node"] if find_executable("node") else []
            dl_ok = False
            dl_err = ""
            for c_args in get_yt_dlp_cookie_candidates():
                cmd = [
                    yt_dlp,
                    *js_args,
                    *c_args,
                    "--no-playlist",
                    "--no-warnings",
                    "--newline",
                    "--progress-template",
                    "download:CF_PROG|%(progress.speed)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s",
                    "-f",
                    fmt,
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    out_tmpl,
                    canonical_url,
                ]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                last_push = 0.0
                err_lines: list[str] = []
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if line.startswith("CF_PROG|"):
                        now = time.monotonic()
                        if now - last_push >= 1.2:
                            parts = line.split("|")
                            try:
                                spd = float(parts[1]) if parts[1] not in ("NA", "None", "") else 0.0
                            except ValueError:
                                spd = 0.0
                            try:
                                dl_b = int(float(parts[2])) if parts[2] not in ("NA", "None", "") else 0
                            except ValueError:
                                dl_b = 0
                            tot_raw = parts[3] if parts[3] not in ("NA", "None", "") else parts[4]
                            try:
                                tot_b = int(float(tot_raw)) if tot_raw not in ("NA", "None", "") else 0
                            except ValueError:
                                tot_b = 0
                            if spd > 0:
                                last_push = now
                                frac = (dl_b / tot_b) if tot_b > 0 else 0.0
                                pct = 5.0 + min(frac, 1.0) * 5.0
                                spd_str = f"{spd / (1024 * 1024):.2f} MB/s" if spd >= 1024 * 1024 else f"{spd / 1024:.0f} KB/s"
                                _claim(
                                    f"Mac relay: downloading '{title[:36]}' · {spd_str}",
                                    pct,
                                    speed_bps=spd,
                                    transferred_bytes=dl_b,
                                    total_bytes=tot_b or None,
                                    transfer_mode="download",
                                )
                    elif line:
                        err_lines.append(line)
                proc.wait()
                if proc.returncode == 0:
                    dl_ok = True
                    break
                dl_err = "\n".join(err_lines[-10:])
            if not dl_ok:
                _fail(_map_ytdlp_error(dl_err))
                return

            mp4s = sorted(tmp_dir.glob("video*.mp4")) or sorted(tmp_dir.glob("video.*"))
            if not mp4s:
                _fail("Download finished on Mac but output video file was missing.")
                return

            video_file = mp4s[0]
            total_bytes = video_file.stat().st_size
            size_mb = total_bytes / (1024 * 1024)
            chunk_size = 5 * 1024 * 1024  # 5 MB chunks (proxy-safe + retryable)
            total_chunks = max(1, (total_bytes + chunk_size - 1) // chunk_size)
            logger.info(
                "[%s] Uploading %.1f MB in %d chunks to Oracle Cloud (%s)...",
                job_id,
                size_mb,
                total_chunks,
                api_url,
            )
            _claim(f"Mac relay: uploading {size_mb:.1f} MB ({total_chunks} chunks) to Oracle...", 10.0)

            uploaded_so_far = 0
            last_speed_bps: float | None = None
            with video_file.open("rb") as f:
                for idx in range(total_chunks):
                    chunk_bytes = f.read(chunk_size)
                    if not chunk_bytes:
                        break
                    last_err = ""
                    sent = False
                    for attempt in range(1, 5):
                        t0 = time.monotonic()
                        try:
                            form_data = {
                                "chunk_index": str(idx),
                                "total_chunks": str(total_chunks),
                                "quality": f"{max_height}p",
                                "transferred_bytes": str(uploaded_so_far + len(chunk_bytes)),
                                "total_bytes": str(total_bytes),
                            }
                            if last_speed_bps is not None:
                                form_data["speed_bps"] = str(round(last_speed_bps, 1))
                            resp = client.post(
                                f"{api_url}/api/relay/jobs/{job_id}/chunk",
                                headers=headers,
                                files={"file": ("chunk.bin", chunk_bytes, "application/octet-stream")},
                                data=form_data,
                                timeout=httpx.Timeout(connect=30.0, read=60.0, write=120.0, pool=30.0),
                            )
                            if resp.status_code == 200:
                                elapsed = max(time.monotonic() - t0, 0.05)
                                last_speed_bps = len(chunk_bytes) / elapsed
                                uploaded_so_far += len(chunk_bytes)
                                sent = True
                                break
                            last_err = f"HTTP {resp.status_code}: {resp.text[:160]}"
                        except Exception as exc:
                            last_err = str(exc)
                        logger.warning("[%s] Chunk %d/%d attempt %d failed (%s); retrying...", job_id, idx + 1, total_chunks, attempt, last_err)
                        time.sleep(attempt * 2)
                    if not sent:
                        _fail(f"Oracle chunk {idx + 1}/{total_chunks} upload failed: {last_err}")
                        return
                    spd_mb = (last_speed_bps or 0.0) / (1024 * 1024)
                    logger.info("[%s] Uploaded chunk %d/%d (%.0f%%) at %.2f MB/s", job_id, idx + 1, total_chunks, ((idx + 1) / total_chunks) * 100, spd_mb)

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

            done_resp = client.post(
                f"{api_url}/api/relay/jobs/{job_id}/complete",
                headers=headers,
                data={
                    "info_json": json.dumps(slim_info),
                    "quality": f"{max_height}p",
                },
                timeout=30.0,
            )
            if done_resp.status_code != 200:
                _fail(f"Oracle finalize failed ({done_resp.status_code}): {done_resp.text[:200]}")
                return

            logger.info("[%s] Upload complete! Oracle is now processing. Temp file deleted.", job_id)
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
