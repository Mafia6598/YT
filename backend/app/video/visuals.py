"""Scene visuals with a license ledger.

Provider order comes from IMAGE_PROVIDERS. Only assets whose license is verified as
reusable are accepted; every accepted asset is recorded with source, author and license
so attribution can go into the description."""
from __future__ import annotations

import base64
import hashlib
import logging
import math
import random
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import get_settings
from ..util import ProviderUnavailable, get_json, http, retry

log = logging.getLogger(__name__)
FONTS = Path("/usr/share/fonts/truetype")
ALLOWED_LICENSES = re.compile(
    r"(public domain|pd|pdm( 1\.0)?|cc0( 1\.0)?|cc[- ]by(-sa)?([- ]\d\.\d)?( [a-z]{2,3})?|no restrictions)", re.I)
FORBIDDEN_LICENSE = re.compile(r"(\b|-)(nc|nd)(\b|-)|non[- ]?commercial|no[- ]?deriv|fair use", re.I)


def license_ok(lic: str) -> bool:
    lic = re.sub(r"\s+", " ", lic).strip()
    return bool(lic) and not FORBIDDEN_LICENSE.search(lic) and bool(ALLOWED_LICENSES.fullmatch(lic))


def font(size: int, bold: bool = True, serif: bool = False) -> ImageFont.FreeTypeFont:
    cands = (["dejavu/DejaVuSerif-Bold.ttf", "liberation/LiberationSerif-Bold.ttf"] if serif else
             ["dejavu/DejaVuSans-Bold.ttf", "liberation/LiberationSans-Bold.ttf"]) if bold else \
        ["dejavu/DejaVuSans.ttf", "liberation/LiberationSans-Regular.ttf"]
    for c in cands:
        p = FONTS / c
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


# ------------------------------------------------------------------ providers
def wikimedia(query: str, out: Path) -> dict[str, Any]:
    try:
        d = get_json("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6, "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|extmetadata|size",
            "iiurlwidth": 2400})
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"Wikimedia Commons unreachable: {e}") from e
    pages = sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = re.sub(r"<.*?>", "", meta.get("LicenseShortName", {}).get("value", "")).strip()
        if not license_ok(lic) or ii.get("width", 0) < 900:
            continue
        url = ii.get("thumburl") or ii.get("url")
        _download(url, out)
        author = re.sub(r"<.*?>", "", meta.get("Artist", {}).get("value", "Unknown")).strip()[:120]
        return {"provider": "wikimedia", "source_url": ii.get("descriptionurl"), "license": lic,
                "author": author, "attribution_required": not lic.lower().startswith(("public", "pd", "cc0")),
                "license_verified": True}
    raise ProviderUnavailable(f"no reusable-licensed Commons image for '{query}'")


def pexels(query: str, out: Path, orientation: str = "landscape") -> dict[str, Any]:
    key = get_settings().pexels_api_key
    if not key:
        raise ProviderUnavailable("PEXELS_API_KEY not set")
    try:
        d = get_json("https://api.pexels.com/v1/search", {"query": query, "per_page": 5,
                                                           "orientation": orientation},
                     headers={"Authorization": key})
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"Pexels error: {e}") from e
    photos = d.get("photos", [])
    if not photos:
        raise ProviderUnavailable(f"no Pexels result for '{query}'")
    ph = photos[0]
    _download(ph["src"]["large2x"], out)
    return {"provider": "pexels", "source_url": ph["url"], "license": "Pexels License",
            "author": ph.get("photographer", ""), "attribution_required": False, "license_verified": True}


def openai_image(prompt: str, out: Path, orientation: str = "landscape") -> dict[str, Any]:
    s = get_settings()
    if not s.openai_api_key:
        raise ProviderUnavailable("OPENAI_API_KEY not set (AI image generation)")

    def _call():
        r = httpx.post("https://api.openai.com/v1/images/generations", timeout=240,
                       headers={"Authorization": f"Bearer {s.openai_api_key}"},
                       json={"model": s.openai_image_model, "n": 1,
                             "size": "1536x1024" if orientation == "landscape" else "1024x1536",
                             "prompt": prompt + ". Cinematic documentary still, photorealistic film "
                                                "grain, dramatic lighting, no text, no logos, "
                                                "no likeness of real identifiable people."})
        r.raise_for_status()
        return r.json()["data"][0]["b64_json"]
    out.write_bytes(base64.b64decode(retry(_call)))
    return {"provider": "openai-image", "source_url": "", "license": "AI-generated (OpenAI terms)",
            "author": "AI-generated", "attribution_required": False, "license_verified": True,
            "synthetic": True}


def _download(url: str, out: Path) -> None:
    def _do():
        with http() as c:
            r = c.get(url)
            r.raise_for_status()
            out.write_bytes(r.content)
    retry(_do)
    Image.open(out).verify()


# ------------------------------------------------------------------ procedural (offline)
PALETTES = [((12, 14, 20), (120, 90, 50)), ((8, 10, 14), (60, 90, 120)), ((20, 8, 8), (150, 40, 30)),
            ((14, 14, 12), (170, 150, 110)), ((6, 12, 10), (60, 110, 90))]


def procedural(scene: dict, out: Path, size: tuple[int, int], title: str, idx: int) -> dict[str, Any]:
    """Copyright-free generated plate. Five distinct styles rotate so consecutive scenes differ."""
    w, h = int(size[0] * 1.25), int(size[1] * 1.25)
    seed = int(hashlib.md5(f"{title}{idx}".encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    style = idx % 5
    dark, accent = PALETTES[(idx + seed) % len(PALETTES)]
    img = Image.new("RGB", (w, h), dark)
    dr = ImageDraw.Draw(img)
    cx, cy = rnd.uniform(0.3, 0.7) * w, rnd.uniform(0.3, 0.6) * h
    # radial light
    for r in range(int(max(w, h) * 0.9), 0, -12):
        a = 1 - r / (max(w, h) * 0.9)
        col = tuple(int(dark[c] + (accent[c] - dark[c]) * a ** 2.2 * 0.85) for c in range(3))
        dr.ellipse([cx - r, cy - r * 0.8, cx + r, cy + r * 0.8], fill=col)
    if style == 0:  # light rays + dust
        rays = Image.new("L", (w, h), 0)
        rd = ImageDraw.Draw(rays)
        for _ in range(9):
            x = rnd.uniform(0, w)
            rd.polygon([(x, 0), (x + rnd.uniform(60, 200), 0), (x - w * 0.3, h), (x - w * 0.42, h)],
                       fill=rnd.randint(18, 45))
        img.paste(Image.new("RGB", (w, h), accent), (0, 0), rays.filter(ImageFilter.GaussianBlur(40)))
        for _ in range(500):
            x, y, s = rnd.uniform(0, w), rnd.uniform(0, h), rnd.uniform(1, 3.5)
            dr.ellipse([x, y, x + s, y + s], fill=tuple(min(255, c + 80) for c in accent))
    elif style == 1:  # abstract map grid with route
        for gx in range(0, w, 90):
            dr.line([(gx, 0), (gx, h)], fill=tuple(c + 18 for c in dark), width=1)
        for gy in range(0, h, 90):
            dr.line([(0, gy), (w, gy)], fill=tuple(c + 18 for c in dark), width=1)
        for _ in range(6):  # coast-like contours
            pts, x, y = [], rnd.uniform(0, w), rnd.uniform(0, h)
            for _ in range(60):
                x += rnd.uniform(-40, 40)
                y += rnd.uniform(-40, 40)
                pts.append((x, y))
            dr.line(pts, fill=tuple(min(255, c + 40) for c in accent), width=3)
        pts = [(rnd.uniform(0.1, 0.9) * w, rnd.uniform(0.2, 0.8) * h) for _ in range(4)]
        dr.line(pts, fill=(200, 40, 30), width=7)
        for p in pts:
            dr.ellipse([p[0] - 14, p[1] - 14, p[0] + 14, p[1] + 14], outline=(230, 60, 40), width=5)
    elif style == 2:  # aged document card
        paper = Image.new("RGB", (int(w * 0.62), int(h * 0.7)), (196, 178, 140))
        pd = ImageDraw.Draw(paper)
        for _ in range(4000):
            x, y = rnd.uniform(0, paper.width), rnd.uniform(0, paper.height)
            pd.point((x, y), fill=(rnd.randint(150, 185), rnd.randint(130, 160), rnd.randint(90, 120)))
        f = font(int(paper.height * 0.05), serif=True)
        pd.text((60, 60), "CLASSIFIED FILE" if idx % 2 else "RECORD", font=f, fill=(120, 20, 20))
        for li in range(12):
            y = 160 + li * paper.height * 0.055
            pd.rectangle([60, y, 60 + rnd.uniform(0.4, 0.85) * paper.width, y + 14], fill=(60, 50, 40))
        paper = paper.rotate(rnd.uniform(-6, 6), expand=True, fillcolor=dark)
        img.paste(paper, (int(w * 0.19), int(h * 0.15)))
    elif style == 3:  # silhouette horizon
        base = h * rnd.uniform(0.62, 0.75)
        pts = [(0, h)]
        x = 0
        while x < w:
            bw = rnd.uniform(30, 160)
            top = base - rnd.uniform(0, h * 0.25) * (rnd.random() < 0.6)
            pts += [(x, top), (x + bw, top)]
            x += bw
        pts += [(w, h)]
        dr.polygon(pts, fill=(4, 4, 6))
    else:  # bokeh / fire embers
        for _ in range(60):
            x, y, r = rnd.uniform(0, w), rnd.uniform(0, h), rnd.uniform(20, 110)
            layer = Image.new("L", (w, h), 0)
            ImageDraw.Draw(layer).ellipse([x - r, y - r, x + r, y + r], fill=rnd.randint(25, 70))
            img.paste(Image.new("RGB", (w, h), tuple(min(255, c + 60) for c in accent)), (0, 0),
                      layer.filter(ImageFilter.GaussianBlur(r / 3)))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    img.save(out, quality=92)
    return {"provider": "procedural", "source_url": "", "license": "Generated by this app (no third-party rights)",
            "author": "History Studio", "attribution_required": False, "license_verified": True}


def get_visual(scene: dict, out: Path, size: tuple[int, int], title: str, providers: list[str] | None = None
               ) -> dict[str, Any]:
    orient = "landscape" if size[0] > size[1] else "portrait"
    order = providers or [p.strip() for p in get_settings().image_providers.split(",")]
    tried = []
    for p in order:
        try:
            if p == "wikimedia" and scene.get("image_query"):
                rec = wikimedia(scene["image_query"], out)
            elif p == "pexels" and scene.get("image_query"):
                rec = pexels(scene["image_query"], out, orient)
            elif p == "openai" and scene.get("image_prompt"):
                rec = openai_image(scene["image_prompt"], out, orient)
            elif p == "procedural":
                rec = procedural(scene, out, size, title, scene.get("id", 0))
            else:
                continue
            return {**rec, "scene_id": scene.get("id"), "path": str(out), "tried": tried}
        except ProviderUnavailable as e:
            tried.append(f"{p}: {e}")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{p}: {type(e).__name__}: {e}")
    rec = procedural(scene, out, size, title, scene.get("id", 0))
    return {**rec, "scene_id": scene.get("id"), "path": str(out), "tried": tried}
