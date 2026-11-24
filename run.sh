#!/usr/bin/env bash
# Local start: builds the dashboard if needed and runs the API + worker on http://localhost:8000
set -e
cd "$(dirname "$0")"
[ -d frontend/dist ] || (cd frontend && npm install && npx vite build)
cd backend && exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
