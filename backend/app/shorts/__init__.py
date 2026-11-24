"""Shorts repurposing: pick 2-4 standalone moments from a long script. Each moment is
re-written as an independent vertical story (new hook, new narration, CTA), not a crop."""
from __future__ import annotations

import logging
import re

from ..llm import complete_json, get_llm
from ..research.agent import STORY_WORDS

log = logging.getLogger(__name__)


def pick_moments(script: dict, n: int = 3) -> list[dict]:
    n = max(2, min(4, n))
    secs = [s for s in script.get("sections", []) if s.get("purpose") not in ("hook", "cta") and s.get("scenes")]
    if get_llm():
        try:
            listing = "\n".join(f"[{i}] {s['heading']}: " + " ".join(sc["narration"] for sc in s["scenes"])[:1500]
                                for i, s in enumerate(secs))
            picks = complete_json(
                "You pick the most shareable moments of a history documentary for YouTube Shorts.",
                f"Choose {n} DIFFERENT moments that each work as a complete 30-60 second story with a "
                "shocking-but-true hook. Return JSON [{\"section\": int, \"focus\": \"one-line angle\", "
                "\"hook_idea\": str, \"why\": str}]\n\n" + listing, max_tokens=16000)
            out, seen = [], set()
            for p in picks if isinstance(picks, list) else []:
                try:
                    i = int(p["section"])
                except (KeyError, ValueError, TypeError):
                    continue
                if 0 <= i < len(secs) and i not in seen and p.get("focus"):
                    seen.add(i)
                    out.append({"section": secs[i]["heading"], "focus": p["focus"],
                                "hook_idea": p.get("hook_idea", ""), "why": p.get("why", "")})
            if len(out) >= 2:
                return out[:n]
            log.warning("LLM returned too few usable moments; using heuristic")
        except Exception as e:  # noqa: BLE001
            log.warning("LLM moment picking failed: %s", e)

    def interest(s: dict) -> float:
        t = " ".join(sc["narration"] for sc in s["scenes"]).lower()
        return (sum(t.count(w) for w in STORY_WORDS) * 2 + len(re.findall(r"\d", t)) * 0.3
                + len(re.findall(r"[!?]", t)))
    ranked = sorted(secs, key=interest, reverse=True)[:n]
    return [{"section": s["heading"], "focus": s["heading"], "hook_idea": "", "why": f"heuristic interest {interest(s):.1f}"}
            for s in ranked]
