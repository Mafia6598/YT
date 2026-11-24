"""Database models (SQLModel / SQLite)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


# Production status shown on the dashboard
STATUSES = [
    "researching", "writing", "narration", "rendering",
    "awaiting_approval", "approved", "uploaded", "published",
    "failed", "rejected",
]
# Ordered production stages. Each can be approved / rejected / regenerated.
STAGES = ["script", "voice", "render", "thumbnails", "seo", "shorts"]


class Topic(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    category: str = ""
    query: str = ""
    summary: str = ""
    wiki_title: str = ""
    scores: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    total_score: float = 0.0
    sources: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "new"  # new | approved | rejected | in_production
    created_at: datetime = Field(default_factory=now)


class Video(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topic.id")
    parent_id: Optional[int] = None  # Shorts point to their long video
    format: str = "long"  # long | short
    title: str = ""
    status: str = "researching"
    stages: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    research: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    script: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    audio: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    render: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    assets: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    thumbnails: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    seo: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    youtube: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: str = ""
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str
    video_id: Optional[int] = None
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "queued"  # queued | running | done | failed | cancelled
    attempts: int = 0
    max_attempts: int = 3
    run_after: datetime = Field(default_factory=now)
    error: str = ""
    log: str = ""
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class AnalyticsSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: Optional[int] = None
    youtube_id: str = ""
    day: str = ""  # YYYY-MM-DD (end of reporting window)
    window_days: int = 28
    views: Optional[int] = None
    watch_minutes: Optional[float] = None
    avg_view_duration_s: Optional[float] = None
    avg_view_pct: Optional[float] = None
    ctr: Optional[float] = None
    impressions: Optional[int] = None
    subscribers_gained: Optional[int] = None
    revenue_usd: Optional[float] = None
    rpm_usd: Optional[float] = None
    traffic_sources: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    retention: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    source: str = "youtube_analytics"  # youtube_analytics | manual
    created_at: datetime = Field(default_factory=now)


class RevenueEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    month: str  # YYYY-MM
    stream: str  # ads | affiliate | sponsorship | digital_products | other
    amount_usd: float
    note: str = ""
    created_at: datetime = Field(default_factory=now)


class AffiliateLink(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str
    disclosure: str = "This description contains affiliate links. I may earn a commission at no extra cost to you."
    approved: bool = False  # never inserted into descriptions until approved
    created_at: datetime = Field(default_factory=now)


class KV(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
