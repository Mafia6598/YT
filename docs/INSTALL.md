# Installation guide

## Requirements
- Python 3.12 or 3.13
- FFmpeg 6+ with libx264, libass, libfreetype (standard builds have these)
- Node.js 18+ (only to build the dashboard)
- Optional: `espeak-ng` (offline fallback voice), DejaVu fonts (Linux: `fonts-dejavu-core`)
- A machine with ~4 CPU cores renders a 10-minute 1080p video in roughly 10–20 minutes.

### Windows
1. Install Python from python.org (tick "Add to PATH"), Node.js LTS, and FFmpeg
   (`winget install Gyan.FFmpeg`). Restart the terminal so `ffmpeg -version` works.
2. Fonts: the renderer looks for DejaVu/Liberation in `/usr/share/fonts`. On Windows run with Docker
   (recommended) or WSL2 (Ubuntu) so paths and fonts match.

### macOS
`brew install python ffmpeg node espeak-ng` and install DejaVu fonts (`brew install --cask font-dejavu`),
or use Docker.

### Ubuntu / Debian / WSL2
```bash
sudo apt install python3 python3-pip python3-venv ffmpeg espeak-ng fonts-dejavu-core fonts-liberation nodejs npm
```

## Install
```bash
git clone <your repo> history-studio && cd history-studio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env
cd frontend && npm install && npx vite build && cd ..
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open http://localhost:8000. (`./run.sh` does the last three steps.)

Development mode for the dashboard: `cd frontend && npx vite` (port 5173, proxies `/api` to 8000).

## Docker
```bash
cp .env.example .env
mkdir -p secrets data/music
docker compose up --build -d
```
Put `client_secret.json` in `./secrets/`. For the one-time YouTube login run the auth step on your own
computer (it needs a browser) and copy `secrets/youtube_token.json` to the server.

## First run checklist
1. **Settings** page: check which integrations are green.
2. **Voice Generator**: preview the narrator. If Edge TTS fails, your network blocks
   `speech.platform.bing.com`; use ElevenLabs/OpenAI or allow that host.
3. **Topic Research**: "Find & score topics" (one category first). Open a topic to see the score breakdown.
4. "Produce video": the pipeline runs research → script and stops for your **script approval**
   (configurable gates in Settings). Edit narration inline if needed, then Approve.
5. Narration, render, SEO and thumbnails run automatically, then the video waits for **final review**
   in Video Editor. Check the license table and the fact-check list.
6. Upload Manager: upload **private** or **unlisted**. To go public type `PUBLISH` (now or scheduled).
7. After a few days: Analytics → "Sync from YouTube Analytics".

## Music and sound
Put licensed music (`.mp3`/`.wav`) in `data/music/` and record each track's license in
`data/music/LICENSES.txt` (e.g. YouTube Audio Library tracks). Without tracks the app synthesizes an
original ambient drone so there is never unlicensed music in a render.

## Production calendar
Settings → Production calendar (JSON). Set `"enabled": true` to run research daily and produce long
videos on `production_days` until `long_per_week` is reached. Everything stops at "awaiting approval".
`publish_slots` is your planned release grid; publishing itself always needs your action.

## Data and backups
Everything lives in `data/` (SQLite `studio.db` and `media/`). Back up that folder and `secrets/`.
Jobs interrupted by a crash or restart are re-queued automatically on start.
