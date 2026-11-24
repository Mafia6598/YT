"""Thumbnail studio: 3 concepts per video (1280x720), heuristic + optional vision-model scoring."""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

from ..config import get_settings
from ..util import extract_json
from ..video.visuals import font

log = logging.getLogger(__name__)
W, H = 1280, 720
YELLOW, RED, WHITE = (255, 214, 0), (220, 30, 30), (255, 255, 255)


def _cover(img: Image.Image, w: int, h: int, focus=(0.5, 0.5)) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), method=Image.LANCZOS, centering=focus)


def _text(draw, xy, words: list[str], size: int, highlight: int, anchor="la", max_w: int = 700):
    f = font(size)
    x, y = xy
    line, lines = [], []
    for w in words:  # wrap
        if draw.textlength(" ".join(line + [w]), font=f) > max_w and line:
            lines.append(line)
            line = []
        line.append(w)
    lines.append(line)
    k = 0
    for ln in lines:
        cx = x
        for w in ln:
            col = YELLOW if k == highlight else WHITE
            draw.text((cx, y), w, font=f, fill=col, stroke_width=max(4, size // 14), stroke_fill=(0, 0, 0))
            cx += draw.textlength(w + " ", font=f)
            k += 1
        y += int(size * 1.08)
    return y


def _fit(draw, words: list[str], size: int, max_w: int, max_h: int) -> int:
    """Largest font size (<= size) where wrapped text fits max_w x max_h."""
    while size > 28:
        f = font(size)
        widest = max(draw.textlength(w, font=f) for w in words)
        lines, cur = 1, ""
        for w in words:
            t = (cur + " " + w).strip()
            if draw.textlength(t, font=f) > max_w and cur:
                lines, cur = lines + 1, w
            else:
                cur = t
        if widest <= max_w and lines * size * 1.08 <= max_h:
            return size
        size -= 4
    return size


def concept_a(bg: Image.Image, words: list[str]) -> Image.Image:
    """Focal subject right, big text left over dark gradient."""
    im = _cover(bg, W, H, (0.7, 0.4))
    im = ImageEnhance.Contrast(im).enhance(1.35)
    grad = Image.linear_gradient("L").rotate(90).resize((W, H))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, ImageOps.invert(grad).point(lambda v: int(v * 0.85)))
    d = ImageDraw.Draw(im)
    d.rectangle([48, 120, 62, 560], fill=RED)
    size = _fit(d, words, 118, 640, 520)
    _text(d, (90, 130), words, size, highlight=len(words) - 1, max_w=640)
    return im


def concept_b(bg: Image.Image, words: list[str]) -> Image.Image:
    """Centered focal point with red ring and top banner text."""
    im = _cover(bg, W, H, (0.5, 0.5))
    im = ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.4)).enhance(0.7)
    vign = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vign).ellipse([W * 0.18, H * 0.05, W * 0.82, H * 1.1], fill=255)
    im = Image.composite(im, ImageEnhance.Brightness(im).enhance(0.25), vign.filter(ImageFilter.GaussianBlur(90)))
    d = ImageDraw.Draw(im)
    d.ellipse([W * 0.56, H * 0.38, W * 0.80, H * 0.84], outline=RED, width=12)
    f = font(104)
    txt = " ".join(words).upper()
    tw = d.textlength(txt, font=f)
    if tw > W - 80:
        f = font(int(104 * (W - 80) / tw))
        tw = d.textlength(txt, font=f)
    d.text(((W - tw) / 2, 36), txt, font=f, fill=WHITE, stroke_width=8, stroke_fill=(0, 0, 0))
    return im


def concept_c(bg: Image.Image, words: list[str]) -> Image.Image:
    """Desaturated tension shot, red split tint, text block bottom-left."""
    im = _cover(bg, W, H, (0.5, 0.35))
    gray = ImageOps.grayscale(im).convert("RGB")
    im = Image.blend(gray, im, 0.35)
    im = ImageEnhance.Contrast(im).enhance(1.5)
    tint = Image.new("RGB", (W // 2, H), (150, 10, 10))
    im.paste(Image.blend(im.crop((W // 2, 0, W, H)), tint, 0.35), (W // 2, 0))
    d = ImageDraw.Draw(im)
    up = [w.upper() for w in words]
    size = _fit(d, up, 96, W - 180, 230)
    f = font(size)
    lines, cur = [], ""
    for w in up:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) > W - 180 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    tw = max(d.textlength(l, font=f) for l in lines)
    top = H - 60 - len(lines) * size * 1.08 - 20
    d.rectangle([40, top, 40 + tw + 60, H - 50], fill=(0, 0, 0))
    _text(d, (70, top + 12), up, size, highlight=0, max_w=W - 180)
    return im


CONCEPTS = {"A: subject right / text left": concept_a, "B: centered focus + ring": concept_b,
            "C: tension split + text block": concept_c}


def heuristic_score(img: Image.Image, words: list[str]) -> dict[str, float]:
    small = img.resize((168, 94))
    lum = ImageStat.Stat(small.convert("L"))
    contrast = min(100, lum.stddev[0] / 70 * 100)
    sat = ImageStat.Stat(small.convert("HSV").split()[1]).mean[0] / 255 * 100
    edges = ImageStat.Stat(small.convert("L").filter(ImageFilter.FIND_EDGES).crop((40, 15, 128, 80))).mean[0]
    focal = min(100, edges / 40 * 100)
    brevity = {1: 80, 2: 100, 3: 100, 4: 85, 5: 65}.get(len(words), 45)
    total = 0.35 * contrast + 0.15 * min(100, sat * 1.5) + 0.25 * focal + 0.25 * brevity
    return {"contrast": round(contrast, 1), "saturation": round(sat, 1), "focal_detail": round(focal, 1),
            "text_brevity": brevity, "total": round(total, 1)}


def vision_rank(paths: list[Path], title: str) -> dict | None:
    """Optional: ask Claude (vision) to rank the concepts for CTR and clarity."""
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    try:
        import anthropic
        content: list[dict[str, Any]] = []
        for i, p in enumerate(paths):
            buf = io.BytesIO()
            Image.open(p).resize((640, 360)).save(buf, "JPEG", quality=85)
            content += [{"type": "text", "text": f"Thumbnail {i}:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(buf.getvalue()).decode()}}]
        content.append({"type": "text", "text":
                        f"Video title: {title}. Rate each thumbnail 0-100 for likely CTR among US viewers "
                        "18-45 of premium history documentaries: clarity at small size, emotional focal "
                        'point, contrast, readable minimal text, not misleading. JSON only: '
                        '[{"i": int, "score": int, "reason": str}]'})
        msg = anthropic.Anthropic(api_key=s.anthropic_api_key).messages.create(
            model=s.anthropic_model, max_tokens=16000, output_config={"effort": "low"},
            messages=[{"role": "user", "content": content}])
        return {r["i"]: r for r in extract_json("".join(b.text for b in msg.content if b.type == "text"))}
    except Exception as e:  # noqa: BLE001
        log.warning("vision ranking failed: %s", e)
        return None


def generate(bg_paths: list[str], text: str, title: str, out_dir: Path) -> dict[str, Any]:
    words = text.split()[:5] or title.split()[:3]
    out_dir.mkdir(parents=True, exist_ok=True)
    if not bg_paths:  # no scene visuals: use a generated plate
        from ..video.visuals import procedural
        p = out_dir / "bg_fallback.jpg"
        procedural({}, p, (W, H), title, 0)
        bg_paths = [str(p)]
    concepts, paths = [], []
    for i, (name, fn) in enumerate(CONCEPTS.items()):
        bg = Image.open(bg_paths[i % len(bg_paths)])
        im = fn(bg, words)
        p = out_dir / f"thumb_{i}.jpg"
        im.save(p, "JPEG", quality=92)
        paths.append(p)
        concepts.append({"index": i, "concept": name, "path": str(p), "text": " ".join(words),
                         "scores": heuristic_score(im, words)})
    vis = vision_rank(paths, title)
    for c in concepts:
        if vis and c["index"] in vis:
            c["scores"]["vision"] = vis[c["index"]]["score"]
            c["scores"]["vision_reason"] = vis[c["index"]].get("reason", "")
            c["scores"]["final"] = round(0.4 * c["scores"]["total"] + 0.6 * vis[c["index"]]["score"], 1)
        else:
            c["scores"]["final"] = c["scores"]["total"]
    best = max(concepts, key=lambda c: c["scores"]["final"])
    return {"concepts": concepts, "recommended": best["index"], "selected": best["index"],
            "scoring": "heuristic + Claude vision" if vis else "heuristic only (no ANTHROPIC_API_KEY)"}
