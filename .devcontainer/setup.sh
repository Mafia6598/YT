#!/usr/bin/env bash
# One-time setup when the Codespace is created: FFmpeg, fonts, Python packages, dashboard build.
set -e
sudo apt-get update
sudo apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core fonts-liberation
pip install --no-cache-dir -r requirements.txt
(cd frontend && npm install --no-audit --no-fund && npx vite build)
mkdir -p data/music secrets
echo "Setup done. Start the studio with: bash .devcontainer/start.sh"
