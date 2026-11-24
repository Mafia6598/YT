"""LLM adapters. `get_llm()` returns a client or None (offline mode)."""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

import httpx

from ..config import get_settings
from ..util import ProviderUnavailable, extract_json, retry

log = logging.getLogger(__name__)


class TruncatedOutput(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str, max_tokens: int = 16000) -> str: ...


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, system: str, prompt: str, max_tokens: int = 32000) -> str:
        a = self._anthropic
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=max(max_tokens, 16000), system=system,  # thinking shares max_tokens
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
        )

        def _call() -> str:
            try:  # server-side fallback model on safety refusals (beta)
                with self.client.beta.messages.stream(
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs) as st:
                    msg = st.get_final_message()
            except (TypeError, a.BadRequestError):
                with self.client.messages.stream(**kwargs) as st:
                    msg = st.get_final_message()
            if msg.stop_reason == "refusal":
                raise RuntimeError("model declined the request")
            if msg.stop_reason == "max_tokens":
                raise TruncatedOutput("model output hit max_tokens; not using a truncated answer")
            return "".join(b.text for b in msg.content if b.type == "text")

        def _call_retry_overload() -> str:
            try:
                return _call()
            except a.APIStatusError as e:  # mid-stream SSE errors arrive as a base APIStatusError
                body = e.body if isinstance(e.body, dict) else {}
                etype = (body.get("error") or {}).get("type") or body.get("type")
                if etype in ("overloaded_error", "api_error") or "overloaded" in str(e).lower():
                    raise a.InternalServerError(str(e), response=e.response, body=e.body) from e
                raise

        return retry(_call_retry_overload, attempts=3, retry_on=(a.RateLimitError, a.APIConnectionError,
                                                                 a.InternalServerError))


class OpenAILLM:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model

    def complete(self, system: str, prompt: str, max_tokens: int = 16000) -> str:
        def _call() -> str:
            r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=300,
                           headers={"Authorization": f"Bearer {self.key}"},
                           json={"model": self.model, "max_completion_tokens": max_tokens,
                                 "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": prompt}]})
            r.raise_for_status()
            ch = r.json()["choices"][0]
            if ch.get("finish_reason") == "length":
                raise TruncatedOutput("model output hit max tokens")
            return ch["message"]["content"]
        return retry(_call)


def get_llm(required: bool = False) -> Optional[LLM]:
    s = get_settings()
    p = s.llm_provider.lower()
    if p in ("anthropic", "auto") and s.anthropic_api_key:
        return AnthropicLLM(s.anthropic_api_key, s.anthropic_model)
    if p in ("openai", "auto") and s.openai_api_key:
        return OpenAILLM(s.openai_api_key, s.openai_model)
    if required:
        raise ProviderUnavailable(
            "No LLM configured. Set ANTHROPIC_API_KEY (recommended) or OPENAI_API_KEY in .env")
    return None


def complete_json(system: str, prompt: str, max_tokens: int = 32000) -> Any:
    llm = get_llm(required=True)
    assert llm
    text = llm.complete(system + "\nReply with valid JSON only, no commentary.", prompt, max_tokens)
    try:
        return extract_json(text)
    except Exception:
        fix = llm.complete("You repair JSON. Output only valid JSON.", text[-60000:], max_tokens)
        return extract_json(fix)
