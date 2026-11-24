"""Real data sources for research. Each function raises ProviderUnavailable when
it cannot reach its source, so callers can degrade gracefully and report it."""
from __future__ import annotations

import logging
import math
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from ..config import get_settings
from ..util import ProviderUnavailable, get_json

log = logging.getLogger(__name__)
WIKI_API = "https://en.wikipedia.org/w/api.php"
RELIABLE_DOMAINS = (
    ".gov", ".edu", ".mil", "britannica.com", "smithsonianmag.com", "si.edu", "history.com",
    "bbc.co.uk", "bbc.com", "nytimes.com", "theguardian.com", "archive.org", "loc.gov",
    "nationalarchives.gov.uk", "jstor.org", "cia.gov", "nasa.gov", "nature.com", "doi.org",
    "washingtonpost.com", "reuters.com", "apnews.com", "pbs.org", "npr.org", "ushmm.org",
    "iwm.org.uk", "wilsoncenter.org", "nsarchive2.gwu.edu", "books.google.",
)


def _wiki(params: dict) -> dict:
    try:
        return get_json(WIKI_API, {**params, "format": "json", "formatversion": 2})
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderUnavailable(f"Wikipedia unreachable: {e}") from e


def wiki_search(query: str, limit: int = 5) -> list[dict]:
    d = _wiki({"action": "query", "list": "search", "srsearch": query, "srlimit": limit})
    return [{"title": r["title"], "snippet": re.sub(r"<.*?>", "", r.get("snippet", "")),
             "wordcount": r.get("wordcount", 0)} for r in d.get("query", {}).get("search", [])]


def wiki_article(title: str) -> dict[str, Any]:
    """Full plain-text article + metadata. Returns {} if the page doesn't exist."""
    d = _wiki({"action": "query", "prop": "extracts|info|langlinks|pageprops", "explaintext": 1,
               "titles": title, "redirects": 1, "inprop": "url", "lllimit": "max"})
    pages = d.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return {}
    p = pages[0]
    return {
        "title": p["title"], "url": p.get("fullurl", f"https://en.wikipedia.org/wiki/{quote(p['title'])}"),
        "text": p.get("extract", ""), "langlinks": len(p.get("langlinks", [])),
        "length": p.get("length", 0), "lastrevid": p.get("lastrevid"),
        "is_disambiguation": "disambiguation" in p.get("pageprops", {}),
    }


def wiki_references(title: str, limit: int = 40) -> list[dict]:
    """Citations used by the article, with publication details where present."""
    d = _wiki({"action": "parse", "page": title, "prop": "wikitext", "redirects": 1})
    wt = d.get("parse", {}).get("wikitext", "")
    refs = []
    for m in re.finditer(r"\{\{\s*[Cc]ite\s+(\w+)(.*?)\}\}", wt, re.S):
        fields = {}
        for part in m.group(2).split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip().lower()] = re.sub(r"\[\[|\]\]|''+", "", v.strip())
        ref = {
            "type": m.group(1).lower(), "title": fields.get("title", ""),
            "author": fields.get("last", fields.get("last1", fields.get("author", fields.get("author1", "")))),
            "publisher": fields.get("publisher", fields.get("work", fields.get("website", fields.get("journal", "")))),
            "date": fields.get("date", fields.get("year", "")), "url": fields.get("url", ""),
            "isbn": fields.get("isbn", ""), "doi": fields.get("doi", ""),
        }
        if ref["title"]:
            ref["reliable_domain"] = _reliable(ref["url"])
            ref["is_book_or_journal"] = ref["type"] in ("book", "journal") or bool(ref["isbn"] or ref["doi"])
            refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def _reliable(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower() if url else ""
    if not host:
        return False
    for d in RELIABLE_DOMAINS:
        d = d.lstrip(".")
        if d.endswith("."):  # prefix rule like "books.google."
            if host.startswith(d) or (".%s" % d) in "." + host:
                return True
        elif host == d or host.endswith("." + d) or (d in ("gov", "edu", "mil") and host.split(".")[-1] == d):
            return True
    return False


def wiki_pageviews(title: str, days: int = 60) -> dict:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/"
           f"all-access/user/{quote(title.replace(' ', '_'), safe='')}/daily/"
           f"{start:%Y%m%d}/{end:%Y%m%d}")
    try:
        items = get_json(url).get("items", [])
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderUnavailable(f"Wikimedia pageviews unreachable: {e}") from e
    views = [i["views"] for i in items]
    if not views:
        return {"avg_daily": 0, "trend": 0.0, "days": 0}
    half = len(views) // 2 or 1
    first, last = sum(views[:half]) / half, sum(views[half:]) / max(1, len(views) - half)
    return {"avg_daily": round(sum(views) / len(views)), "trend": round((last - first) / max(first, 1), 3),
            "days": len(views)}


def youtube_autocomplete(query: str) -> list[str]:
    """Unofficial YouTube suggest endpoint. Optional (USE_YOUTUBE_AUTOCOMPLETE)."""
    if not get_settings().use_youtube_autocomplete:
        raise ProviderUnavailable("autocomplete disabled")
    try:
        d = get_json("https://suggestqueries.google.com/complete/search",
                     {"client": "firefox", "ds": "yt", "hl": "en", "gl": "us", "q": query})
        return list(d[1])
    except (httpx.HTTPError, ValueError, IndexError) as e:
        raise ProviderUnavailable(f"YouTube suggest unreachable: {e}") from e


def youtube_competition(query: str) -> dict:
    """Uses YouTube Data API v3 (YOUTUBE_API_KEY). ~101 quota units per call."""
    key = get_settings().youtube_api_key
    if not key:
        raise ProviderUnavailable("YOUTUBE_API_KEY not set")
    try:
        s = get_json("https://www.googleapis.com/youtube/v3/search", {
            "part": "snippet", "q": query, "type": "video", "maxResults": 15,
            "regionCode": "US", "relevanceLanguage": "en", "key": key})
        ids = [i["id"]["videoId"] for i in s.get("items", [])]
        total = s.get("pageInfo", {}).get("totalResults", 0)
        stats = get_json("https://www.googleapis.com/youtube/v3/videos", {
            "part": "statistics,snippet,contentDetails", "id": ",".join(ids), "key": key}) if ids else {"items": []}
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderUnavailable(f"YouTube Data API error: {e}") from e
    vids = []
    for v in stats.get("items", []):
        vids.append({"id": v["id"], "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
                     "published": v["snippet"]["publishedAt"],
                     "views": int(v["statistics"].get("viewCount", 0))})
    views = sorted((v["views"] for v in vids), reverse=True)
    recent = [v for v in vids if v["published"] >= (date.today() - timedelta(days=365)).isoformat()]
    return {"total_results": total, "top_views": views[:5], "median_views": views[len(views) // 2] if views else 0,
            "recent_uploads_1y": len(recent), "videos": vids[:10]}


def log_scale(x: float, lo: float, hi: float) -> float:
    """Map x onto 0..100 on a log scale between lo and hi."""
    if x <= lo:
        return 0.0
    return max(0.0, min(100.0, 100 * (math.log10(x) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))))
