from __future__ import annotations

import json
import os
from typing import Any

from groq import Groq


_DEFAULT_TIMEOUT = 20.0  # seconds

# Primary = 20B (fast, cheap). Fallback = 120B if 20B is unavailable.
PRIMARY_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
FALLBACK_MODEL = os.environ.get("GROQ_FALLBACK_MODEL", "openai/gpt-oss-120b")


class GroqJSONError(RuntimeError):
    """Raised when Groq returns a non-JSON or empty response."""


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return Groq(api_key=api_key, timeout=_DEFAULT_TIMEOUT)


def _call_one_model(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    client = _client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        response_format={"type": "json_object"},
        # GPT-OSS family: suppress reasoning tokens for clean JSON output.
        # Do NOT set reasoning_format here -- it's Qwen3-only and will error.
        include_reasoning=False,
        reasoning_effort="low",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise GroqJSONError(f"empty response from {model}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise GroqJSONError(f"{model} returned non-JSON: {exc}") from exc


def call_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 256,
) -> dict[str, Any]:
    """
    Call Groq with JSON-mode response format.

    Tries PRIMARY_MODEL first, then FALLBACK_MODEL on failure.
    Raises GroqJSONError if both fail.
    """
    last_error: Exception | None = None
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            return _call_one_model(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise GroqJSONError(f"all models failed: {last_error}")