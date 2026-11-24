"""Revenue planning. Scenarios are arithmetic on views and the RPM values YOU provide.
Nothing here is a forecast or a guarantee."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlmodel import select

from ..db import session
from ..models import RevenueEntry
from ..analytics import latest_snapshots

STREAMS = ["ads", "affiliate", "sponsorship", "digital_products", "other"]


def scenarios(monthly_views: float | None, rpms: dict[str, float], target: float = 1000.0,
              other_monthly: float = 0.0) -> dict[str, Any]:
    actual_views = sum((r["views"] or 0) for r in latest_snapshots() if r.get("window_days") == 28)
    views = monthly_views if monthly_views is not None else actual_views
    out = []
    for name, rpm in rpms.items():
        ads = views / 1000 * rpm
        need = max(0.0, target - other_monthly) / rpm * 1000 if rpm > 0 else None
        out.append({"scenario": name, "rpm_usd": rpm, "monthly_views": int(views),
                    "ad_revenue_usd": round(ads, 2), "total_with_other_usd": round(ads + other_monthly, 2),
                    "views_needed_for_target": int(need) if need is not None else None})
    return {"views_basis": "user-provided" if monthly_views is not None else "actual last-28-day views from analytics",
            "actual_28d_views": actual_views, "target_usd": target, "scenarios": out,
            "notes": ["YouTube ads revenue requires the YouTube Partner Program "
                      "(1,000 subscribers + 4,000 public watch hours in 12 months, or 10M Shorts views in 90 days).",
                      "RPM varies widely by niche, audience country, season and video length. "
                      "Use your own RPM from YouTube Studio once available.",
                      "These numbers are scenarios, not guarantees."]}


def summary() -> dict[str, Any]:
    with session() as s:
        rows = s.exec(select(RevenueEntry).order_by(RevenueEntry.month)).all()
    by_month: dict[str, dict] = defaultdict(lambda: {k: 0.0 for k in STREAMS})
    for r in rows:
        by_month[r.month][r.stream if r.stream in STREAMS else "other"] += r.amount_usd
    months = [{"month": m, **v, "total": round(sum(v.values()), 2)} for m, v in sorted(by_month.items())]
    yt = [r for r in latest_snapshots() if r.get("revenue_usd") is not None]
    return {"months": months, "entries": [r.model_dump() for r in rows],
            "youtube_reported_28d_revenue": round(sum(r["revenue_usd"] for r in yt), 2) if yt else None}
