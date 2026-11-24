"""Documentary script generation (long-form and Shorts) + fact-check pass."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..llm import complete_json, get_llm

log = logging.getLogger(__name__)
WPM = 150  # documentary narration pace

SYSTEM = """You are the head writer of a premium, Netflix-style history documentary channel
for an American audience (18-45). You write original narration in native American English:
cinematic, tense, emotionally intelligent, never cheesy, never clickbait-dishonest.
ABSOLUTE RULES:
- Use ONLY facts present in the SOURCE TEXT provided. Do not add names, numbers, dates,
  quotes or events that are not in it. If something is uncertain in the source, say so on air.
- Never copy sentences verbatim from the source or from any other creator; rewrite everything.
- Every factual claim you make must be listed in "claims" with a short supporting quote
  copied from the SOURCE TEXT."""

LONG_SPEC = """Write a LONG-FORM documentary script of about {words} words of narration ({minutes} minutes).
Structure (use these purposes, multiple story sections allowed):
1. hook - first 5 seconds, a powerful line that opens a question the video answers.
2. intro - curiosity-driven setup, promise of what the viewer will learn.
3. story sections - chronological/logical, vivid concrete details, emotional moments.
   Insert a retention hook (open loop / tease) at the end of most sections.
4. climax / reveal.
5. conclusion - meaning and legacy.
6. cta - natural, one or two sentences (subscribe for the next story), no begging.
Each scene is 1-3 sentences (roughly 8-18 seconds of narration) and must have a DIFFERENT visual."""

SHORT_SPEC = """Write a YouTube SHORT script of {words} words ({seconds} seconds), vertical video.
First sentence is the hook (pattern interrupt, under 12 words). Fast but clear pacing, a complete
mini-story on its own, ends with a one-line CTA pointing to the full documentary on the channel.
Each scene is ONE short sentence."""

SCHEMA = """Return JSON:
{"title": str, "hook": str, "logline": str,
 "sections": [{"heading": str, "purpose": "hook|intro|story|retention|climax|conclusion|cta",
   "scenes": [{"narration": str,
               "visual": "what the viewer sees (shot description, camera move)",
               "image_query": "2-5 word search query for archival photos/footage of a REAL subject",
               "image_prompt": "prompt for an AI-generated cinematic illustration (no real-person likeness, no text)",
               "on_screen_text": "short lower-third/date/location text or empty",
               "sfx": "sound effect suggestion or empty", "music": "music mood", "mood": str}]}],
 "claims": [{"claim": str, "support": "exact quote from SOURCE TEXT"}]}"""


def _source_block(packet: dict) -> str:
    refs = "\n".join(f"- {r['title']} ({r.get('publisher', '')}, {r.get('date', '')}) {r.get('url', '')}"
                     for r in packet.get("strong_references", [])[:12])
    return f"SOURCE TEXT (Wikipedia: {packet['url']}):\n{packet['text'][:45000]}\n\nCITED SOURCES:\n{refs}"


def generate(packet: dict, fmt: str = "long", minutes: float = 10, focus: str = "",
             notes: str = "") -> dict[str, Any]:
    if get_llm():
        try:
            return _generate_llm(packet, fmt, minutes, focus, notes)
        except Exception as e:
            log.warning("LLM script generation failed, using offline template: %s", e)
            s = _generate_offline(packet, fmt, minutes, focus)
            s["warnings"].append(f"LLM failed ({e}); offline template used")
            return s
    return _generate_offline(packet, fmt, minutes, focus)


def _generate_llm(packet: dict, fmt: str, minutes: float, focus: str, notes: str) -> dict:
    if fmt == "short":
        secs = max(30, min(60, int(minutes * 60) if minutes < 2 else 50))
        spec = SHORT_SPEC.format(words=int(secs * 2.6), seconds=secs)
    else:
        spec = LONG_SPEC.format(words=int(minutes * WPM), minutes=minutes)
    prompt = f"{spec}\n{SCHEMA}\n"
    if focus:
        prompt += f"\nFOCUS ON THIS ANGLE/MOMENT: {focus}\n"
    if notes:
        prompt += f"\nEDITOR NOTES TO APPLY: {notes}\n"
    prompt += "\n" + _source_block(packet)
    data = complete_json(SYSTEM, prompt)
    data.update(format=fmt, generator="llm", warnings=[])
    data["claims"] = fact_check(packet, data.get("claims", []))
    return finalize(data)


MIN_QUOTE_WORDS = 6


def fact_check(packet: dict, claims: list[dict]) -> list[dict]:
    """Independent check of each claim against the source text.

    supported  = the claim itself appears verbatim in the source, OR an LLM judge said the
                 claim is supported AND its quoted evidence (>= 6 words) really is in the source.
    A matching quote alone is never enough: the quote must also back the claim."""
    text = packet["text"]
    ntext = _norm(text)
    out = []
    for c in claims:
        quote = (c.get("support") or "").strip()
        nq = _norm(quote)
        out.append({**c, "claim_verbatim_in_source": bool(_norm(c.get("claim", ""))) and _norm(c["claim"]) in ntext,
                    "quote_found_in_source": len(nq.split()) >= MIN_QUOTE_WORDS and nq in ntext})
    to_judge = [c for c in out if not c["claim_verbatim_in_source"]]
    if to_judge and get_llm():
        try:
            verdicts = complete_json(
                "You are a strict fact-checker. Judge ONLY against the source text given. A claim is "
                "supported only if the source states it; added numbers, names, motives or certainty "
                "make it not_in_source.",
                "For each numbered claim answer supported / contradicted / not_in_source and give the exact "
                "source sentence as evidence.\n"
                'Return JSON [{"i": int, "verdict": str, "evidence": "exact quote from SOURCE TEXT or empty"}]\n\n'
                + "\n".join(f"{i}. CLAIM: {c['claim']} | CITED QUOTE: {c.get('support', '')}"
                            for i, c in enumerate(to_judge))
                + "\n\nSOURCE TEXT:\n" + text[:45000], max_tokens=32000)
            for v in verdicts if isinstance(verdicts, list) else []:
                try:
                    c = to_judge[int(v["i"])]
                except (KeyError, ValueError, TypeError, IndexError):
                    continue  # one bad row must not discard the others
                ev = _norm(v.get("evidence") or "")
                c["verdict"] = v.get("verdict")
                c["evidence"] = v.get("evidence", "")
                c["evidence_found_in_source"] = len(ev.split()) >= MIN_QUOTE_WORDS and ev in ntext
        except Exception as e:
            log.warning("fact-check LLM pass failed: %s", e)
    for c in out:
        if c["claim_verbatim_in_source"] or (c.get("verdict") == "supported" and c.get("evidence_found_in_source")):
            c["status"] = "supported"
        elif c.get("verdict") == "contradicted":
            c["status"] = "contradicted"
        else:
            c["status"] = "needs_review"
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# --------------------------------------------------------------------------- offline
SKIP = {"see also", "references", "notes", "further reading", "external links", "bibliography",
        "sources", "citations", "footnotes", "in popular culture", "gallery", "works cited"}
TRANSITIONS = ["And this is where the story takes a turn.", "But the records tell us more.",
               "To understand why, we have to go back.", "What came next is harder to explain.",
               "Keep that detail in mind, because it matters later."]


def _sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"\n=+\s*(.*?)\s*=+\n", "\n" + text)
    out = [("Introduction", parts[0])]
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i], parts[i + 1]))
    return [(h, b.strip()) for h, b in out if h.lower() not in SKIP and len(b.strip()) > 200]


def _sentences(t: str) -> list[str]:
    t = re.sub(r"\s+", " ", t)  # keep parentheticals: they often carry hedges ("disputed", "estimated")
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"])", t) if 30 < len(s.strip()) < 320]


def _query(sentence: str, title: str) -> str:
    """Archive search query: a proper noun from the sentence (not its first word) + topic."""
    rest = sentence.split(" ", 1)[1] if " " in sentence else ""
    caps = [c for c in re.findall(r"\b(?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\b", rest)
            if c.lower() not in title.lower()]
    return (f"{caps[0]} {title}" if caps else title)[:70]


def _generate_offline(packet: dict, fmt: str, minutes: float, focus: str) -> dict:
    """Extractive draft: every fact sentence is a light rewrite-free extract from the
    source, so nothing is invented. Quality is basic; configure an LLM for real scripts."""
    title = packet["title"]
    secs = _sections(packet["text"])
    if sum(len(b) for _, b in secs) < 600:
        raise ValueError(f"Source text for '{title}' is too thin to write a documentary from")
    if focus:
        f = focus.lower()
        secs = sorted(secs, key=lambda s: (s[0].lower() != f, -s[1].lower().count(f)))
    target_words = int(minutes * WPM) if fmt == "long" else 120
    claims, sections, words = [], [], 0
    lead = _sentences(secs[0][1]) if secs else []
    hook_line = lead[0] if lead else title
    sections.append({"heading": "Cold open", "purpose": "hook", "scenes": [{
        "narration": f"{hook_line} But the full story is far stranger than that one sentence.",
        "visual": "Slow push-in on a dark archival image, film grain", "image_query": title,
        "image_prompt": f"cinematic dark documentary establishing shot evoking {title}, moody light",
        "on_screen_text": title.upper(), "sfx": "low boom", "music": "tense ambient drone", "mood": "ominous"}]})
    claims.append({"claim": hook_line, "support": hook_line})
    words += len(hook_line.split()) + 10
    per_scene = 2 if fmt == "long" else 1
    for si, (heading, body) in enumerate(secs):
        sents = _sentences(body)
        if si == 0:
            sents = sents[1:]
        scenes = []
        for i in range(0, len(sents), per_scene):
            chunk = sents[i:i + per_scene]
            narr = " ".join(chunk)
            scenes.append({"narration": narr, "visual": f"Archival or illustrative imagery: {_query(chunk[0], title)}",
                           "image_query": f"{_query(chunk[0], title)}",
                           "image_prompt": f"cinematic historical illustration, {heading.lower()}, {title}, dramatic chiaroscuro",
                           "on_screen_text": (re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", narr) or [""])[0],
                           "sfx": "", "music": "documentary underscore", "mood": "tense"})
            claims += [{"claim": c, "support": c} for c in chunk]
            words += len(narr.split())
            if words >= target_words or len(scenes) >= (6 if fmt == "long" else 4):
                break
        if scenes:
            if fmt == "long" and si < len(secs) - 1:
                scenes[-1]["narration"] += " " + TRANSITIONS[si % len(TRANSITIONS)]
            sections.append({"heading": heading, "purpose": "intro" if si == 0 else "story", "scenes": scenes})
        if words >= target_words:
            break
    cta = ("If you want more forgotten stories like this one, subscribe. The next file is already open."
           if fmt == "long" else "The full story is on the channel.")
    sections.append({"heading": "Closing", "purpose": "cta", "scenes": [{
        "narration": cta, "visual": "Slow fade on final image, channel end card", "image_query": title,
        "image_prompt": f"cinematic closing shot, {title}, fading light", "on_screen_text": "SUBSCRIBE",
        "sfx": "", "music": "resolve", "mood": "reflective"}]})
    warn = ["Offline extractive draft: no LLM key configured. Facts are copied from the source and "
            "need a human rewrite for originality and storytelling before publishing."]
    if fmt == "long" and words < 8 * WPM:
        warn.append(f"Source text only supported ~{words} words (< 8 minutes).")
    data = {"title": title, "hook": hook_line, "logline": hook_line, "format": fmt, "sections": sections,
            "claims": fact_check(packet, claims),
            "generator": "offline-template", "warnings": warn}
    return finalize(data)


def finalize(script: dict, durations: list[float] | None = None) -> dict:
    """Assign scene ids and timecodes (estimated from word count, or real audio durations)."""
    t, k = 0.0, 0
    for sec in script.get("sections", []):
        sec["start"] = round(t, 2)
        for sc in sec.get("scenes", []):
            sc["id"] = k
            d = durations[k] if durations and k < len(durations) else \
                max(2.0, len(sc.get("narration", "").split()) / WPM * 60)
            sc["start"], sc["end"] = round(t, 2), round(t + d, 2)
            sc["timecode"] = f"{_tc(t)} - {_tc(t + d)}"
            t += d
            k += 1
    script["duration_s"] = round(t, 1)
    script["word_count"] = sum(len(sc.get("narration", "").split()) for sc in iter_scenes(script))
    return script


def iter_scenes(script: dict):
    for sec in script.get("sections", []):
        yield from sec.get("scenes", [])


def _tc(s: float) -> str:
    return f"{int(s // 60):02d}:{int(s % 60):02d}"
