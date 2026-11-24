"""Text-to-speech with interchangeable provider adapters.

Every adapter implements `synthesize(text, out_path) -> list[word timings]` and writes an MP3.
Word timings are (start_s, end_s, word); adapters without native timings return [] and the
caller estimates them for subtitles."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

from ..config import get_settings
from ..util import ProviderUnavailable, ffprobe_duration, retry, run

log = logging.getLogger(__name__)
Words = list[tuple[float, float, str]]


class TTS(Protocol):
    name: str

    def synthesize(self, text: str, out: Path) -> Words: ...


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("—", ", ").replace("–", "-").replace("&", "and")
    text = re.sub(r"\bc\.\s*(\d)", r"circa \1", text)
    text = re.sub(r"\bU\.S\.", "U.S.", text)
    return text


class EdgeTTS:
    """Microsoft Edge online voices. No API key. Default: en-US-ChristopherNeural (deep male)."""
    name = "edge"

    def __init__(self):
        s = get_settings()
        self.voice, self.rate, self.pitch = s.edge_voice, s.edge_rate, s.edge_pitch

    def synthesize(self, text: str, out: Path) -> Words:
        import edge_tts

        async def _go() -> Words:
            try:
                com = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch,
                                           boundary="WordBoundary")
            except TypeError:
                com = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            words: Words = []
            with open(out, "wb") as f:
                async for ch in com.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"])
                    elif ch["type"] == "WordBoundary":
                        st = ch["offset"] / 1e7
                        words.append((st, st + ch["duration"] / 1e7, ch["text"]))
            return words

        try:
            words = retry(lambda: asyncio.run(_go()), attempts=3, retry_on=(Exception,))
        except Exception as e:
            raise ProviderUnavailable(f"Edge TTS failed (needs internet access to "
                                      f"speech.platform.bing.com): {e}") from e
        if not out.exists() or out.stat().st_size < 1000:
            raise ProviderUnavailable("Edge TTS returned no audio")
        return words


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self):
        s = get_settings()
        if not (s.elevenlabs_api_key and s.elevenlabs_voice_id):
            raise ProviderUnavailable("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID not set")
        self.s = s

    def synthesize(self, text: str, out: Path) -> Words:
        def _call():
            r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{self.s.elevenlabs_voice_id}",
                           params={"output_format": "mp3_44100_128"}, timeout=180,
                           headers={"xi-api-key": self.s.elevenlabs_api_key},
                           json={"text": text, "model_id": self.s.elevenlabs_model,
                                 "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                                                    "style": 0.35, "use_speaker_boost": True}})
            r.raise_for_status()
            out.write_bytes(r.content)
        retry(_call)
        return []


class OpenAITTS:
    name = "openai"

    def __init__(self):
        s = get_settings()
        if not s.openai_api_key:
            raise ProviderUnavailable("OPENAI_API_KEY not set")
        self.s = s

    def synthesize(self, text: str, out: Path) -> Words:
        def _call():
            r = httpx.post("https://api.openai.com/v1/audio/speech", timeout=180,
                           headers={"Authorization": f"Bearer {self.s.openai_api_key}"},
                           json={"model": self.s.openai_tts_model, "voice": self.s.openai_tts_voice,
                                 "input": text, "response_format": "mp3",
                                 "instructions": "Deep, calm American male documentary narrator. "
                                                 "Measured, dramatic pacing with natural pauses."})
            r.raise_for_status()
            out.write_bytes(r.content)
        retry(_call)
        return []


class EspeakTTS:
    """Offline last-resort voice (robotic). Only for testing the pipeline without internet."""
    name = "espeak"

    def synthesize(self, text: str, out: Path) -> Words:
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not exe:
            raise ProviderUnavailable("espeak-ng not installed")
        with tempfile.NamedTemporaryFile(suffix=".wav") as w:
            run([exe, "-v", "en-us+m3", "-s", "150", "-p", "35", "-w", w.name, text])
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", w.name, "-ar", "44100", "-ac", "1",
                 "-b:a", "128k", str(out)])
        return []


PROVIDERS = {"edge": EdgeTTS, "elevenlabs": ElevenLabsTTS, "openai": OpenAITTS, "espeak": EspeakTTS}


def provider_chain(preferred: str | None = None) -> list[str]:
    s = get_settings()
    chain = [preferred or s.tts_provider] + [p.strip() for p in s.tts_fallbacks.split(",")]
    return list(dict.fromkeys(p for p in chain if p in PROVIDERS))


def synthesize(text: str, out: Path, preferred: str | None = None,
               only: str | None = None) -> tuple[str, Words, list[str]]:
    """Try providers in order (or only `only`). Returns (provider_used, words, errors)."""
    errors = []
    for name in ([only] if only else provider_chain(preferred)):
        try:
            words = PROVIDERS[name]().synthesize(clean_for_speech(text), out)
            return name, words, errors
        except ProviderUnavailable as e:
            errors.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
    raise ProviderUnavailable("All TTS providers failed: " + " | ".join(errors))


def estimate_words(text: str, duration: float) -> Words:
    toks = text.split()
    total = sum(len(t) + 1 for t in toks) or 1
    t, out = 0.0, []
    for tok in toks:
        d = duration * (len(tok) + 1) / total
        out.append((t, t + d, tok))
        t += d
    return out


def narrate_scenes(scenes: list[dict], workdir: Path, preferred: str | None = None,
                   scene_gap: float = 0.35, section_ends: set[int] | None = None) -> dict:
    """Synthesize each scene separately (automatic segmentation), pad with pauses,
    merge into one loudness-normalized MP3 and return timings for subtitles/edit."""
    workdir.mkdir(parents=True, exist_ok=True)
    section_ends = section_ends or set()
    segs, words_all, used, errors = [], [], set(), []
    offset = 0.0
    sticky: str | None = None  # one voice for the whole video: no mid-video provider switch
    for sc in scenes:
        raw = workdir / f"seg_{sc['id']:03d}_raw.mp3"
        # PCM segments: exact durations, single lossy encode at the end (no MP3 padding drift)
        seg = workdir / f"seg_{sc['id']:03d}.wav"
        gap = 0.8 if sc["id"] in section_ends else scene_gap
        text = clean_for_speech(sc.get("narration", ""))
        if not text:
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                 f"anullsrc=r=44100:cl=mono", "-t", f"{gap}", "-c:a", "pcm_s16le", seg])
            segs.append({"scene_id": sc["id"], "path": str(seg), "start": round(offset, 3),
                         "duration": round(ffprobe_duration(seg), 3), "provider": None})
            offset += segs[-1]["duration"]
            continue
        name, words, errs = synthesize(text, raw, preferred, only=sticky)
        sticky = sticky or name
        used.add(name)
        errors += errs
        raw_wav = workdir / f"seg_{sc['id']:03d}_raw.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-ar", "44100", "-ac", "1",
             "-c:a", "pcm_s16le", raw_wav])
        raw_dur = ffprobe_duration(raw_wav)
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw_wav, "-af",
             "silenceremove=start_periods=1:start_threshold=-50dB", "-c:a", "pcm_s16le",
             workdir / "_trim.wav"])
        trimmed = max(0.0, raw_dur - ffprobe_duration(workdir / "_trim.wav"))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", workdir / "_trim.wav", "-af",
             f"apad=pad_dur={gap}", "-c:a", "pcm_s16le", seg])
        raw_wav.unlink(missing_ok=True)
        dur = ffprobe_duration(seg)
        speech = dur - gap
        if words:  # native timings are relative to the untrimmed audio
            words = [(max(0.0, a - trimmed), max(0.0, b - trimmed), w) for a, b, w in words]
        else:
            words = estimate_words(text, speech)
        words_all += [(round(a + offset, 3), round(b + offset, 3), w) for a, b, w in words]
        segs.append({"scene_id": sc["id"], "path": str(seg), "start": round(offset, 3),
                     "duration": round(dur, 3), "provider": name})
        offset += dur
    listfile = workdir / "concat.txt"
    listfile.write_text("".join(f"file '{Path(s['path']).name}'\n" for s in segs))
    (workdir / "_trim.wav").unlink(missing_ok=True)
    merged = workdir / "narration.mp3"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listfile.name,
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-b:a", "192k", merged], cwd=workdir)
    return {"path": str(merged), "duration": round(ffprobe_duration(merged), 2), "segments": segs,
            "words": words_all, "providers": sorted(used), "fallback_errors": errors[:10]}
