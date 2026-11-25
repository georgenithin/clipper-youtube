#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Updating yt-dlp..."
if [ -x "backend/.venv/bin/pip" ]; then
  backend/.venv/bin/pip install --upgrade yt-dlp
elif command -v uv >/dev/null 2>&1 && [ -x "backend/.venv/bin/python" ]; then
  uv pip install --python backend/.venv/bin/python --upgrade yt-dlp
elif command -v brew >/dev/null 2>&1; then
  brew upgrade yt-dlp || brew install yt-dlp
else
  python3 -m pip install --upgrade yt-dlp
fi

echo "Current yt-dlp version:"
if [ -x "backend/.venv/bin/yt-dlp" ]; then
  backend/.venv/bin/yt-dlp --version
else
  yt-dlp --version
fi
