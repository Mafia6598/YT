# API integration guide

All keys go in `.env` (never in code, never in the browser). Restart the server after editing it.

## 1. Anthropic Claude (scripts, SEO, brainstorming, fact-check, thumbnail ranking) — recommended
1. Go to https://console.anthropic.com → Settings → API Keys → Create key. Add billing credit.
2. `.env`: `ANTHROPIC_API_KEY=sk-ant-...` (default model `claude-opus-5-5`).
3. Check: Settings page shows `LLM: anthropic (claude-opus-5-5)`.
Without it, scripts come from an offline extractive template that copies source sentences. That is
only for testing the pipeline: it is not original enough to publish.

Rough cost: one 10-minute script + fact-check + SEO is roughly 30–60k input and 10–20k output tokens.

### Free alternative: Google Gemini (untested with a live key)
1. https://aistudio.google.com/apikey → Create API key (free tier, no card).
2. `.env`: `GEMINI_API_KEY=...` and leave `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` empty (or set `LLM_PROVIDER=gemini`).
   Model: `GEMINI_MODEL=gemini-flash-latest`.
3. Check: Settings page shows `LLM: gemini (...)`.
The free tier has per-minute and per-day request limits; a failed job can be retried from the dashboard.
Check Google's current terms on how free-tier prompts are used. Expect weaker scripts than Claude, so
review the script and the fact-check list carefully before approving.

## 2. Voice
**Edge TTS (default, free, no key).** Uses Microsoft's online voices. Needs outbound access to
`speech.platform.bing.com`. Deep male options: `en-US-ChristopherNeural` (default), `en-US-GuyNeural`,
`en-US-EricNeural`, `en-US-RogerNeural`. Tune `EDGE_RATE` / `EDGE_PITCH`. Unofficial service: it can change
without notice, which is why fallbacks exist.

**ElevenLabs (best quality).** https://elevenlabs.io → Profile → API Keys. Pick a narrator in the Voice
Library, add it to My Voices, copy its Voice ID.
```
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
TTS_FALLBACKS=edge,espeak
```
A 10-minute script is ~9,000 characters, so check your plan's character quota.

**OpenAI TTS.** https://platform.openai.com/api-keys → `OPENAI_API_KEY`, `TTS_PROVIDER=openai`
(voice `onyx`, model `gpt-4o-mini-tts`).

**espeak-ng.** Offline and robotic; only a last-resort fallback so the pipeline never stalls.

## 3. Visuals
- **Wikimedia Commons** (no key): archive photos. Only `Public domain`, `CC0`, `CC BY`, `CC BY-SA` are
  accepted; author and license go into the description's IMAGE CREDITS automatically.
- **Pexels** (free key): https://www.pexels.com/api/ → `PEXELS_API_KEY`. Pexels License, no attribution required.
- **OpenAI images**: uses `OPENAI_API_KEY`, model `gpt-image-1`. Prompts forbid real-person likeness and text.
  Keep `CONTAINS_SYNTHETIC_MEDIA=true`.
- **Procedural**: built-in generated plates (always available, no rights issues; basic look).
Order is set by `IMAGE_PROVIDERS`. Always review the license table in Video Editor before approving.

## 4. YouTube Data API key (research: competition check)
1. https://console.cloud.google.com → create project "history-studio".
2. APIs & Services → Library → enable **YouTube Data API v3** and **YouTube Analytics API**.
3. Credentials → Create credentials → API key → restrict it to YouTube Data API v3.
4. `.env`: `YOUTUBE_API_KEY=...`
Quota: 10,000 units/day by default. Each topic competition check costs ~101 units (≈ 90 topics/day).
Turn off "YouTube competition check" on the research page to save quota.

## 5. YouTube OAuth (upload, thumbnails, playlists, captions, analytics)
1. Same Google Cloud project → APIs & Services → **OAuth consent screen**: User type External,
   app name, your email. Add yourself under **Test users**.
2. Credentials → Create credentials → **OAuth client ID** → Application type **Desktop app** →
   Download JSON → save as `secrets/client_secret.json`.
3. On a computer with a browser:
   ```bash
   cd backend && python -m app.youtube.auth
   ```
   Log in with the Google account that owns the channel, allow the scopes. This writes
   `secrets/youtube_token.json` (keep it private; it is in `.gitignore`).
4. Upload Manager should now show "Connected to <your channel>".

Important YouTube rules:
- Videos uploaded through an **unverified** API project (not audited by Google) are locked to
  **private**. Request an audit at https://support.google.com/youtube/contact/yt_api_form when you're ready
  to publish from the app. Until then, upload here and publish from YouTube Studio.
- Custom thumbnails require a phone-verified channel.
- Upload costs ~1,600 quota units; captions ~400; thumbnail ~50.
- Revenue/RPM metrics only exist for channels in the YouTube Partner Program; otherwise they show `n/a`.
- While in "Testing" status, Google refresh tokens expire after 7 days: re-run the auth step or publish
  the consent screen to "In production".

## 6. Trend inputs
- Wikipedia/Wikimedia pageviews: no key (audience interest).
- YouTube autocomplete: unofficial endpoint, no key (`USE_YOUTUBE_AUTOCOMPLETE=false` to disable).

## What was tested in the build environment

The build sandbox had FFmpeg and Python but **no internet access** to Wikipedia, Microsoft, OpenAI,
ElevenLabs, Pexels or Google suggest, and no credentials.

| Feature | Status |
|---|---|
| Job queue, retries, crash recovery, approval gates, stage chaining | Tested (unit + e2e) |
| Script generation, offline template, claim checking | Tested offline with a fixture article |
| Script generation with Claude | Code written against the current SDK, **not run** (no key) |
| TTS segmentation, pauses, merge, loudness, subtitles | Tested with espeak |
| Edge TTS / ElevenLabs / OpenAI TTS | **Not run** (network blocked) |
| Render 1920×1080 + 1080×1920, Ken Burns, grade, subtitles, ducked music | Tested (2.5-min long + two ~1-min Shorts) |
| Wikimedia / Pexels / OpenAI images | **Not run** (network blocked); procedural plates tested |
| Thumbnails (3 concepts + scoring), SEO template, chapters | Tested; Claude vision ranking not run |
| Topic scoring math | Tested with mocked sources; live Wikipedia/YouTube calls **not run** |
| YouTube upload/publish/analytics | **Not run** (needs your OAuth). Private-only and publish-approval guards tested |
| Dashboard | Built and loaded in a headless browser (desktop + phone width), no console errors |
