"""Background job queue (SQLite-backed) + production calendar scheduler.

- Jobs survive restarts: on startup any job left 'running' is re-queued (crash recovery).
- Failed jobs retry with exponential backoff up to max_attempts.
- PermissionError (approval rules) never retries."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, update
from sqlmodel import select

from .config import get_settings
from .db import kv_get, kv_set, session, touch
from .models import Job, Topic, Video, now

log = logging.getLogger("worker")
_stop = threading.Event()

DEFAULT_SCHEDULE = {
    "enabled": False, "timezone": "America/New_York", "research_time": "07:00",
    "research_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "production_days": ["mon", "wed", "fri"], "long_per_week": 3, "shorts_per_day": 2,
    "min_topic_score": 55, "auto_pick_topics": True,
    "publish_slots": {"long": ["tue 17:00", "thu 17:00", "sat 12:00"], "short": ["daily 12:00", "daily 19:00"]},
    "note": "Production runs up to 'awaiting approval'. Nothing is published without your approval.",
}


def schedule_cfg() -> dict:
    return {**DEFAULT_SCHEDULE, **kv_get("schedule")}


def recover() -> int:
    with session() as s:
        stuck = s.exec(select(Job).where(Job.status == "running")).all()
        for j in stuck:
            if j.kind in ("upload", "publish") or j.attempts >= j.max_attempts:
                # side effects on YouTube may already have happened, or the job keeps crashing
                j.status = "failed"
                j.error = "interrupted by restart; not retried automatically (check YouTube Studio for uploads)"
            else:
                j.status = "queued"
            j.log = (j.log + "\nrecovered after restart").strip()
            s.add(j)
        s.commit()
        return len(stuck)


def _claim() -> Job | None:
    with session() as s:
        j = s.exec(select(Job).where(Job.status == "queued", Job.run_after <= now())
                   .order_by(Job.id)).first()
        if not j:
            return None
        # atomic claim: only one worker/process can flip queued -> running
        res = s.execute(update(Job).where(Job.id == j.id, Job.status == "queued")
                        .values(status="running", attempts=Job.attempts + 1, updated_at=now()))
        s.commit()
        if res.rowcount != 1:
            return None
        s.refresh(j)
        return j


def run_job(j: Job) -> None:
    from .pipeline import HANDLERS, load, save, set_stage
    t0 = time.time()
    try:
        res = HANDLERS[j.kind](j)
        with session() as s:
            jj = s.get(Job, j.id)
            jj.status, jj.result, jj.updated_at = "done", res or {}, now()
            jj.log = (jj.log + f"\ndone in {time.time() - t0:.1f}s").strip()
            touch(jj, "result")
            s.add(jj)
            s.commit()
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log.error("job %s %s failed: %s\n%s", j.id, j.kind, err, traceback.format_exc())
        final = isinstance(e, PermissionError) or j.attempts >= j.max_attempts
        with session() as s:
            jj = s.get(Job, j.id)
            jj.error, jj.updated_at = err[:4000], now()
            jj.log = (jj.log + f"\nattempt {j.attempts} failed: {err[:500]}").strip()
            if final:
                jj.status = "failed"
            else:
                jj.status = "queued"
                jj.run_after = now() + timedelta(seconds=20 * 2 ** j.attempts)
            s.add(jj)
            s.commit()
        if final and j.video_id:
            try:
                v = load(j.video_id)
                if j.kind in v.stages or j.kind in ("research", "script", "voice", "render", "seo", "thumbnails"):
                    set_stage(v, j.kind, "failed", error=err[:1000])
                if j.kind not in ("upload", "publish"):
                    v.status = "failed"
                v.error = err[:2000]
                save(v, "stages")
            except Exception:  # noqa: BLE001
                log.exception("could not mark video failed")


def worker_loop() -> None:
    n = recover()
    if n:
        log.info("re-queued %d interrupted jobs", n)
    poll = get_settings().worker_poll_seconds
    while not _stop.is_set():
        try:
            j = _claim()
        except Exception:  # noqa: BLE001
            log.exception("claim failed")
            j = None
        if j:
            run_job(j)
        else:
            _stop.wait(poll)


# ------------------------------------------------------------------ scheduler
def scheduler_tick(at: datetime | None = None) -> list[str]:
    from .pipeline import create_production, enqueue
    cfg = schedule_cfg()
    actions: list[str] = []
    if not cfg["enabled"]:
        return actions
    tz = ZoneInfo(cfg["timezone"])
    local = (at or datetime.now(tz)).astimezone(tz)
    day = local.strftime("%a").lower()
    today = local.date().isoformat()
    state = kv_get("scheduler_state")
    hh, mm = (int(x) for x in str(cfg["research_time"]).split(":"))
    if (local.hour, local.minute) < (hh, mm):
        return actions
    if day in cfg["research_days"] and state.get("research") != today:
        enqueue("discover_topics", payload={"per_category": 1})
        state["research"] = today
        actions.append("research")
    if day in cfg["production_days"] and state.get("production") != today:
        week_ago = now() - timedelta(days=7)
        with session() as s:
            made = len(s.exec(select(Video).where(Video.format == "long", Video.created_at >= week_ago)).all())
            status = ["approved"] + (["new"] if cfg["auto_pick_topics"] else [])
            topic = s.exec(select(Topic).where(Topic.status.in_(status),
                                               Topic.total_score >= cfg["min_topic_score"])
                           .order_by(case((Topic.status == "approved", 0), else_=1), Topic.total_score.desc())).first()
        state["production"] = today
        kv_set("scheduler_state", state)  # saved first so a failure can't re-trigger every minute
        if made < cfg["long_per_week"] and topic:
            v = create_production(topic.id, "long")
            actions.append(f"production video {v.id}")
    kv_set("scheduler_state", state)
    return actions


def scheduler_loop() -> None:
    while not _stop.is_set():
        try:
            scheduler_tick()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        _stop.wait(60)


def start() -> list[threading.Thread]:
    ts = [threading.Thread(target=worker_loop, name="worker", daemon=True),
          threading.Thread(target=scheduler_loop, name="scheduler", daemon=True)]
    for t in ts:
        t.start()
    return ts


def stop() -> None:
    _stop.set()
