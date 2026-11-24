"""End-to-end and unit tests. Network-dependent providers are mocked or skipped.

Run:  cd backend && python -m pytest -q
Full render test (slower, ~1-3 min): RUN_RENDER=1 python -m pytest -q -k e2e
"""
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", str(Path(__file__).parent / "_data"))
os.environ["LLM_PROVIDER"] = "offline"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["IMAGE_PROVIDERS"] = "procedural"
os.environ.setdefault("TTS_PROVIDER", "espeak")
os.environ.setdefault("TTS_FALLBACKS", "espeak")

from fixtures import PACKET  # noqa: E402

from app import pipeline, worker  # noqa: E402
from app.db import init_db, session  # noqa: E402
from app.models import Job, Topic, Video  # noqa: E402
from app.research import agent, sources  # noqa: E402
from app.script import generator  # noqa: E402
from app.seo import chapters  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _db():
    d = Path(os.environ["DATA_DIR"])
    shutil.rmtree(d, ignore_errors=True)
    init_db()
    yield


def test_offline_script_uses_only_source_sentences():
    sc = generator.generate(PACKET, "long", 3)
    assert sc["generator"] == "offline-template"
    assert sc["sections"][0]["purpose"] == "hook" and sc["sections"][-1]["purpose"] == "cta"
    norm = generator._norm(PACKET["text"])
    for c in sc["claims"]:
        assert generator._norm(c["claim"]) in norm, c["claim"]
    assert all("timecode" in s for s in generator.iter_scenes(sc))
    assert "See also" not in [s["heading"] for s in sc["sections"]]


def test_short_script_focus():
    sc = generator.generate(PACKET, "short", 0.85, focus="Search and discovery")
    assert sc["sections"][1]["heading"] == "Search and discovery"
    assert sc["duration_s"] < 90


def test_fact_check_flags_unsupported():
    out = generator.fact_check(PACKET, [{"claim": "x", "support": "The tent had been cut open from the inside"},
                                        {"claim": "aliens did it", "support": "aliens landed"}])
    assert out[0]["status"] == "supported" and out[1]["status"] == "needs_review"


def test_scoring_with_mocked_sources(monkeypatch):
    monkeypatch.setattr(sources, "wiki_search", lambda q, limit=1: [{"title": "Dyatlov Pass incident"}])
    monkeypatch.setattr(sources, "wiki_article", lambda t: {
        "title": t, "url": PACKET["url"], "text": PACKET["text"], "langlinks": 45, "length": 60000,
        "lastrevid": 1, "is_disambiguation": False})
    monkeypatch.setattr(sources, "wiki_pageviews", lambda t: {"avg_daily": 3000, "trend": 0.1, "days": 60})

    def nope(*a):
        raise sources.ProviderUnavailable("offline")
    monkeypatch.setattr(sources, "youtube_autocomplete", nope)
    monkeypatch.setattr(sources, "youtube_competition", nope)
    t = agent.score_topic("Historical Mysteries", "Dyatlov Pass incident")
    assert 0 < t["total_score"] <= 100
    assert t["scores"]["search_demand"]["value"] == 50.0
    assert "unavailable" in t["scores"]["competition"]["basis"]
    assert t["scores"]["confidence"] == "3/5 measured"


def test_chapters_rules():
    sc = generator.finalize({"sections": [
        {"heading": h, "scenes": [{"narration": "word " * 40}]} for h in ["a", "b", "c", "d"]]})
    ch = chapters(sc)
    assert ch[0]["start"] == 0 and len(ch) >= 3


def test_upload_refuses_public():
    from app import youtube
    with pytest.raises(PermissionError):
        youtube.upload("x.mp4", {"title": "t", "description": ""}, "public")
    with pytest.raises(PermissionError):
        youtube.publish("abc", {})


def test_revenue_scenarios():
    from app.revenue import scenarios
    r = scenarios(200000, {"typical": 5.0}, target=1000)
    assert r["scenarios"][0]["ad_revenue_usd"] == 1000.0
    assert r["scenarios"][0]["views_needed_for_target"] == 200000


def test_worker_retry_and_recovery(monkeypatch):
    calls = {"n": 0}

    def flaky(job):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return {"ok": True}
    monkeypatch.setitem(pipeline.HANDLERS, "flaky", flaky)
    j = pipeline.enqueue("flaky")
    worker.run_job(worker._claim())
    with session() as s:
        jj = s.get(Job, j.id)
        assert jj.status == "queued" and jj.attempts == 1
        jj.run_after = jj.created_at
        jj.status = "running"  # simulate crash mid-run
        s.add(jj)
        s.commit()
    assert worker.recover() >= 1
    worker.run_job(worker._claim())
    with session() as s:
        assert s.get(Job, j.id).status == "done"


def _drain(max_jobs=50):
    for _ in range(max_jobs):
        j = worker._claim()
        if not j:
            return
        worker.run_job(j)


@pytest.mark.skipif(not os.environ.get("RUN_RENDER"), reason="set RUN_RENDER=1 for the full render test")
def test_e2e_long_and_short(monkeypatch):
    monkeypatch.setattr(agent, "research_packet", lambda title: dict(PACKET))
    with session() as s:
        t = Topic(title="Dyatlov Pass incident", wiki_title="Dyatlov Pass incident", category="Historical Mysteries")
        s.add(t)
        s.commit()
        s.refresh(t)
    pipeline.DEFAULT_PIPELINE.update(gates=["script"], fast_render=True)
    v = pipeline.create_production(t.id, "long", minutes=2)
    _drain()
    v = pipeline.load(v.id)
    assert v.status == "awaiting_approval" and v.stages["script"]["status"] == "awaiting_approval"
    pipeline.approve(v.id, "script")
    _drain()
    v = pipeline.load(v.id)
    assert v.stages["final"]["status"] == "awaiting_approval", v.stages
    assert Path(v.render["path"]).exists() and v.render["size"] == "1920x1080"
    assert len(v.thumbnails["concepts"]) == 3 and v.seo["title"]
    assert abs(v.render["duration"] - v.audio["duration"]) < 1.0
    # shorts
    pipeline.DEFAULT_PIPELINE.update(gates=[])
    pipeline.enqueue("shorts", v.id, {"count": 2})
    _drain()
    with session() as s:
        kids = s.exec(__import__("sqlmodel").select(Video).where(Video.parent_id == v.id)).all()
    assert len(kids) == 2
    for k in kids:
        assert k.render["size"] == "1080x1920" and Path(k.render["path"]).exists(), k.stages
        assert k.render["duration"] <= 90


# ---------------------------------------------------------------- review regressions
def _video(status="awaiting_approval", stages=None, **kw):
    with session() as s:
        v = Video(title="t", status=status, stages=stages or {}, **kw)
        s.add(v)
        s.commit()
        s.refresh(v)
        return v


def test_approve_requires_awaiting_state():
    v = _video(status="researching", stages={"research": {"status": "running"}})
    with pytest.raises(ValueError):
        pipeline.approve(v.id, "final")
    v = _video(stages={"script": {"status": "awaiting_approval"}})
    pipeline.approve(v.id, "script")
    with pytest.raises(ValueError):  # double click must not queue a second voice job
        pipeline.approve(v.id, "script")


def test_reject_stops_chain_and_cancels_jobs():
    v = _video(status="rendering", stages={"render": {"status": "running"}})
    j = pipeline.enqueue("seo", v.id)
    pipeline.reject(v.id, "render")
    pipeline.stage_done(v.id, "render")
    v2 = pipeline.load(v.id)
    assert v2.status == "rejected" and "seo" not in [k for k, st in v2.stages.items() if st["status"] == "queued"]
    with session() as s:
        assert s.get(Job, j.id).status == "cancelled"


def test_recover_does_not_rerun_uploads_or_exhausted_jobs():
    with session() as s:
        a = Job(kind="upload", status="running", attempts=1)
        b = Job(kind="render", status="running", attempts=5, max_attempts=3)
        c = Job(kind="render", status="running", attempts=1, max_attempts=3)
        s.add_all([a, b, c])
        s.commit()
        ids = (a.id, b.id, c.id)
    worker.recover()
    with session() as s:
        assert [s.get(Job, i).status for i in ids] == ["failed", "failed", "queued"]
        s.get(Job, ids[2]).status = "cancelled"
        s.commit()


def test_publish_needs_matching_unused_nonce(monkeypatch):
    from app import youtube
    monkeypatch.setattr(youtube, "publish", lambda *a, **k: {"status": "public"})
    v = _video(status="uploaded", stages={"final": {"status": "approved"}},
               youtube={"youtube_id": "abc", "publish_approval": {"approved_by_owner": True, "nonce": "n1"}})
    bad = Job(kind="publish", video_id=v.id, payload={"approval": {"approved_by_owner": True, "nonce": "zz"}})
    with pytest.raises(PermissionError):
        pipeline.h_publish(bad)
    good = Job(kind="publish", video_id=v.id, payload={"approval": {"approved_by_owner": True, "nonce": "n1"}})
    pipeline.h_publish(good)
    v2 = pipeline.load(v.id)
    v2.status = "uploaded"
    pipeline.save(v2)
    with pytest.raises(PermissionError):  # replay of the same approval
        pipeline.h_publish(good)


def test_regenerate_final_rejected():
    v = _video()
    with pytest.raises(ValueError):
        pipeline.regenerate(v.id, "final")


def test_fact_check_rejects_short_or_unrelated_quotes():
    out = generator.fact_check(PACKET, [
        {"claim": "The KGB murdered all nine hikers", "support": "the hikers"},
        {"claim": "Searchers found 50 bodies", "support": "Searchers found"},
        {"claim": "Searchers found the abandoned tent on 26 February.", "support": ""}])
    assert [c["status"] for c in out] == ["needs_review", "needs_review", "supported"]


def test_license_filter():
    from app.video.visuals import license_ok
    assert license_ok("CC BY-SA 4.0") and license_ok("Public domain") and license_ok("CC0")
    for bad in ["CC BY-NC 4.0", "CC BY-ND 4.0", "CC BY-NC-SA 3.0", "pd-textlogo", "Fair use", "GFDL"]:
        assert not license_ok(bad), bad


def test_last_chapter_at_least_10s():
    sc = generator.finalize({"sections": [
        {"heading": h, "scenes": [{"narration": "word " * n}]} for h, n in [("a", 40), ("b", 40), ("c", 40), ("d", 5)]]})
    ch = chapters(sc)
    assert sc["duration_s"] - ch[-1]["start"] >= 10


def test_subtitle_time_formats():
    from app.video.render import _ass_time, _srt_time
    assert _ass_time(59.996) == "0:01:00.00" and _srt_time(1.001) == "00:00:01,001"
