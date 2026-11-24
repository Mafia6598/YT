"""Production pipeline: stage handlers, approval gates and job chaining."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlmodel import select

from . import seo as seo_mod
from . import shorts as shorts_mod
from . import thumbnails as thumbs_mod
from . import tts
from . import youtube
from .config import get_settings
from .db import kv_get, session, touch
from .models import AffiliateLink, AnalyticsSnapshot, Job, Topic, Video, now
from .research import agent as research
from .script import generator
from .video import render as render_mod
from .video import visuals

log = logging.getLogger(__name__)

NEXT = {"research": "script", "script": "voice", "voice": "render", "render": "seo",
        "seo": "thumbnails", "thumbnails": None}
STAGE_STATUS = {"research": "researching", "script": "writing", "voice": "narration", "render": "rendering",
                "thumbnails": "rendering", "seo": "rendering"}
DEFAULT_PIPELINE = {"gates": ["script"], "auto_shorts": False, "shorts_per_video": 3, "fast_render": False,
                    "default_minutes": 10, "tts_provider": None, "image_providers": None}


def pipeline_cfg() -> dict:
    return {**DEFAULT_PIPELINE, **kv_get("pipeline")}


# ----------------------------------------------------------------- jobs
def enqueue(kind: str, video_id: int | None = None, payload: dict | None = None, delay_s: int = 0) -> Job:
    with session() as s:
        j = Job(kind=kind, video_id=video_id, payload=payload or {},
                max_attempts=get_settings().job_max_attempts, run_after=now() + timedelta(seconds=delay_s))
        s.add(j)
        s.commit()
        s.refresh(j)
        if video_id and (kind in NEXT or kind == "shorts"):
            v = s.get(Video, video_id)
            if v:
                v.stages[kind] = {"status": "queued", "job_id": j.id, "updated": now().isoformat()}
                touch(v, "stages")
                s.add(v)
                s.commit()
        return j


def set_stage(v: Video, stage: str, status: str, **extra) -> None:
    v.stages[stage] = {**v.stages.get(stage, {}), "status": status, "updated": now().isoformat(), **extra}
    touch(v, "stages")
    v.updated_at = now()


def save(v: Video, *fields: str) -> None:
    with session() as s:
        touch(v, *[f for f in fields if f in ("stages", "research", "script", "audio", "render", "assets",
                                              "thumbnails", "seo", "youtube")])
        s.add(v)
        s.commit()


def load(video_id: int) -> Video:
    with session() as s:
        v = s.get(Video, video_id)
        if not v:
            raise ValueError(f"video {video_id} not found")
        return v


def stage_done(video_id: int, stage: str, chain: bool = True) -> None:
    """Gate or chain to next stage."""
    v = load(video_id)
    if v.status == "rejected" or v.stages.get(stage, {}).get("status") == "rejected":
        return  # owner rejected while the job was running: don't continue the chain
    cfg = pipeline_cfg()
    if stage in cfg["gates"]:
        set_stage(v, stage, "awaiting_approval")
        v.status = "awaiting_approval"
        save(v, "stages")
        return
    set_stage(v, stage, "done")
    nxt = NEXT.get(stage)
    nxt_done = v.stages.get(nxt or "", {}).get("status") in ("done", "approved")
    if nxt and (chain or not nxt_done):
        v.status = STAGE_STATUS[nxt]
        save(v, "stages")
        enqueue(nxt, video_id)
    else:
        save(v, "stages")
        _finish(video_id)


def _finish(video_id: int) -> None:
    """All stages done -> final human review before upload."""
    v = load(video_id)
    set_stage(v, "final", "awaiting_approval")
    v.status = "awaiting_approval"
    save(v, "stages")
    if v.format == "long" and pipeline_cfg()["auto_shorts"] and not v.stages.get("shorts"):
        enqueue("shorts", video_id)


def approve(video_id: int, stage: str) -> Video:
    v = load(video_id)
    cur = v.stages.get(stage, {}).get("status")
    if cur != "awaiting_approval":
        raise ValueError(f"'{stage}' is not awaiting approval (current: {cur or 'not started'})")
    if stage == "final" and not (v.render.get("path") and Path(v.render["path"]).exists() and v.seo.get("title")):
        raise ValueError("final review needs a rendered video and SEO metadata")
    set_stage(v, stage, "approved")
    if stage == "final":
        v.status = "approved"
        save(v, "stages")
        return v
    nxt = NEXT.get(stage)
    v.status = STAGE_STATUS.get(nxt, v.status) if nxt else v.status
    save(v, "stages")
    if nxt:
        enqueue(nxt, video_id)
    else:
        _finish(video_id)
    return v


def reject(video_id: int, stage: str, notes: str = "") -> Video:
    v = load(video_id)
    set_stage(v, stage, "rejected", notes=notes)
    v.status = "rejected"
    save(v, "stages")
    cancel_queued(video_id)
    return v


def cancel_queued(video_id: int) -> int:
    with session() as s:
        jobs = s.exec(select(Job).where(Job.video_id == video_id, Job.status == "queued")).all()
        v = s.get(Video, video_id)
        for j in jobs:
            j.status = "cancelled"
            s.add(j)
            if v and v.stages.get(j.kind, {}).get("job_id") == j.id:
                v.stages[j.kind] = {**v.stages[j.kind], "status": "cancelled"}
                touch(v, "stages")
                s.add(v)
        s.commit()
        return len(jobs)


def invalidate_final(v: Video) -> None:
    """Any edit after final approval needs a fresh approval."""
    if v.stages.get("final", {}).get("status") == "approved":
        set_stage(v, "final", "awaiting_approval", note="content edited after approval")
        if v.status == "approved":
            v.status = "awaiting_approval"


def regenerate(video_id: int, stage: str, notes: str = "", chain: bool = True) -> Job:
    if stage not in HANDLERS:
        raise ValueError(f"'{stage}' cannot be regenerated")
    v = load(video_id)
    if v.youtube.get("youtube_id"):
        raise ValueError("Already uploaded to YouTube; create a new production instead of rebuilding it")
    cancel_queued(video_id)
    v.status = STAGE_STATUS.get(stage, v.status)
    if chain:  # downstream outputs become stale and are rebuilt
        for later in _after(stage):
            v.stages.pop(later, None)
    v.stages.pop("final", None)
    save(v, "stages")
    return enqueue(stage, video_id, {"notes": notes, "chain": chain})


def _after(stage: str) -> list[str]:
    out, cur = [], NEXT.get(stage)
    while cur:
        out.append(cur)
        cur = NEXT.get(cur)
    return out


def create_production(topic_id: int, fmt: str = "long", minutes: float | None = None) -> Video:
    with session() as s:
        t = s.get(Topic, topic_id)
        if not t:
            raise ValueError("topic not found")
        v = Video(topic_id=t.id, format=fmt, title=t.title, status="researching",
                  research={"minutes": minutes or pipeline_cfg()["default_minutes"]})
        t.status = "in_production"
        s.add(v)
        s.add(t)
        s.commit()
        s.refresh(v)
    enqueue("research", v.id)
    return v


# ----------------------------------------------------------------- handlers
def h_research(job: Job) -> dict:
    v = load(job.video_id)
    with session() as s:
        t = s.get(Topic, v.topic_id)
    minutes = v.research.get("minutes", 10)
    packet = research.research_packet(t.wiki_title or t.title)
    packet["minutes"] = minutes
    v.research = packet
    set_stage(v, "research", "done")
    save(v, "research", "stages")
    stage_done(v.id, "research", job.payload.get("chain", True))
    return {"sources": packet["source_count"]}


def h_script(job: Job) -> dict:
    v = load(job.video_id)
    set_stage(v, "script", "running")
    v.status = "writing"
    save(v, "stages")
    notes = job.payload.get("notes", "")
    if v.script and notes:
        notes += "\nPrevious draft title: " + v.script.get("title", "")
    sc = generator.generate(v.research, v.format, v.research.get("minutes", 10),
                            focus=v.research.get("focus", ""), notes=notes)
    v.script = sc
    if v.format == "long" or not v.title:
        v.title = sc.get("title") or v.title
    save(v, "script")
    flagged = [c for c in sc["claims"] if c["status"] != "supported"]
    res = {"words": sc["word_count"], "est_duration_s": sc["duration_s"], "flagged_claims": len(flagged)}
    stage_done(v.id, "script", job.payload.get("chain", True))  # last: nothing may fail after chaining
    return res


def _scenes(v: Video) -> list[dict]:
    return list(generator.iter_scenes(v.script))


def h_voice(job: Job) -> dict:
    v = load(job.video_id)
    set_stage(v, "voice", "running")
    v.status = "narration"
    save(v, "stages")
    ends = {sec["scenes"][-1]["id"] for sec in v.script["sections"] if sec.get("scenes")}
    d = Path(v_dir(v.id)) / "audio"
    shutil.rmtree(d, ignore_errors=True)
    audio = tts.narrate_scenes(_scenes(v), d, job.payload.get("provider") or pipeline_cfg()["tts_provider"],
                               section_ends=ends)
    durations = [s["duration"] for s in audio["segments"]]
    v.script = generator.finalize(v.script, durations)
    v.audio = audio
    save(v, "audio", "script")
    stage_done(v.id, "voice", job.payload.get("chain", True))
    return {"duration": audio["duration"], "providers": audio["providers"]}


def v_dir(video_id: int) -> str:
    from .util import video_dir
    return str(video_dir(video_id))


def h_render(job: Job) -> dict:
    v = load(job.video_id)
    set_stage(v, "render", "running")
    v.status = "rendering"
    save(v, "stages")
    size = render_mod.SIZES[v.format]
    vis_dir = Path(v_dir(v.id)) / f"visuals_{v.format}"
    vis_dir.mkdir(exist_ok=True)
    cfg = pipeline_cfg()
    providers = cfg["image_providers"]
    assets, paths = [], {}
    old = {a["scene_id"]: a for a in v.assets} if not job.payload.get("refresh_visuals") else {}
    for sc in _scenes(v):
        out = vis_dir / f"scene_{sc['id']:03d}.jpg"
        a = old.get(sc["id"])
        if a and Path(a["path"]).exists():
            assets.append(a)
        else:
            assets.append(visuals.get_visual(sc, out, size, v.title, providers))
        paths[sc["id"]] = assets[-1]["path"]
    v.assets = assets
    save(v, "assets")
    res = render_mod.render(_scenes(v), v.audio, paths, Path(v_dir(v.id)), v.format,
                            fast=job.payload.get("fast", cfg["fast_render"]), seed=v.id)
    v.render = res
    save(v, "render")
    stage_done(v.id, "render", job.payload.get("chain", True))
    return {"duration": res["duration"], "visual_providers": sorted({a["provider"] for a in assets})}


def h_thumbnails(job: Job) -> dict:
    v = load(job.video_id)
    set_stage(v, "thumbnails", "running")
    save(v, "stages")
    if v.format == "short":  # Shorts use a frame from the video; keep one simple cover
        bg = [a["path"] for a in v.assets[:1]]
    else:
        non_proc = [a["path"] for a in v.assets if a["provider"] != "procedural"]
        bg = (non_proc or [a["path"] for a in v.assets])[:6]
        bg = [bg[0], bg[len(bg) // 2], bg[-1]] if len(bg) >= 3 else bg
    text = (v.seo or {}).get("thumbnail_text") or seo_mod._offline(v.title, v.script, v.format)["thumbnail_text"]
    th = thumbs_mod.generate(bg, text, v.title, Path(v_dir(v.id)) / "thumbnails")
    v.thumbnails = th
    save(v, "thumbnails")
    stage_done(v.id, "thumbnails", job.payload.get("chain", True))
    return {"recommended": th["recommended"], "scoring": th["scoring"]}


def h_seo(job: Job) -> dict:
    v = load(job.video_id)
    set_stage(v, "seo", "running")
    save(v, "stages")
    with session() as s:
        links = [l.model_dump() for l in s.exec(select(AffiliateLink).where(AffiliateLink.approved == True)).all()]  # noqa: E712
    meta = seo_mod.generate(v.title, v.script, v.research, v.assets, v.format,
                            affiliate_links=links if job.payload.get("include_affiliates") else None,
                            synthetic=get_settings().contains_synthetic_media)
    v.seo = meta
    save(v, "seo")
    stage_done(v.id, "seo", job.payload.get("chain", True))
    return {"title": meta["title"], "chapters": len(meta["chapters"])}


def h_shorts(job: Job) -> dict:
    v = load(job.video_id)
    set_stage(v, "shorts", "running")
    save(v, "stages")
    moments = shorts_mod.pick_moments(v.script, job.payload.get("count", pipeline_cfg()["shorts_per_video"]))
    with session() as s:  # one transaction: a failure creates no partial set of children
        kids = [Video(topic_id=v.topic_id, parent_id=v.id, format="short",
                      title=f"{v.title}: {m['focus']}"[:90], status="writing",
                      research={**v.research, "focus": m["focus"], "minutes": 0.85, "moment": m})
                for m in moments]
        s.add_all(kids)
        s.commit()
        ids = [k.id for k in kids]
    for cid in ids:
        enqueue("script", cid)
    v = load(job.video_id)
    set_stage(v, "shorts", "done", children=ids, moments=moments)
    save(v, "stages")
    return {"shorts": ids}


def h_upload(job: Job) -> dict:
    v = load(job.video_id)
    if v.stages.get("final", {}).get("status") != "approved":
        raise PermissionError("Final review not approved yet")
    if v.youtube.get("youtube_id") or v.youtube.get("upload_started_at"):
        # never re-send: an interrupted upload may already exist on YouTube (check YouTube Studio)
        raise PermissionError("Upload already done or started for this video; check YouTube Studio before retrying")
    v.youtube = {**v.youtube, "upload_started_at": now().isoformat()}
    save(v, "youtube")
    privacy = job.payload.get("privacy") or get_settings().default_privacy
    sel = v.thumbnails.get("selected", 0) if v.thumbnails else None
    thumb = next((c["path"] for c in v.thumbnails.get("concepts", []) if c["index"] == sel), None) \
        if v.thumbnails else None
    res = youtube.upload(v.render["path"], v.seo, privacy, thumb if v.format == "long" else None,
                         job.payload.get("playlist_id"), v.render.get("subtitles_srt"),
                         synthetic=get_settings().contains_synthetic_media, is_short=v.format == "short")
    v.youtube = {**v.youtube, **res, "uploaded_at": now().isoformat()}
    v.status = "uploaded"
    save(v, "youtube")
    return res


def h_publish(job: Job) -> dict:
    v = load(job.video_id)
    approval = job.payload.get("approval", {})
    stored = v.youtube.get("publish_approval", {})
    if not approval.get("nonce") or approval.get("nonce") != stored.get("nonce") or stored.get("used"):
        raise PermissionError("Publish approval is missing, stale or already used. Approve again in the dashboard.")
    if v.status != "uploaded" or v.stages.get("final", {}).get("status") != "approved":
        raise PermissionError("Video must be uploaded and its final review approved before publishing")
    v.youtube["publish_approval"] = {**stored, "used": True}
    save(v, "youtube")
    res = youtube.publish(v.youtube["youtube_id"], approval, job.payload.get("publish_at"))
    v.youtube = {**v.youtube, **res, "published_at": now().isoformat()}
    v.status = "published"
    save(v, "youtube")
    return res


def h_analytics(job: Job) -> dict:
    with session() as s:
        vids = s.exec(select(Video).where(Video.status.in_(["uploaded", "published"]))).all()
    by_yt = {v.youtube.get("youtube_id"): v.id for v in vids if v.youtube.get("youtube_id")}
    rows = youtube.fetch_video_analytics(list(by_yt), job.payload.get("days", 28))
    with session() as s:
        for r in rows:
            r.pop("notes", None)
            s.add(AnalyticsSnapshot(video_id=by_yt.get(r["youtube_id"]), **r))
        s.commit()
    return {"videos": len(rows)}


def h_discover(job: Job) -> dict:
    p = job.payload
    found = research.discover(p.get("categories"), p.get("per_category", 2), p.get("use_llm", True),
                              p.get("deep", True))
    added = 0
    with session() as s:
        existing = {t.wiki_title for t in s.exec(select(Topic)).all()}
        for t in found:
            if t["wiki_title"] in existing:
                continue
            s.add(Topic(**t))
            added += 1
        s.commit()
    return {"found": len(found), "added": added}


HANDLERS: dict[str, Callable[[Job], dict[str, Any]]] = {
    "research": h_research, "script": h_script, "voice": h_voice, "render": h_render,
    "thumbnails": h_thumbnails, "seo": h_seo, "shorts": h_shorts, "upload": h_upload,
    "publish": h_publish, "analytics_sync": h_analytics, "discover_topics": h_discover,
}
