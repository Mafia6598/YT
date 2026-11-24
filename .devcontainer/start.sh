#!/usr/bin/env bash
# Starts the studio (API + worker) on port 8000 unless it is already running.
cd "$(dirname "$0")/.."
if curl -fs http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "History Studio is already running: open the Ports tab -> Dashboard (8000)."
  exit 0
fi
[ -d frontend/dist ] || (cd frontend && npm install --no-audit --no-fund && npx vite build)
mkdir -p data/music secrets
echo "Starting History Studio. Open the Ports tab -> Dashboard (8000)."
cd backend && exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
