"""LLM provider abstraction.

Single entry point: `complete(system, prompt, max_tokens)` → text.

Provider selected by env var LLM_PROVIDER:
  - "anthropic" (default) — uses ANTHROPIC_API_KEY + ANTHROPIC_MODEL
  - "gemini"              — uses GEMINI_API_KEY + GEMINI_MODEL
                            Google AI Studio free tier: 500 req/day,
                            250K tokens/min on gemini-2.5-flash. No card.
                            Get a key at https://aistudio.google.com/apikey

Both providers are called with structured-output hints in the prompt;
JSON extraction lives in analyzer._extract_json.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("llm")


def _provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()


def _anthropic_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def _gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


@lru_cache(maxsize=1)
def _anthropic_client():
    from anthropic import Anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to .env, "
            "or switch to free Gemini with LLM_PROVIDER=gemini + GEMINI_API_KEY."
        )
    return Anthropic(api_key=key)


@lru_cache(maxsize=1)
def _gemini_client():
    from google import genai
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Get a free one at "
            "https://aistudio.google.com/apikey then add to .env."
        )
    return genai.Client(api_key=key)


def complete(*, system: str, prompt: str, max_tokens: int = 800) -> str:
    """Provider-agnostic single-shot text completion. Returns raw text."""
    prov = _provider()
    if prov == "gemini":
        return _gemini_complete(system, prompt, max_tokens)
    return _anthropic_complete(system, prompt, max_tokens)


def _anthropic_complete(system: str, prompt: str, max_tokens: int) -> str:
    client = _anthropic_client()
    resp = client.messages.create(
        model=_anthropic_model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: list[str] = []
    for block in (resp.content or []):
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    return "".join(parts).strip()


def _gemini_complete(system: str, prompt: str, max_tokens: int) -> str:
    from google.genai import types
    client = _gemini_client()
    resp = client.models.generate_content(
        model=_gemini_model(),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    # SDK exposes .text on simple text responses
    text = getattr(resp, "text", None)
    if text:
        return text.strip()
    # Fallback: walk candidates
    try:
        for cand in resp.candidates or []:
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                t = getattr(part, "text", None)
                if t:
                    return t.strip()
    except Exception:
        pass
    return ""


def provider_info() -> dict:
    """Used by dashboard / logs to show which backend is live."""
    prov = _provider()
    if prov == "gemini":
        return {"provider": "gemini", "model": _gemini_model()}
    return {"provider": "anthropic", "model": _anthropic_model()}
