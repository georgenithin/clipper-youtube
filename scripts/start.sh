#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d "backend/.venv" ]; then
  echo "Backend virtualenv not found at backend/.venv."
  echo "Run: python3.11 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt"
  exit 1
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "Frontend dependencies not installed. Installing now..."
  npm --prefix frontend install
fi

if [ ! -d "node_modules" ]; then
  npm install
fi

exec npm run dev
