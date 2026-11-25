"""Supabase Storage integration (REST API via httpx) for cloud clip storage & local cleanup."""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

ASSET_FILES = (
    "clip.mp4",
    "clip_nocaptions.mp4",
    "audio.mp3",
    "captions.srt",
    "captions.ass",
    "thumbnail.jpg",
    "metadata.txt",
    "metadata.json",
)


def _supabase_cfg() -> tuple[str, str, str, bool]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    secret = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    bucket = os.getenv("SUPABASE_BUCKET", "clipforge").strip() or "clipforge"
    auto_clean = os.getenv("SUPABASE_AUTO_CLEAN_LOCAL", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return url, secret, bucket, auto_clean


def is_supabase_enabled() -> bool:
    url, secret, _, _ = _supabase_cfg()
    return bool(url and secret)


def is_auto_clean_enabled() -> bool:
    _, _, _, auto_clean = _supabase_cfg()
    return is_supabase_enabled() and auto_clean


def get_supabase_bucket() -> str:
    _, _, bucket, _ = _supabase_cfg()
    return bucket


def _headers(secret: str, content_type: Optional[str] = None, upsert: bool = False) -> dict[str, str]:
    h = {
        "apikey": secret,
        "Authorization": f"Bearer {secret}",
    }
    if content_type:
        h["Content-Type"] = content_type
    if upsert:
        h["x-upsert"] = "true"
    return h


def ensure_bucket() -> None:
    """Ensure the configured Supabase Storage bucket exists."""
    url, secret, bucket, _ = _supabase_cfg()
    if not url or not secret:
        return
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(f"{url}/storage/v1/bucket/{bucket}", headers=_headers(secret))
            if r.status_code == 200:
                return
            client.post(
                f"{url}/storage/v1/bucket",
                headers=_headers(secret, "application/json"),
                json={"id": bucket, "name": bucket, "public": False},
            )
    except Exception as exc:
        logger.warning("Could not verify Supabase bucket '%s': %s", bucket, exc)


def upload_file(local_path: Path, object_path: str) -> bool:
    """Upload a single file to Supabase Storage with upsert. Returns True on success."""
    url, secret, bucket, _ = _supabase_cfg()
    if not url or not secret or not local_path.exists():
        return False

    ctype, _ = mimetypes.guess_type(local_path.name)
    if not ctype:
        if local_path.suffix == ".srt" or local_path.suffix == ".ass":
            ctype = "text/plain; charset=utf-8"
        else:
            ctype = "application/octet-stream"

    encoded_path = quote(object_path.lstrip("/"), safe="/")
    endpoint = f"{url}/storage/v1/object/{bucket}/{encoded_path}"

    try:
        with local_path.open("rb") as f, httpx.Client(timeout=180.0) as client:
            resp = client.post(
                endpoint,
                headers=_headers(secret, content_type=ctype, upsert=True),
                content=f.read(),
            )
        if resp.status_code in (200, 201):
            return True
        logger.warning(
            "Supabase upload failed for %s (%d): %s",
            object_path,
            resp.status_code,
            resp.text[:240],
        )
        return False
    except Exception as exc:
        logger.warning("Supabase upload error for %s: %s", object_path, exc)
        return False


def upload_clip_directory(job_id: str, clip_dir: Path) -> bool:
    """Upload all rendered assets in clip_dir to Supabase Storage under {job_id}/{clip_dir.name}/."""
    if not is_supabase_enabled():
        return False

    ensure_bucket()
    folder = clip_dir.name
    all_ok = True
    uploaded_any = False

    for fname in ASSET_FILES:
        fpath = clip_dir / fname
        if not fpath.exists():
            continue
        ok = upload_file(fpath, f"{job_id}/{folder}/{fname}")
        if ok:
            uploaded_any = True
        else:
            all_ok = False

    return uploaded_any and all_ok


def create_signed_url(
    object_path: str,
    expires_in: int = 3600,
    download_name: Optional[str] = None,
) -> Optional[str]:
    """Generate a temporary signed URL for streaming or downloading a Supabase object."""
    url, secret, bucket, _ = _supabase_cfg()
    if not url or not secret:
        return None

    encoded_path = quote(object_path.lstrip("/"), safe="/")
    endpoint = f"{url}/storage/v1/object/sign/{bucket}/{encoded_path}"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                endpoint,
                headers=_headers(secret, "application/json"),
                json={"expiresIn": expires_in},
            )
        if resp.status_code != 200:
            return None
        signed_rel = resp.json().get("signedURL")
        if not signed_rel:
            return None
        full_url = f"{url}/storage/v1{signed_rel}" if signed_rel.startswith("/") else f"{url}/storage/v1/{signed_rel}"
        if download_name:
            sep = "&" if "?" in full_url else "?"
            full_url = f"{full_url}{sep}download={quote(download_name)}"
        return full_url
    except Exception as exc:
        logger.warning("Failed to sign Supabase URL for %s: %s", object_path, exc)
        return None


def iter_object_chunks(object_path: str, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
    """Stream an object's bytes from Supabase Storage."""
    url, secret, bucket, _ = _supabase_cfg()
    encoded_path = quote(object_path.lstrip("/"), safe="/")
    endpoint = f"{url}/storage/v1/object/{bucket}/{encoded_path}"
    with httpx.Client(timeout=180.0) as client:
        with client.stream("GET", endpoint, headers=_headers(secret)) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=chunk_size):
                if chunk:
                    yield chunk
