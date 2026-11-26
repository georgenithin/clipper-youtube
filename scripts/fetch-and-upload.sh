#!/usr/bin/env bash
# Download a YouTube video on THIS machine (your home IP + browser cookies),
# then send the file to your Oracle-hosted ClipForge backend for processing.
#
# Usage: ./scripts/fetch-and-upload.sh "<youtube-url>" [clip_count]
# Only use on videos you own or have permission to use.
set -euo pipefail

URL="${1:?Usage: $0 <youtube-url> [clip_count]}"
CLIPS="${2:-5}"
API="${CLIPFORGE_API:-https://168-107-88-109.sslip.io}"
BROWSER="${YT_BROWSER:-brave}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YTDLP="$ROOT_DIR/backend/.venv/bin/yt-dlp"
[ -x "$YTDLP" ] || YTDLP="yt-dlp"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Fetching title..."
TITLE="$("$YTDLP" --cookies-from-browser "$BROWSER" --no-playlist --print title "$URL" | head -n 1)"

echo "Downloading (720p) to temp folder..."
"$YTDLP" --cookies-from-browser "$BROWSER" --no-playlist \
  -f "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]" \
  --merge-output-format mp4 -o "$TMP_DIR/video.mp4" "$URL"

echo "Uploading to $API ..."
curl --fail-with-body -sS -X POST "$API/api/jobs/upload" \
  -F "file=@$TMP_DIR/video.mp4" \
  -F "title=$TITLE" \
  -F "options={\"clip_count\":$CLIPS,\"quality\":\"720p\"}" \
  | python3 -c 'import sys,json; j=json.load(sys.stdin); print("Job created:", j["id"])'

echo "Done. Open your ClipForge site to watch progress."
