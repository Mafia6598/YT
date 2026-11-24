"""Shared helpers: HTTP with retries, ffmpeg wrappers, JSON extraction."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import httpx

from .config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T")


class ProviderUnavailable(RuntimeError):
    """Raised when an integration is not configured (missing key) or unreachable."""


def retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.5,
          retry_on: tuple = (httpx.TransportError, httpx.HTTPStatusError, TimeoutError)) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:  # type: ignore[misc]
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 \
                    and e.response.status_code != 429:
                raise
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    assert last is not None
    raise last


def http() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True,
                        headers={"User-Agent": get_settings().http_user_agent})


def get_json(url: str, params: dict | None = None, headers: dict | None = None) -> Any:
    def _do():
        with http() as c:
            r = c.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()
    return retry(_do)


def run(cmd: list[str], timeout: int = 1800, cwd: str | Path | None = None) -> str:
    log.debug("run: %s", " ".join(map(str, cmd)))
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                       cwd=str(cwd) if cwd else None)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-1500:]}")
    return p.stdout


def ffprobe_duration(path: str | Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of an LLM reply."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return dec.raw_decode(text, i)[0]  # first complete JSON value; ignores trailing text
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON in reply")


def slug(s: str, n: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "item"


def video_dir(video_id: int) -> Path:
    d = get_settings().media_dir / f"video_{video_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def media_url(path: str | Path) -> str:
    p = Path(path).resolve()
    rel = p.relative_to(get_settings().media_dir.resolve())
    return f"/media/{rel.as_posix()}"
