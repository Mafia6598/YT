# History Studio: AI production platform for a faceless history channel

Self-hosted platform that researches, writes, narrates, renders and prepares documentary
videos and Shorts for a US-audience history channel (untold/dark history, mysteries, Soviet
history, secret operations, wars, historical wealth). It prepares videos for publication;
**it never publishes publicly without your explicit approval.**

```
Topic research → Script (+ fact check) → Narration → Visuals + render → SEO → Thumbnails
      → Final review (you) → Upload private/unlisted → Publish (you type PUBLISH)
                                   ↘ Shorts (2–4 re-written vertical stories per long video)
Analytics sync → weekly growth report → revenue scenarios
```

## What is in the box

| Module | Path | Notes |
|---|---|---|
| API + worker | `backend/app/main.py`, `worker.py`, `pipeline.py` | FastAPI, SQLite (WAL), persistent job queue with retries, backoff and crash recovery; production calendar |
| Research agent | `backend/app/research/` | Wikipedia search/article/citations, Wikimedia pageviews (interest), YouTube autocomplete (demand), YouTube Data API (competition), 5-factor score with "measured vs unavailable" per factor. Topics without a real source are dropped. |
| Script generator | `backend/app/script/generator.py` | Claude (default `claude-opus-5-5`) writes long (8–15 min) or Short scripts **only from the fetched source text**: hook, intro, story sections with retention hooks, climax, conclusion, CTA; scene-by-scene visuals, timecodes, on-screen text, SFX and music cues. Every claim needs a quote from the source; a second pass flags unsupported claims. Offline extractive template if no key. |
| Voice | `backend/app/tts/` | Adapters: Edge TTS (default, free, `en-US-ChristopherNeural`), ElevenLabs, OpenAI TTS, espeak (offline fallback). Per-scene segmentation, pauses, merge, loudness normalisation, word timings. |
| Video engine | `backend/app/video/` | FFmpeg: per-scene Ken Burns moves (6 patterns), grade, vignette, grain, lower-thirds, fades; ducked background music; burned-in subtitles (+ SRT); 1920×1080 and 1080×1920. Visual sources: Wikimedia Commons (PD/CC0/CC-BY/CC-BY-SA only, with attribution), Pexels, OpenAI images, procedural plates. License ledger per asset. |
| Thumbnails | `backend/app/thumbnails/` | 3 concepts at 1280×720, heuristic score + optional Claude vision ranking, manual pick |
| SEO | `backend/app/seo/` | 3 titles, description with chapters, sources, image credits, AI disclosure, tags (≤500 chars), hashtags, pinned comment, thumbnail text |
| Shorts | `backend/app/shorts/` | Picks 2–4 moments and re-writes each as an independent vertical story with its own hook and CTA |
| YouTube | `backend/app/youtube/` | OAuth, resumable upload, thumbnail, captions, playlist, `containsSyntheticMedia`; analytics (views, watch time, AVD, % viewed, subs, CTR/impressions and revenue when available, traffic sources, retention curve) |
| Growth + revenue | `backend/app/analytics/`, `revenue/` | Weekly report from real data only; revenue scenarios from your RPM inputs; revenue ledger; affiliate links need approval |
| Dashboard | `frontend/` | React, responsive: Dashboard, Topic Research, Script, Voice, Video Editor, Thumbnails, Shorts, Upload, Analytics, Revenue, Settings. Approve / reject / edit / regenerate every stage. |

## Quick start

```bash
cp .env.example .env              # add keys you have (none are required to try it)
pip install -r requirements.txt   # Python 3.12+; also needs ffmpeg installed
./run.sh                          # builds the dashboard, starts http://localhost:8000
```
Or with Docker: `docker compose up --build`.

Full guide: [docs/INSTALL.md](docs/INSTALL.md). Keys and OAuth: [docs/API_INTEGRATIONS.md](docs/API_INTEGRATIONS.md).

## Safety rules built into the code
- `youtube.upload()` refuses any privacy other than `private`/`unlisted`.
- `youtube.publish()` requires an approval record that only the dashboard creates after you type `PUBLISH`.
- The scheduler only produces up to "awaiting approval".
- Scripts may only use facts from the fetched sources; claims without a matching quote are flagged.
- Analytics and revenue show `n/a` when YouTube doesn't provide a metric; nothing is estimated or invented.
- Affiliate links are never inserted until approved, and always with a disclosure line.
- External images are accepted only with a verified reusable license, and credited in the description.

## Tests
```bash
cd backend && python -m pytest -q                 # unit tests (fast)
RUN_RENDER=1 python -m pytest -q -k e2e           # full offline render: long video + 2 Shorts (~4 min)
```

## Status of integrations (honest)
See the table at the end of [docs/API_INTEGRATIONS.md](docs/API_INTEGRATIONS.md): what was tested in the build
environment and what still needs your credentials to be verified.
