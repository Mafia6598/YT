"""Topic research agent: discover candidates, verify they exist in real sources,
score them, and build a fact-grounded research packet for script writing."""
from __future__ import annotations

import logging
import random
import re
from typing import Any

from ..llm import complete_json, get_llm
from ..util import ProviderUnavailable
from . import sources
from .seeds import CATEGORIES

log = logging.getLogger(__name__)

WEIGHTS = {"audience_interest": 0.25, "search_demand": 0.20, "competition": 0.15,
           "storytelling": 0.25, "significance": 0.15}
STORY_WORDS = ["mystery", "mysterious", "secret", "classified", "disappear", "vanish", "never found",
               "survivor", "survived", "escape", "betray", "conspiracy", "unexplained", "killed",
               "executed", "spy", "covert", "fortune", "richest", "hidden", "cover-up", "legend",
               "trial", "rescue", "doomed", "tragedy", "disaster", "catastrophe", "lost", "forgotten"]


def brainstorm_queries(categories: list[str], n: int) -> list[tuple[str, str]]:
    """LLM proposes *names* of events/people to look up. Never trusted as facts."""
    if not get_llm():
        return []
    try:
        data = complete_json(
            "You are a research assistant for a US-audience history documentary channel.",
            f"Propose {n} specific, real, well-documented historical subjects per category "
            f"for these categories: {categories}. Prefer lesser-known stories with strong narrative "
            "potential that still have solid documentation. Return JSON: "
            '[{"category": str, "query": "<exact name as it would appear as a Wikipedia article title>"}]. '
            "Only names, no descriptions.", max_tokens=16000)
        return [(d["category"], d["query"]) for d in data if d.get("query")]
    except Exception as e:  # brainstorming is optional
        log.warning("brainstorm failed: %s", e)
        return []


def _measure(fn, *a) -> tuple[Any, str]:
    try:
        return fn(*a), "measured"
    except ProviderUnavailable as e:
        return None, f"unavailable: {e}"


def score_topic(category: str, query: str, deep: bool = True) -> dict[str, Any] | None:
    hits = sources.wiki_search(query, limit=1)
    if not hits:
        return None
    art = sources.wiki_article(hits[0]["title"])
    if not art or art["is_disambiguation"] or len(art["text"]) < 1500:
        return None
    title = art["title"]
    scores: dict[str, Any] = {}

    pv, basis = _measure(sources.wiki_pageviews, title)
    scores["audience_interest"] = {
        "value": round(sources.log_scale((pv or {}).get("avg_daily", 0) + 1, 20, 20000), 1) if pv else 50.0,
        "basis": f"Wikipedia daily views {pv}" if pv else basis + " (neutral 50)"}

    sug, b2 = _measure(sources.youtube_autocomplete, title.split("(")[0].strip())
    comp, b3 = (_measure(sources.youtube_competition, f"{title} history") if deep
                else (None, "skipped"))
    if sug is not None or comp:
        demand = 0.0
        parts = []
        if sug is not None:
            demand += min(len(sug), 10) * 6
            parts.append(f"{len(sug)} YouTube suggestions")
        if comp:
            demand += sources.log_scale(comp["median_views"] + 1, 1e3, 1e6) * 0.4
            parts.append(f"median top-15 views {comp['median_views']:,}")
        scores["search_demand"] = {"value": round(min(demand, 100), 1), "basis": ", ".join(parts)}
    else:
        scores["search_demand"] = {"value": 50.0, "basis": f"{b2}; {b3} (neutral 50)"}

    if comp:
        top = sum(comp["top_views"]) / max(1, len(comp["top_views"]))
        val = 100 - sources.log_scale(top + 1, 1e4, 2e7) * 0.6 - min(comp["recent_uploads_1y"], 15) / 15 * 40
        scores["competition"] = {"value": round(max(val, 0), 1),
                                 "basis": f"higher = easier. top-5 avg views {int(top):,}, "
                                          f"{comp['recent_uploads_1y']} uploads in last year",
                                 "top_videos": comp["videos"][:5]}
    else:
        scores["competition"] = {"value": 50.0, "basis": b3 + " (neutral 50)"}

    low = art["text"].lower()
    hits_story = sum(low.count(w) for w in STORY_WORDS)
    scores["storytelling"] = {"value": round(min(100, 25 + hits_story * 3), 1),
                              "basis": f"{hits_story} narrative-signal words in source article (heuristic)"}
    sig = sources.log_scale(art["langlinks"] + 1, 2, 150) * 0.7 + sources.log_scale(art["length"], 5e3, 2e5) * 0.3
    scores["significance"] = {"value": round(sig, 1),
                              "basis": f"{art['langlinks']} language editions, article {art['length']:,} bytes"}

    total = sum(scores[k]["value"] * w for k, w in WEIGHTS.items())
    measured = sum(1 for k in WEIGHTS if "unavailable" not in scores[k]["basis"]
                   and "skipped" not in scores[k]["basis"])
    summary = re.split(r"(?<=\.)\s", art["text"].strip(), maxsplit=3)
    return {
        "title": title, "category": category, "query": query, "wiki_title": title,
        "summary": " ".join(summary[:3])[:600], "scores": {**scores, "confidence": f"{measured}/5 measured"},
        "total_score": round(total, 1),
        "sources": [{"title": f"{title} - Wikipedia", "url": art["url"], "publisher": "Wikipedia",
                     "kind": "tertiary", "revision": art["lastrevid"]}],
    }


def discover(categories: list[str] | None = None, per_category: int = 2, use_llm: bool = True,
             deep: bool = True) -> list[dict]:
    cats = categories or list(CATEGORIES)
    candidates: list[tuple[str, str]] = []
    for c in cats:
        pool = list(CATEGORIES.get(c, []))
        random.shuffle(pool)
        candidates += [(c, q) for q in pool[:per_category]]
    if use_llm:
        candidates += brainstorm_queries(cats, per_category)
    out, seen = [], set()
    for cat, q in candidates:
        try:
            t = score_topic(cat, q, deep=deep)
        except ProviderUnavailable:
            raise
        except Exception as e:
            log.warning("scoring %s failed: %s", q, e)
            continue
        if t and t["title"] not in seen:
            seen.add(t["title"])
            out.append(t)
    return sorted(out, key=lambda t: -t["total_score"])


def research_packet(wiki_title: str) -> dict[str, Any]:
    """Everything the script writer is allowed to use."""
    art = sources.wiki_article(wiki_title)
    if not art:
        raise ValueError(f"Wikipedia article not found: {wiki_title}")
    try:
        refs = sources.wiki_references(art["title"])
    except ProviderUnavailable:
        refs = []
    strong = [r for r in refs if r["reliable_domain"] or r["is_book_or_journal"]]
    related = []
    try:
        related = [h["title"] for h in sources.wiki_search(f'"{art["title"]}"', limit=4)
                   if h["title"] != art["title"]]
    except ProviderUnavailable:
        pass
    return {
        "title": art["title"], "url": art["url"], "revision": art["lastrevid"],
        "text": art["text"][:60000], "references": refs, "strong_references": strong[:15],
        "related_articles": related,
        "source_count": 1 + len(strong),
        "verification_note": (
            "Facts must come from the article text. The article's own cited sources are listed "
            "with publication details; the fact-check pass maps each script claim to the text. "
            "Claims flagged 'unsupported' need manual review before publishing."),
    }
