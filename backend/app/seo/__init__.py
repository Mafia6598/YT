"""YouTube SEO agent: titles, description, chapters, tags, hashtags, pinned comment."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..llm import complete_json, get_llm

log = logging.getLogger(__name__)


def _tc(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def chapters(script: dict) -> list[dict]:
    """YouTube rules: first chapter at 0:00, at least 3 chapters, each >= 10 s (the last one too)."""
    secs = [s for s in script.get("sections", []) if s.get("scenes")]
    total = script.get("duration_s") or (secs[-1]["scenes"][-1].get("end", 0) if secs else 0)
    out: list[dict] = []
    for s in secs:
        start = s["scenes"][0]["start"]
        if out and start - out[-1]["start"] < 10:
            continue
        out.append({"start": 0 if not out else start, "title": "Intro" if not out else s["heading"]})
    while len(out) > 1 and total - out[-1]["start"] < 10:
        out.pop()  # merge a too-short final chapter into the previous one
    return out if len(out) >= 3 else []


def sources_block(research: dict) -> str:
    lines = [f"- {research.get('title', '')} (Wikipedia, rev. {research.get('revision', '')}): {research.get('url', '')}"]
    for r in research.get("strong_references", [])[:10]:
        bits = [r.get("author"), r.get("title"), r.get("publisher"), r.get("date")]
        lines.append("- " + ", ".join(b for b in bits if b) + (f" {r['url']}" if r.get("url") else ""))
    return "\n".join(lines)


def credits_block(assets: list[dict]) -> str:
    need = [a for a in assets if a.get("attribution_required")]
    seen, lines = set(), []
    for a in need:
        key = a.get("source_url")
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {a.get('author', 'Unknown')} / {a.get('license')} / {a.get('source_url')}")
    return "\n".join(lines)


def generate(video_title: str, script: dict, research: dict, assets: list[dict], fmt: str = "long",
             affiliate_links: list[dict] | None = None, synthetic: bool = True) -> dict[str, Any]:
    chaps = chapters(script) if fmt == "long" else []
    base = _llm(video_title, script, fmt) if get_llm() else None
    if base is None:
        base = _offline(video_title, script, fmt)
    # required items first (disclosures, credits), sources last so a trim never removes them
    parts = [base["description_intro"]]
    if affiliate_links:  # only links the owner approved are passed in
        parts.append("LINKS (affiliate)\n" + "\n".join(f"- {a['name']}: {a['url']}" for a in affiliate_links)
                     + "\n" + affiliate_links[0]["disclosure"])
    if chaps:
        parts.append("CHAPTERS\n" + "\n".join(f"{_tc(c['start'])} {c['title']}" for c in chaps))
    if synthetic:
        parts.append("Narration voice and some visuals are AI-generated. Historical facts are drawn from the "
                     "sources listed below.")
    cr = credits_block(assets)
    if cr:
        parts.append("IMAGE CREDITS\n" + cr)
    parts.append(" ".join(base["hashtags"]))
    parts.append("SOURCES & FURTHER READING\n" + sources_block(research))
    desc = _clip_bytes(re.sub(r"[<>]", "", "\n\n".join(parts)), 4900)
    tags, total = [], 0
    for t in base["tags"]:  # YouTube limit: 500 chars total
        t = re.sub(r"[<>]", "", t).strip()
        if t and total + len(t) + 2 <= 480:
            tags.append(t)
            total += len(t) + 2
    titles = [re.sub(r"[<>]", "", t)[:100] for t in base["titles"][:3]]
    return {"title": titles[0], "title_options": titles,
            "description": desc, "tags": tags, "hashtags": base["hashtags"][:3],
            "keywords": base.get("keywords", []), "chapters": chaps,
            "pinned_comment": base["pinned_comment"], "thumbnail_text": base["thumbnail_text"],
            "category_id": "27",  # Education
            "generator": base.get("_generator", "offline-template")}


def _clip_bytes(s: str, limit: int) -> str:
    """YouTube limits the description to 5000 BYTES (UTF-8), not characters."""
    b = s.encode("utf-8")
    return s if len(b) <= limit else b[:limit].decode("utf-8", "ignore").rsplit("\n", 1)[0]


SEO_KEYS = {"titles": list, "description_intro": str, "keywords": list, "tags": list, "hashtags": list,
            "pinned_comment": str, "thumbnail_text": str}


def _llm(title: str, script: dict, fmt: str) -> dict | None:
    narration = " ".join(sc["narration"] for s in script.get("sections", []) for sc in s.get("scenes", []))
    try:
        data = complete_json(
            "You are a YouTube SEO strategist for a premium US history documentary channel. "
            "Titles must be compelling but accurate: never promise something the video does not deliver.",
            f"Format: {'YouTube Short' if fmt == 'short' else 'long-form documentary'}. Working title: {title}\n"
            f"Narration:\n{narration[:20000]}\n\nReturn JSON: {{"
            '"titles": [3 options, each <= 70 chars, strongest first], '
            '"description_intro": "2 short paragraphs, first 150 chars carry the main keyword", '
            '"keywords": [8-12 search phrases US viewers type], "tags": [15-25 tags], '
            '"hashtags": ["#History", 2 more], "pinned_comment": "question that sparks discussion", '
            '"thumbnail_text": "2-4 punchy words, not the title"}}', max_tokens=16000)
        if not isinstance(data, dict) or any(not isinstance(data.get(k), t) or not data.get(k)
                                             for k, t in SEO_KEYS.items()):
            raise ValueError("incomplete SEO JSON from model")
        data["_generator"] = "llm"
        return data
    except Exception as e:  # noqa: BLE001
        log.warning("SEO LLM failed: %s", e)
        return None


def _offline(title: str, script: dict, fmt: str) -> dict:
    hook = script.get("hook") or script.get("logline") or title
    words = [w for w in re.findall(r"[A-Za-z]+", title) if len(w) > 3]
    return {
        "titles": [f"{title}: The Untold Story", f"What Really Happened: {title}",
                   f"{title} - A History Documentary"] if fmt == "long" else
                  [f"{title} #shorts", f"The Truth About {title} #shorts", f"{title} in 60 Seconds #shorts"],
        "description_intro": f"{hook}\n\nThis documentary tells the story of {title}, using the sources listed below.",
        "keywords": [title, f"{title} history", f"{title} documentary", f"what happened {title}"],
        "tags": [title, f"{title} history", f"{title} documentary", "history documentary", "dark history",
                 "untold history", "history explained", "documentary"] + words,
        "hashtags": ["#History", "#Documentary", "#" + re.sub(r"\W", "", title.title())[:30]],
        "pinned_comment": f"Had you heard about {title} before this video? Tell me what surprised you most.",
        "thumbnail_text": " ".join(re.sub(r"[^\w\s'-]", "", title.split(":")[0]).upper().split()[:3]),
    }
