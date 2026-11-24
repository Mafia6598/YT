"""Growth agent: weekly analysis of REAL analytics snapshots. If there is no data it says so."""
from __future__ import annotations

import logging
from collections import defaultdict
from statistics import mean
from typing import Any

from sqlmodel import select

from ..db import session
from ..llm import get_llm
from ..models import AnalyticsSnapshot, Topic, Video

log = logging.getLogger(__name__)


def latest_snapshots() -> list[dict]:
    with session() as s:
        snaps = s.exec(select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.created_at.desc())).all()
        seen, rows = set(), []
        for sn in snaps:
            if sn.youtube_id in seen:
                continue
            seen.add(sn.youtube_id)
            v = s.get(Video, sn.video_id) if sn.video_id else None
            t = s.get(Topic, v.topic_id) if v and v.topic_id else None
            sel = (v.thumbnails or {}).get("selected") if v else None
            concept = next((c["concept"] for c in (v.thumbnails or {}).get("concepts", [])
                            if c["index"] == sel), None) if v else None
            rows.append({**sn.model_dump(), "title": v.title if v else sn.youtube_id,
                         "format": v.format if v else "", "category": t.category if t else "unknown",
                         "duration_s": (v.render or {}).get("duration") if v else None,
                         "thumbnail_concept": concept,
                         "hook": (v.script or {}).get("hook", "") if v else ""})
        return rows


def _avg(vals):
    vals = [x for x in vals if x is not None]
    return round(mean(vals), 2) if vals else None


def weekly_report() -> dict[str, Any]:
    rows = latest_snapshots()
    if not rows:
        return {"has_data": False, "summary": "No analytics yet. Connect YouTube and run 'Sync analytics' "
                                              "after videos have been live for a few days.",
                "recommendations": []}
    longs = [r for r in rows if r["format"] != "short"]
    totals = {"videos": len(rows), "views": sum(r["views"] or 0 for r in rows),
              "watch_hours": round(sum(r["watch_minutes"] or 0 for r in rows) / 60, 1),
              "subscribers_gained": sum(r["subscribers_gained"] or 0 for r in rows),
              "revenue_usd": round(sum(r["revenue_usd"] or 0 for r in rows), 2)
              if any(r["revenue_usd"] is not None for r in rows) else None,
              "avg_ctr": _avg([r["ctr"] for r in rows]), "avg_view_pct": _avg([r["avg_view_pct"] for r in longs])}
    by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    cats = sorted(({"category": c, "videos": len(v), "avg_views": _avg([x["views"] for x in v]),
                    "avg_view_pct": _avg([x["avg_view_pct"] for x in v]), "avg_ctr": _avg([x["ctr"] for x in v])}
                   for c, v in by_cat.items()), key=lambda x: -(x["avg_views"] or 0))
    by_thumb: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["thumbnail_concept"] and r["ctr"] is not None:
            by_thumb[r["thumbnail_concept"]].append(r["ctr"])
    thumbs = {k: _avg(v) for k, v in by_thumb.items()}
    hooks = []
    for r in longs:
        ret = r.get("retention") or []
        at = [w for x, w in ret if 0.04 <= x <= 0.08]
        if at:
            hooks.append({"title": r["title"], "hook": r["hook"][:140], "retention_at_~30s": round(at[0], 3)})
    hooks.sort(key=lambda h: -h["retention_at_~30s"])
    recs = []
    if len(cats) > 1 and cats[0]["avg_views"]:
        recs.append(f"Top category by average views: {cats[0]['category']} ({cats[0]['avg_views']:,.0f}). "
                    f"Weight next week's research toward it.")
        if cats[-1]["avg_views"] is not None and cats[-1]["videos"] >= 2:
            recs.append(f"Weakest category: {cats[-1]['category']} ({cats[-1]['avg_views']:,.0f} avg views).")
    if thumbs and len(thumbs) > 1:
        best = max(thumbs, key=lambda k: thumbs[k])
        recs.append(f"Thumbnail concept with highest CTR so far: {best} ({thumbs[best]}%). Prefer it when scores tie.")
    if totals["avg_ctr"] is not None and totals["avg_ctr"] < 4:
        recs.append("Average CTR is under 4%: test bolder thumbnail text (2-3 words) and more specific titles.")
    if totals["avg_view_pct"] is not None and totals["avg_view_pct"] < 35:
        recs.append("Average view percentage is under 35%: tighten intros, put the first reveal before 60s, "
                    "and try 8-10 minute cuts.")
    if hooks:
        recs.append(f"Best hook by early retention: \"{hooks[0]['hook']}\". Reuse its structure.")
    if len(rows) < 5:
        recs.append("Fewer than 5 videos with data: treat all patterns as early signals, not conclusions.")
    report = {"has_data": True, "totals": totals, "categories": cats, "thumbnail_ctr": thumbs,
              "hooks": hooks[:5], "videos": rows, "recommendations": recs}
    llm = get_llm()
    if llm:
        try:
            report["ai_analysis"] = llm.complete(
                "You are a YouTube growth analyst. Use ONLY the numbers given. Never invent metrics. "
                "Be concrete: next topics, hooks, titles, thumbnails, length.",
                f"Weekly channel data (JSON):\n{ {k: report[k] for k in ('totals', 'categories', 'thumbnail_ctr', 'hooks')} }",
                max_tokens=16000)
        except Exception as e:  # noqa: BLE001
            report["ai_analysis_error"] = str(e)
    return report
