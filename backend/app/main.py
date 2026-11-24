"""FastAPI app: REST API for the dashboard + static media + built frontend."""
from __future__ import annotations

import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import analytics, pipeline, revenue, tts, worker, youtube
from .config import ROOT, get_settings
from .db import get_session, init_db, kv_get, kv_set, touch
from .models import STAGES, STATUSES, AffiliateLink, AnalyticsSnapshot, Job, RevenueEntry, Topic, Video, now
from .research.seeds import CATEGORIES
from .script import generator
from .util import ProviderUnavailable, media_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if not getattr(app.state, "no_worker", False):
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="History Studio", version="1.0", lifespan=lifespan)
S = get_settings()


@app.exception_handler(ValueError)
async def _value_error(_, exc: ValueError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def _token_guard(request, call_next):
    """Optional shared secret (STUDIO_TOKEN) for API calls when the server is reachable from a network."""
    tok = S.studio_token
    if tok and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        given = request.headers.get("x-studio-token") or request.cookies.get("studio_token")
        if given != tok:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "missing or wrong studio token"})
    return await call_next(request)
app.mount("/media", StaticFiles(directory=str(S.media_dir)), name="media")


def _urls(obj: Any) -> Any:
    """Add *_url siblings for any local media path inside JSON blobs."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = _urls(v)
            if isinstance(v, str) and (k == "path" or k.endswith(("_srt", "_ass"))) and v.startswith(str(S.media_dir)):
                try:
                    out[f"{k}_url" if k != "path" else "url"] = media_url(v)
                except ValueError:
                    pass
        return out
    if isinstance(obj, list):
        return [_urls(x) for x in obj]
    return obj


def video_out(v: Video, full: bool = True) -> dict:
    d = v.model_dump()
    if not full:
        for k in ("research", "script", "audio", "assets", "seo"):
            d.pop(k, None)
        d["thumbnail_url"] = None
        if v.thumbnails:
            sel = v.thumbnails.get("selected", 0)
            c = next((c for c in v.thumbnails.get("concepts", []) if c["index"] == sel), None)
            d["thumbnail_url"] = media_url(c["path"]) if c else None
        d["video_url"] = media_url(v.render["path"]) if v.render.get("path") else None
        d.pop("thumbnails", None)
        d.pop("render", None)
        return d
    if d.get("research"):
        d["research"] = {k: v_ for k, v_ in d["research"].items() if k != "text"} | \
                        {"text_chars": len(v.research.get("text", ""))}
    if d.get("audio"):
        d["audio"].pop("words", None)
    return _urls(d)


# ------------------------------------------------------------------ status
@app.get("/api/health")
def health():
    return {"ok": True, "time": now().isoformat()}


@app.get("/api/status")
def integration_status():
    from .llm import get_llm
    llm = get_llm()
    return {
        "llm": {"provider": llm.name if llm else "offline-template",
                "model": S.anthropic_model if llm and llm.name == "anthropic" else (S.openai_model if llm else None),
                "configured": bool(llm)},
        "tts": {"preferred": S.tts_provider, "chain": tts.provider_chain(),
                "elevenlabs": bool(S.elevenlabs_api_key and S.elevenlabs_voice_id), "openai": bool(S.openai_api_key),
                "edge_voice": S.edge_voice, "espeak": bool(shutil.which("espeak-ng") or shutil.which("espeak"))},
        "images": {"order": S.image_providers, "pexels": bool(S.pexels_api_key), "openai": bool(S.openai_api_key)},
        "research": {"youtube_data_api_key": bool(S.youtube_api_key), "autocomplete": S.use_youtube_autocomplete},
        "youtube_oauth": {"client_secrets": S.google_client_secrets_file.exists(),
                          "token": S.youtube_token_file.exists()},
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "music_tracks": len(list(S.music_dir.glob("*.mp3"))) if S.music_dir.exists() else 0,
    }


@app.get("/api/dashboard")
def dashboard(s: Session = Depends(get_session)):
    vids = s.exec(select(Video).order_by(Video.updated_at.desc())).all()
    counts = {st: 0 for st in STATUSES}
    for v in vids:
        counts[v.status] = counts.get(v.status, 0) + 1
    jobs = s.exec(select(Job).order_by(Job.id.desc()).limit(15)).all()
    topics = s.exec(select(Topic).where(Topic.status == "new").order_by(Topic.total_score.desc()).limit(5)).all()
    return {"counts": counts, "videos": [video_out(v, False) for v in vids[:30]],
            "jobs": [j.model_dump() for j in jobs], "top_topics": [t.model_dump() for t in topics],
            "schedule": worker.schedule_cfg(), "pipeline": pipeline.pipeline_cfg(),
            "awaiting": [video_out(v, False) for v in vids if v.status == "awaiting_approval"]}


# ------------------------------------------------------------------ topics
class DiscoverIn(BaseModel):
    categories: Optional[list[str]] = None
    per_category: int = 2
    use_llm: bool = True
    deep: bool = True


@app.get("/api/categories")
def categories():
    return list(CATEGORIES)


@app.get("/api/topics")
def list_topics(status: Optional[str] = None, s: Session = Depends(get_session)):
    q = select(Topic).order_by(Topic.total_score.desc())
    if status:
        q = q.where(Topic.status == status)
    return [t.model_dump() for t in s.exec(q).all()]


@app.post("/api/topics/discover")
def discover(body: DiscoverIn):
    j = pipeline.enqueue("discover_topics", payload=body.model_dump())
    return {"job_id": j.id}


class TopicIn(BaseModel):
    query: str
    category: str = "Untold History"


@app.post("/api/topics")
def add_topic(body: TopicIn, s: Session = Depends(get_session)):
    from .research.agent import score_topic
    try:
        t = score_topic(body.category, body.query, deep=True)
    except ProviderUnavailable as e:
        raise HTTPException(503, str(e))
    if not t:
        raise HTTPException(404, "No substantial Wikipedia article found for that query; "
                                 "topics need a verifiable source.")
    topic = Topic(**t)
    s.add(topic)
    s.commit()
    s.refresh(topic)
    return topic.model_dump()


class TopicPatch(BaseModel):
    status: str


@app.patch("/api/topics/{tid}")
def patch_topic(tid: int, body: TopicPatch, s: Session = Depends(get_session)):
    t = s.get(Topic, tid) or _404()
    t.status = body.status
    s.add(t)
    s.commit()
    return t.model_dump()


class ProduceIn(BaseModel):
    format: str = "long"
    minutes: Optional[float] = None


@app.post("/api/topics/{tid}/produce")
def produce(tid: int, body: ProduceIn):
    v = pipeline.create_production(tid, body.format, body.minutes)
    return video_out(v, False)


# ------------------------------------------------------------------ videos
@app.get("/api/videos")
def list_videos(format: Optional[str] = None, s: Session = Depends(get_session)):
    q = select(Video).order_by(Video.id.desc())
    if format:
        q = q.where(Video.format == format)
    return [video_out(v, False) for v in s.exec(q).all()]


@app.get("/api/videos/{vid}")
def get_video(vid: int, s: Session = Depends(get_session)):
    v = s.get(Video, vid) or _404()
    d = video_out(v)
    d["jobs"] = [j.model_dump() for j in s.exec(select(Job).where(Job.video_id == vid).order_by(Job.id.desc())).all()]
    d["children"] = [video_out(c, False) for c in s.exec(select(Video).where(Video.parent_id == vid)).all()]
    return d


@app.delete("/api/videos/{vid}")
def delete_video(vid: int, s: Session = Depends(get_session)):
    v = s.get(Video, vid) or _404()
    if v.youtube.get("youtube_id"):
        raise HTTPException(400, "Video is on YouTube; delete it there first, then here.")
    pipeline.cancel_queued(vid)
    for c in s.exec(select(Video).where(Video.parent_id == vid)).all():
        if not c.youtube.get("youtube_id"):
            pipeline.cancel_queued(c.id)
            s.delete(c)
            shutil.rmtree(S.media_dir / f"video_{c.id}", ignore_errors=True)
    s.delete(v)
    s.commit()
    shutil.rmtree(S.media_dir / f"video_{vid}", ignore_errors=True)
    return {"deleted": vid}


class ScriptIn(BaseModel):
    script: dict
    rerun: bool = True


@app.put("/api/videos/{vid}/script")
def edit_script(vid: int, body: ScriptIn):
    v = pipeline.load(vid)
    sc = generator.finalize(body.script)
    sc["edited_by_owner"] = now().isoformat()
    v.script = sc
    pipeline.set_stage(v, "script", "approved")
    pipeline.invalidate_final(v)
    pipeline.save(v, "script", "stages")
    if body.rerun:
        pipeline.regenerate(vid, "voice")
    return video_out(pipeline.load(vid))


class SeoIn(BaseModel):
    seo: dict


@app.put("/api/videos/{vid}/seo")
def edit_seo(vid: int, body: SeoIn):
    v = pipeline.load(vid)
    v.seo = {**v.seo, **body.seo, "edited_by_owner": now().isoformat()}
    pipeline.invalidate_final(v)
    pipeline.save(v, "seo", "stages")
    return video_out(v)


class ThumbIn(BaseModel):
    selected: int


@app.put("/api/videos/{vid}/thumbnail")
def pick_thumbnail(vid: int, body: ThumbIn):
    v = pipeline.load(vid)
    v.thumbnails["selected"] = body.selected
    v.thumbnails["manual_choice"] = True
    pipeline.invalidate_final(v)
    pipeline.save(v, "thumbnails", "stages")
    return video_out(v)


class StageIn(BaseModel):
    notes: str = ""
    chain: bool = True
    payload: dict = {}


@app.post("/api/videos/{vid}/stages/{stage}/{action}")
def stage_action(vid: int, stage: str, action: str, body: StageIn = StageIn()):
    if stage not in STAGES + ["research", "final"]:
        raise HTTPException(400, f"unknown stage {stage}")
    if action == "approve":
        return video_out(pipeline.approve(vid, stage))
    if action == "reject":
        return video_out(pipeline.reject(vid, stage, body.notes))
    if action == "regenerate":
        if stage == "shorts":
            j = pipeline.enqueue("shorts", vid, body.payload)
        else:
            j = pipeline.regenerate(vid, stage, body.notes, body.chain)
            if body.payload:
                from .db import session as db_session
                with db_session() as s:
                    jj = s.get(Job, j.id)
                    jj.payload = {**jj.payload, **body.payload}
                    touch(jj, "payload")
                    s.add(jj)
                    s.commit()
        return {"job_id": j.id}
    raise HTTPException(400, "action must be approve | reject | regenerate")


class UploadIn(BaseModel):
    privacy: str = "private"
    playlist_id: Optional[str] = None


@app.post("/api/videos/{vid}/upload")
def upload(vid: int, body: UploadIn):
    if body.privacy not in ("private", "unlisted"):
        raise HTTPException(400, "Uploads are private or unlisted only. Publish separately after approval.")
    v = pipeline.load(vid)
    if v.stages.get("final", {}).get("status") != "approved":
        raise HTTPException(400, "Approve the final review first.")
    return {"job_id": pipeline.enqueue("upload", vid, body.model_dump()).id}


class PublishIn(BaseModel):
    confirm: str
    publish_at: Optional[str] = None


@app.post("/api/videos/{vid}/publish")
def publish(vid: int, body: PublishIn):
    if body.confirm != "PUBLISH":
        raise HTTPException(400, 'Type PUBLISH to confirm public release.')
    v = pipeline.load(vid)
    if not v.youtube.get("youtube_id") or v.status != "uploaded":
        raise HTTPException(400, "Upload (private/unlisted) first; already-published videos can't be published again.")
    if v.stages.get("final", {}).get("status") != "approved":
        raise HTTPException(400, "Final review must be approved.")
    approval = {"approved_by_owner": True, "at": now().isoformat(), "nonce": uuid.uuid4().hex}
    v.youtube["publish_approval"] = approval
    pipeline.save(v, "youtube")
    return {"job_id": pipeline.enqueue("publish", vid, {"approval": approval, "publish_at": body.publish_at}).id}


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs")
def jobs(limit: int = 100, s: Session = Depends(get_session)):
    return [j.model_dump() for j in s.exec(select(Job).order_by(Job.id.desc()).limit(limit)).all()]


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: int, s: Session = Depends(get_session)):
    j = s.get(Job, jid) or _404()
    if j.status not in ("failed", "cancelled"):
        raise HTTPException(400, f"Only failed or cancelled jobs can be retried (this one is {j.status}).")
    if j.kind == "publish":
        raise HTTPException(400, "Publish again from the Upload Manager so a fresh approval is recorded.")
    j.status, j.attempts, j.run_after, j.error = "queued", 0, now(), ""
    s.add(j)
    s.commit()
    return j.model_dump()


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: int, s: Session = Depends(get_session)):
    j = s.get(Job, jid) or _404()
    if j.status == "queued":
        j.status = "cancelled"
        s.add(j)
        v = s.get(Video, j.video_id) if j.video_id else None
        if v and v.stages.get(j.kind, {}).get("job_id") == j.id:
            v.stages[j.kind] = {**v.stages[j.kind], "status": "cancelled"}
            touch(v, "stages")
            s.add(v)
        s.commit()
    return j.model_dump()


# ------------------------------------------------------------------ voice
class VoiceIn(BaseModel):
    text: str
    provider: Optional[str] = None


@app.post("/api/voice/preview")
def voice_preview(body: VoiceIn):
    d = S.media_dir / "previews"
    d.mkdir(exist_ok=True)
    out = d / f"preview_{uuid.uuid4().hex[:8]}.mp3"
    try:
        name, _, errs = tts.synthesize(body.text[:1500], out, body.provider)
    except ProviderUnavailable as e:
        raise HTTPException(503, str(e))
    return {"provider": name, "url": media_url(out), "fallback_errors": errs}


# ------------------------------------------------------------------ youtube / analytics / revenue
@app.get("/api/youtube/status")
def yt_status():
    return youtube.status()


@app.get("/api/youtube/playlists")
def yt_playlists():
    try:
        return youtube.list_playlists()
    except ProviderUnavailable as e:
        raise HTTPException(503, str(e))


@app.post("/api/analytics/sync")
def analytics_sync(days: int = 28):
    return {"job_id": pipeline.enqueue("analytics_sync", payload={"days": days}).id}


@app.get("/api/analytics/report")
def analytics_report():
    return analytics.weekly_report()


class ManualStats(BaseModel):
    video_id: Optional[int] = None
    youtube_id: str
    views: Optional[int] = None
    watch_minutes: Optional[float] = None
    avg_view_duration_s: Optional[float] = None
    avg_view_pct: Optional[float] = None
    ctr: Optional[float] = None
    subscribers_gained: Optional[int] = None
    revenue_usd: Optional[float] = None


@app.post("/api/analytics/manual")
def analytics_manual(body: ManualStats, s: Session = Depends(get_session)):
    snap = AnalyticsSnapshot(**body.model_dump(), source="manual", day=now().date().isoformat())
    s.add(snap)
    s.commit()
    return snap.model_dump()


@app.get("/api/revenue")
def revenue_summary():
    return revenue.summary()


class RevIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    stream: str = Field(pattern=r"^(ads|affiliate|sponsorship|digital_products|other)$")
    amount_usd: float = Field(gt=0)
    note: str = ""


@app.post("/api/revenue/entries")
def add_revenue(body: RevIn, s: Session = Depends(get_session)):
    e = RevenueEntry(**body.model_dump())
    s.add(e)
    s.commit()
    return e.model_dump()


@app.delete("/api/revenue/entries/{eid}")
def del_revenue(eid: int, s: Session = Depends(get_session)):
    e = s.get(RevenueEntry, eid) or _404()
    s.delete(e)
    s.commit()
    return {"deleted": eid}


class ScenarioIn(BaseModel):
    monthly_views: Optional[float] = None
    rpms: dict[str, float] = {"conservative": 3.0, "typical": 6.0, "optimistic": 10.0}
    target: float = 1000
    other_monthly: float = 0


@app.post("/api/revenue/scenarios")
def rev_scenarios(body: ScenarioIn):
    return revenue.scenarios(body.monthly_views, body.rpms, body.target, body.other_monthly)


class AffIn(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://\S+$")
    disclosure: Optional[str] = None


@app.get("/api/affiliates")
def affiliates(s: Session = Depends(get_session)):
    return [a.model_dump() for a in s.exec(select(AffiliateLink)).all()]


@app.post("/api/affiliates")
def add_affiliate(body: AffIn, s: Session = Depends(get_session)):
    a = AffiliateLink(name=body.name, url=body.url, approved=False)
    if body.disclosure:
        a.disclosure = body.disclosure
    s.add(a)
    s.commit()
    return a.model_dump()


@app.post("/api/affiliates/{aid}/{action}")
def affiliate_action(aid: int, action: str, s: Session = Depends(get_session)):
    a = s.get(AffiliateLink, aid) or _404()
    if action == "delete":
        s.delete(a)
    else:
        a.approved = action == "approve"
        s.add(a)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ settings
@app.get("/api/settings")
def get_settings_api():
    return {"pipeline": pipeline.pipeline_cfg(), "schedule": worker.schedule_cfg(), "status": integration_status()}


@app.put("/api/settings/{name}")
def put_settings(name: str, body: dict):
    if name not in ("pipeline", "schedule"):
        raise HTTPException(400, "unknown settings group")
    cur = kv_get(name)
    if name == "schedule":
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(body.get("timezone", worker.schedule_cfg()["timezone"]))
            hh, mm = str(body.get("research_time", "07:00")).split(":")
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
        except Exception:
            raise HTTPException(400, "Invalid timezone or research_time (use e.g. America/New_York and 07:00)")
    if name == "pipeline" and "gates" in body:
        bad = set(body["gates"]) - set(pipeline.NEXT)
        if bad:
            raise HTTPException(400, f"unknown gates: {sorted(bad)}")
    cur.update(body)
    kv_set(name, cur)
    return pipeline.pipeline_cfg() if name == "pipeline" else worker.schedule_cfg()


def _404():
    raise HTTPException(404, "not found")


# ------------------------------------------------------------------ frontend
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
