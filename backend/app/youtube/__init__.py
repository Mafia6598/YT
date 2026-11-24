"""YouTube Data API v3 + YouTube Analytics API v2.

Safety rules enforced in code:
- upload() only accepts privacy 'private' or 'unlisted'.
- publish() (making a video public or scheduling a public release) requires an explicit
  owner approval token created from the dashboard, and is never called automatically."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..util import ProviderUnavailable

log = logging.getLogger(__name__)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    s = get_settings()
    if not s.youtube_token_file.exists():
        raise ProviderUnavailable("YouTube not connected. Run: python -m app.youtube.auth (see docs/API_INTEGRATIONS.md)")
    creds = Credentials.from_authorized_user_file(str(s.youtube_token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            s.youtube_token_file.write_text(creds.to_json())
        else:
            raise ProviderUnavailable("YouTube token invalid. Re-run: python -m app.youtube.auth")
    return creds


def status() -> dict:
    s = get_settings()
    out = {"client_secrets_present": s.google_client_secrets_file.exists(),
           "token_present": s.youtube_token_file.exists(), "connected": False}
    try:
        yt = _yt()
        ch = yt.channels().list(part="snippet,statistics", mine=True).execute()["items"][0]
        out.update(connected=True, channel_id=ch["id"], channel_title=ch["snippet"]["title"],
                   subscribers=ch["statistics"].get("subscriberCount"))
    except ProviderUnavailable as e:
        out["error"] = str(e)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=credentials(), cache_discovery=False)


def _yta():
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=credentials(), cache_discovery=False)


def upload(video_path: str, seo: dict, privacy: str, thumbnail: str | None = None,
           playlist_id: str | None = None, captions_srt: str | None = None,
           synthetic: bool = True, is_short: bool = False) -> dict[str, Any]:
    if privacy not in ("private", "unlisted"):
        raise PermissionError("Uploads are restricted to private or unlisted. Use publish() after approval.")
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    title = seo["title"]
    if is_short and "#shorts" not in title.lower() and len(title) < 90:
        title += " #shorts"
    body = {
        "snippet": {"title": title[:100], "description": seo["description"][:5000],
                    "tags": seo.get("tags", []), "categoryId": seo.get("category_id", "27"),
                    "defaultLanguage": "en", "defaultAudioLanguage": "en-US"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": synthetic, "embeddable": True},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    vid = resp["id"]
    result: dict[str, Any] = {"youtube_id": vid, "privacy": privacy, "url": f"https://youtu.be/{vid}",
                              "steps": {"video": "ok"}}
    if thumbnail:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail, mimetype="image/jpeg")).execute()
            result["steps"]["thumbnail"] = "ok"
        except Exception as e:  # noqa: BLE001 - needs a verified channel for custom thumbnails
            result["steps"]["thumbnail"] = f"failed: {e}"
    if playlist_id:
        try:
            yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
            result["steps"]["playlist"] = "ok"
        except Exception as e:  # noqa: BLE001
            result["steps"]["playlist"] = f"failed: {e}"
    if captions_srt and Path(captions_srt).exists():
        try:
            yt.captions().insert(part="snippet", body={"snippet": {"videoId": vid, "language": "en",
                                                                   "name": "English", "isDraft": False}},
                                 media_body=MediaFileUpload(captions_srt, mimetype="application/octet-stream")
                                 ).execute()
            result["steps"]["captions"] = "ok"
        except Exception as e:  # noqa: BLE001
            result["steps"]["captions"] = f"failed: {e}"
    return result


def publish(youtube_id: str, approval: dict, publish_at: str | None = None) -> dict:
    """Make public now, or schedule (publish_at ISO 8601 UTC). Requires owner approval."""
    if not approval or not approval.get("approved_by_owner"):
        raise PermissionError("Public publishing requires explicit owner approval from the dashboard.")
    yt = _yt()
    cur = yt.videos().list(part="status", id=youtube_id).execute()["items"][0]["status"]
    # videos.update replaces the whole status part: start from the current object
    st = {k: v for k, v in cur.items() if k in ("embeddable", "license", "publicStatsViewable",
                                                 "selfDeclaredMadeForKids", "containsSyntheticMedia")}
    st.setdefault("containsSyntheticMedia", True)
    st.update({"privacyStatus": "private", "publishAt": publish_at} if publish_at else {"privacyStatus": "public"})
    yt.videos().update(part="status", body={"id": youtube_id, "status": st}).execute()
    return {"youtube_id": youtube_id, "status": "scheduled" if publish_at else "public", "publish_at": publish_at}


def list_playlists() -> list[dict]:
    items = _yt().playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
    return [{"id": p["id"], "title": p["snippet"]["title"]} for p in items]


# ------------------------------------------------------------------ analytics
def fetch_video_analytics(youtube_ids: list[str], days: int = 28) -> list[dict]:
    """Real numbers only. Metrics the channel isn't eligible for come back as None."""
    if not youtube_ids:
        return []
    yta = _yta()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)  # inclusive window of exactly `days` days
    base = dict(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat())
    out: dict[str, dict] = {v: {"youtube_id": v, "day": end.isoformat(), "window_days": days,
                                "traffic_sources": {}, "retention": [], "notes": []} for v in youtube_ids}

    def q(metrics: str, dims: str, filters: str):
        extra = {"sort": "-" + metrics.split(",")[0], "maxResults": 200} if dims == "video" else {}
        r = yta.reports().query(metrics=metrics, dimensions=dims, filters=filters, **extra, **base).execute()
        cols = [c["name"] for c in r.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in r.get("rows", [])]

    chunks = [youtube_ids[i:i + 50] for i in range(0, len(youtube_ids), 50)]
    for ch in chunks:
        try:
            for row in q("views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
                         "video", "video==" + ",".join(ch)):
                o = out[row["video"]]
                o.update(views=int(row["views"]), watch_minutes=float(row["estimatedMinutesWatched"]),
                         avg_view_duration_s=float(row["averageViewDuration"]),
                         avg_view_pct=float(row["averageViewPercentage"]),
                         subscribers_gained=int(row["subscribersGained"]))
        except Exception as e:  # noqa: BLE001
            for v in ch:
                out[v]["notes"].append(f"core metrics unavailable ({str(e)[:160]})")
    vf = "video==" + ",".join(youtube_ids[:50])
    for metrics, fields in [("estimatedRevenue,playbackBasedCpm", ("revenue_usd", None)),
                            ("videoThumbnailImpressions,videoThumbnailImpressionsClickRate", ("impressions", "ctr"))]:
        try:
            for row in q(metrics, "video", vf):
                o = out[row["video"]]
                vals = [row[m] for m in metrics.split(",")]
                if fields[0] == "revenue_usd":
                    o["revenue_usd"] = float(vals[0])
                    o["rpm_usd"] = round(float(vals[0]) / max(1, o.get("views") or 0) * 1000, 2) if o.get("views") else None
                else:
                    o["impressions"], o["ctr"] = int(vals[0]), float(vals[1])
        except Exception as e:  # noqa: BLE001 - not monetized / metric not available for this channel
            for o in out.values():
                o["notes"].append(f"{metrics}: unavailable ({str(e)[:120]})")
    for v in youtube_ids:
        try:
            out[v]["traffic_sources"] = {r["insightTrafficSourceType"]: int(r["views"])
                                         for r in q("views", "insightTrafficSourceType", f"video=={v}")}
            out[v]["retention"] = [[float(r["elapsedVideoTimeRatio"]), float(r["audienceWatchRatio"])]
                                   for r in q("audienceWatchRatio", "elapsedVideoTimeRatio", f"video=={v}")]
        except Exception as e:  # noqa: BLE001
            out[v]["notes"].append(f"traffic/retention unavailable ({str(e)[:120]})")
    return list(out.values())
