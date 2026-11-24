"""Central configuration. Every secret comes from environment variables (.env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # Storage
    data_dir: Path = ROOT / "data"
    database_url: str = ""  # default: sqlite file inside data_dir

    # LLM (research brainstorming, scripts, SEO, shorts, analysis)
    llm_provider: str = "auto"  # auto | anthropic | openai | gemini | offline
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    gemini_api_key: str = ""  # free tier key from aistudio.google.com (OpenAI-compatible endpoint)
    gemini_model: str = "gemini-flash-latest"

    # Text-to-speech
    tts_provider: str = "edge"  # edge | elevenlabs | openai | espeak
    tts_fallbacks: str = "edge,espeak"
    edge_voice: str = "en-US-ChristopherNeural"
    edge_rate: str = "-6%"
    edge_pitch: str = "-6Hz"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "onyx"

    # Visuals
    image_providers: str = "wikimedia,pexels,openai,procedural"
    pexels_api_key: str = ""
    openai_image_model: str = "gpt-image-1"
    music_dir: Path = ROOT / "data" / "music"  # put licensed tracks here

    # Research
    youtube_api_key: str = ""  # Data API key, used for search/competition stats
    use_youtube_autocomplete: bool = True

    # YouTube OAuth (upload + analytics)
    google_client_secrets_file: Path = ROOT / "secrets" / "client_secret.json"
    youtube_token_file: Path = ROOT / "secrets" / "youtube_token.json"
    default_privacy: str = "private"  # private | unlisted. Public needs approval.
    contains_synthetic_media: bool = True

    # Worker
    worker_poll_seconds: float = 2.0
    job_max_attempts: int = 3
    studio_token: str = ""  # optional shared secret for the API (set when exposing the port)
    http_user_agent: str = "HistoryStudio/1.0 (self-hosted research bot)"

    def model_post_init(self, _ctx) -> None:
        # relative paths in .env are relative to the project root, not the CWD
        for f in ("data_dir", "music_dir", "google_client_secrets_file", "youtube_token_file"):
            p = getattr(self, f)
            if not p.is_absolute():
                object.__setattr__(self, f, (ROOT / p).resolve())

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'studio.db'}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.media_dir.mkdir(parents=True, exist_ok=True)
    return s
