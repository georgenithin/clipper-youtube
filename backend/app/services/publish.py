"""Direct publishing to YouTube Shorts (Data API v3) and Instagram Reels (Graph API)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from sqlmodel import Session

from app.config import PROJECT_ROOT
from app.models import AppSetting
from app.services.storage import create_signed_url, is_supabase_enabled, iter_object_chunks

logger = logging.getLogger(__name__)

YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/youtube.readonly"
)

IG_GRAPH_BASE = "https://graph.facebook.com/v21.0"


class PublishError(RuntimeError):
    """User-facing error raised when publishing to YouTube or Instagram fails."""


def _get_setting(session: Session, key: str) -> str:
    row = session.get(AppSetting, key)
    if not row:
        return ""
    try:
        val = json.loads(row.value)
        return str(val) if val is not None else ""
    except Exception:
        return str(row.value)


def _set_setting(session: Session, key: str, value: str) -> None:
    encoded = json.dumps(value)
    row = session.get(AppSetting, key)
    if row:
        row.value = encoded
    else:
        session.add(AppSetting(key=key, value=encoded))
    session.commit()


def _delete_setting(session: Session, key: str) -> None:
    row = session.get(AppSetting, key)
    if row:
        session.delete(row)
        session.commit()


def get_youtube_oauth_config() -> tuple[str, str, str]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv(
        "YOUTUBE_REDIRECT_URI",
        "https://168-107-88-109.sslip.io/api/auth/youtube/callback",
    ).strip()
    return client_id, client_secret, redirect_uri


def get_instagram_config(session: Session) -> tuple[str, str]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    token = _get_setting(session, "instagram_access_token") or os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    ig_user_id = _get_setting(session, "instagram_ig_user_id") or os.getenv("INSTAGRAM_IG_USER_ID", "").strip()
    return token, ig_user_id


def _load_youtube_accounts(session: Session) -> list[dict[str, str]]:
    """Load all connected YouTube channels, auto-migrating legacy single-account settings."""
    raw = _get_setting(session, "youtube_accounts")
    accounts: list[dict[str, str]] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                accounts = [a for a in parsed if isinstance(a, dict) and a.get("refresh_token")]
        except Exception:
            accounts = []

    if not accounts:
        legacy_rt = _get_setting(session, "youtube_refresh_token")
        if legacy_rt:
            legacy_title = _get_setting(session, "youtube_channel_title") or "YouTube Channel"
            accounts = [
                {
                    "id": "default",
                    "channel_title": legacy_title,
                    "thumbnail": "",
                    "refresh_token": legacy_rt,
                }
            ]
            _save_youtube_accounts(session, accounts)
    return accounts


def _save_youtube_accounts(session: Session, accounts: list[dict[str, str]]) -> None:
    _set_setting(session, "youtube_accounts", json.dumps(accounts))


def get_publish_status(session: Session) -> dict[str, Any]:
    client_id, client_secret, redirect_uri = get_youtube_oauth_config()
    accounts = _load_youtube_accounts(session) if (client_id and client_secret) else []
    public_accounts = [
        {
            "id": a.get("id", "default"),
            "channel_title": a.get("channel_title") or "YouTube Channel",
            "thumbnail": a.get("thumbnail") or "",
        }
        for a in accounts
    ]
    ig_token, ig_user_id = get_instagram_config(session)

    return {
        "youtube": {
            "oauth_configured": bool(client_id and client_secret),
            "connected": len(public_accounts) > 0,
            "channel_title": public_accounts[0]["channel_title"] if public_accounts else None,
            "accounts": public_accounts,
            "redirect_uri": redirect_uri,
        },
        "instagram": {
            "connected": bool(ig_token and ig_user_id),
            "ig_user_id": ig_user_id or None,
        },
    }


def build_youtube_auth_url(state: str = "") -> str:
    client_id, client_secret, redirect_uri = get_youtube_oauth_config()
    if not client_id or not client_secret:
        raise PublishError(
            "YouTube OAuth is not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env."
        )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_SCOPES,
        "access_type": "offline",
        "prompt": "select_account consent",
        "state": state,
    }
    return f"{YOUTUBE_AUTH_URL}?{urlencode(params)}"


def exchange_youtube_code(session: Session, code: str) -> dict[str, Any]:
    client_id, client_secret, redirect_uri = get_youtube_oauth_config()
    if not client_id or not client_secret:
        raise PublishError("YouTube OAuth client credentials missing in .env.")

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            YOUTUBE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise PublishError(f"YouTube OAuth token exchange failed: {resp.text[:240]}")
        tokens = resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")

        channel_id = ""
        channel_title = "YouTube Channel"
        thumbnail = ""
        try:
            ch = client.get(
                YOUTUBE_CHANNELS_URL,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if ch.status_code == 200:
                items = ch.json().get("items") or []
                if items:
                    channel_id = str(items[0].get("id") or "")
                    snip = items[0].get("snippet") or {}
                    channel_title = str(snip.get("title") or channel_title)
                    thumbnail = str((snip.get("thumbnails") or {}).get("default", {}).get("url") or "")
        except Exception:
            pass

        accounts = _load_youtube_accounts(session)
        acc_id = channel_id or f"yt_{len(accounts) + 1}"
        existing = next((a for a in accounts if a.get("id") == acc_id), None)
        if not refresh_token and existing:
            refresh_token = existing.get("refresh_token", "")
        if not refresh_token:
            raise PublishError("Google did not return a refresh token. Try reconnecting and granting consent.")

        entry = {
            "id": acc_id,
            "channel_title": channel_title,
            "thumbnail": thumbnail,
            "refresh_token": refresh_token,
        }
        accounts = [a for a in accounts if a.get("id") != acc_id]
        accounts.append(entry)
        _save_youtube_accounts(session, accounts)
        return {"connected": True, "id": acc_id, "channel_title": channel_title}


def disconnect_youtube(session: Session, account_id: Optional[str] = None) -> None:
    if account_id:
        accounts = [a for a in _load_youtube_accounts(session) if a.get("id") != account_id]
        _save_youtube_accounts(session, accounts)
        if not accounts:
            _delete_setting(session, "youtube_refresh_token")
            _delete_setting(session, "youtube_channel_title")
    else:
        _delete_setting(session, "youtube_accounts")
        _delete_setting(session, "youtube_refresh_token")
        _delete_setting(session, "youtube_channel_title")


def save_instagram_config(session: Session, access_token: str, ig_user_id: str) -> dict[str, Any]:
    _set_setting(session, "instagram_access_token", access_token.strip())
    _set_setting(session, "instagram_ig_user_id", ig_user_id.strip())
    return {"connected": bool(access_token.strip() and ig_user_id.strip()), "ig_user_id": ig_user_id.strip()}


def _refresh_youtube_access_token(session: Session, account_id: Optional[str] = None) -> str:
    client_id, client_secret, _ = get_youtube_oauth_config()
    accounts = _load_youtube_accounts(session)
    if not client_id or not client_secret or not accounts:
        raise PublishError("YouTube account is not connected. Connect it in Settings first.")

    target = next((a for a in accounts if a.get("id") == account_id), None) if account_id else accounts[0]
    if not target:
        raise PublishError("Selected YouTube channel was not found. Reconnect it in Settings.")
    refresh_token = target["refresh_token"]

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise PublishError(f"Failed to refresh YouTube token: {resp.text[:240]}")
    token = resp.json().get("access_token")
    if not token:
        raise PublishError("Google OAuth returned no access token.")
    return str(token)


def _ensure_local_video(job_id: str, clip_dir: Path, fname: str) -> tuple[Path, bool]:
    """Return (local_file_path, is_temp) downloading from Supabase Storage if cleaned locally."""
    local_path = clip_dir / fname
    if local_path.exists():
        return local_path, False
    if not is_supabase_enabled():
        raise PublishError(f"Clip file {fname} not found on disk.")

    fd, tmp_str = tempfile.mkstemp(prefix="cf_pub_", suffix=".mp4")
    os.close(fd)
    tmp_path = Path(tmp_str)
    try:
        object_path = f"{job_id}/{clip_dir.name}/{fname}"
        with tmp_path.open("wb") as out:
            for chunk in iter_object_chunks(object_path):
                out.write(chunk)
        return tmp_path, True
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise PublishError(f"Could not fetch clip from Supabase Storage: {exc}") from exc


def publish_to_youtube_shorts(
    session: Session,
    job_id: str,
    clip_dir: Path,
    metadata: dict[str, Any],
    privacy_status: str = "private",
    use_nocaptions: bool = False,
    account_id: Optional[str] = None,
) -> dict[str, Any]:
    """Upload a clip as a YouTube Short using YouTube Data API v3 resumable upload."""
    access_token = _refresh_youtube_access_token(session, account_id=account_id)
    fname = "clip_nocaptions.mp4" if use_nocaptions else "clip.mp4"
    video_path, is_temp = _ensure_local_video(job_id, clip_dir, fname)

    yt = metadata.get("youtube_shorts") or {}
    title = str(yt.get("title") or "ClipForge Short").strip()[:100]
    desc = str(yt.get("description") or "").strip()
    hashtags = [str(t).strip() for t in (yt.get("hashtags") or []) if str(t).strip()]
    if not any(t.lower() == "#shorts" for t in hashtags):
        hashtags.insert(0, "#Shorts")

    # Append hashtags to description if not already present
    missing_tags = [t for t in hashtags if t.lower() not in desc.lower()]
    if missing_tags:
        desc = (f"{desc}\n\n{' '.join(missing_tags)}").strip()

    tags_clean = [t.lstrip("#") for t in hashtags][:15]
    body = {
        "snippet": {
            "title": title,
            "description": desc[:5000],
            "tags": tags_clean,
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": privacy_status if privacy_status in ("private", "unlisted", "public") else "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        file_size = video_path.stat().st_size
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=600.0, pool=30.0)) as client:
            # 1. Initiate resumable upload session
            init_resp = client.post(
                YOUTUBE_UPLOAD_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(file_size),
                    "X-Upload-Content-Type": "video/mp4",
                },
                json=body,
            )
            if init_resp.status_code not in (200, 201):
                raise PublishError(f"YouTube upload init failed ({init_resp.status_code}): {init_resp.text[:260]}")
            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                raise PublishError("YouTube did not return a resumable upload Location header.")

            # 2. Upload MP4 bytes
            with video_path.open("rb") as f:
                up_resp = client.put(
                    upload_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "video/mp4",
                        "Content-Length": str(file_size),
                    },
                    content=f.read(),
                )
            if up_resp.status_code not in (200, 201):
                raise PublishError(f"YouTube video upload failed ({up_resp.status_code}): {up_resp.text[:260]}")
            data = up_resp.json()
            video_id = data.get("id")
            if not video_id:
                raise PublishError("YouTube upload succeeded but no video ID was returned.")
            return {
                "platform": "youtube",
                "video_id": video_id,
                "url": f"https://youtube.com/shorts/{video_id}",
                "privacy_status": body["status"]["privacyStatus"],
            }
    finally:
        if is_temp:
            video_path.unlink(missing_ok=True)


def publish_to_instagram_reels(
    session: Session,
    job_id: str,
    clip_id: str,
    clip_dir: Path,
    metadata: dict[str, Any],
    public_api_base: str = "",
    use_nocaptions: bool = False,
) -> dict[str, Any]:
    """Publish a clip as an Instagram Reel via Meta's Instagram Graph API."""
    access_token, ig_user_id = get_instagram_config(session)
    if not access_token or not ig_user_id:
        raise PublishError(
            "Instagram is not connected. Add your Instagram User ID and Access Token in Settings."
        )

    fname = "clip_nocaptions.mp4" if use_nocaptions else "clip.mp4"
    dl_type = "mp4_nocaptions" if use_nocaptions else "mp4"

    # Instagram requires a publicly reachable HTTPS MP4 URL
    video_url: Optional[str] = None
    if is_supabase_enabled():
        video_url = create_signed_url(f"{job_id}/{clip_dir.name}/{fname}", expires_in=3600)
    if not video_url and public_api_base:
        video_url = f"{public_api_base.rstrip('/')}/api/clips/{clip_id}/download?type={dl_type}&inline=true"
    if not video_url:
        raise PublishError(
            "Instagram requires a public HTTPS video URL (Supabase Storage or public backend URL)."
        )

    ig = metadata.get("instagram_reels") or {}
    caption = str(ig.get("caption") or "").strip()
    hashtags = [str(t).strip() for t in (ig.get("hashtags") or []) if str(t).strip()]
    missing_tags = [t for t in hashtags if t.lower() not in caption.lower()]
    if missing_tags:
        caption = (f"{caption}\n\n{' '.join(missing_tags)}").strip()
    caption = caption[:2200]

    with httpx.Client(timeout=60.0) as client:
        # 1. Create Reels media container
        create_resp = client.post(
            f"{IG_GRAPH_BASE}/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": access_token,
            },
        )
        if create_resp.status_code != 200:
            raise PublishError(f"Instagram container creation failed: {create_resp.text[:260]}")
        creation_id = create_resp.json().get("id")
        if not creation_id:
            raise PublishError("Instagram did not return a media creation_id.")

        # 2. Poll until Instagram finishes ingesting/processing the MP4 (up to ~120s)
        status_code = "IN_PROGRESS"
        for _ in range(30):
            time.sleep(4)
            st_resp = client.get(
                f"{IG_GRAPH_BASE}/{creation_id}",
                params={"fields": "status_code,status", "access_token": access_token},
            )
            if st_resp.status_code == 200:
                st_data = st_resp.json()
                status_code = str(st_data.get("status_code") or "")
                if status_code == "FINISHED":
                    break
                if status_code in ("ERROR", "EXPIRED"):
                    raise PublishError(f"Instagram media processing failed: {st_data}")

        if status_code != "FINISHED":
            raise PublishError(f"Instagram media processing timed out (last status: {status_code}).")

        # 3. Publish the Reel
        pub_resp = client.post(
            f"{IG_GRAPH_BASE}/{ig_user_id}/media_publish",
            data={
                "creation_id": creation_id,
                "access_token": access_token,
            },
        )
        if pub_resp.status_code != 200:
            raise PublishError(f"Instagram publish failed: {pub_resp.text[:260]}")
        media_id = pub_resp.json().get("id")

        permalink = f"https://www.instagram.com/reel/"
        if media_id:
            try:
                p_resp = client.get(
                    f"{IG_GRAPH_BASE}/{media_id}",
                    params={"fields": "permalink", "access_token": access_token},
                )
                if p_resp.status_code == 200 and p_resp.json().get("permalink"):
                    permalink = str(p_resp.json()["permalink"])
            except Exception:
                pass

        return {
            "platform": "instagram",
            "media_id": media_id,
            "url": permalink,
        }
