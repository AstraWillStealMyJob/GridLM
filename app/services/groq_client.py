"""Thin Groq client wrapper with JSON-mode support."""
from __future__ import annotations

import json
import os
from typing import Any

from groq import Groq


_DEFAULT_TIMEOUT = 20.0  # seconds


class GroqJSONError(RuntimeError):
    """Raised when Groq returns a non-JSON or empty response."""


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return Groq(api_key=api_key, timeout=_DEFAULT_TIMEOUT)


def call_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """
    Call Groq with JSON-mode response format and return the parsed dict.

    Raises GroqJSONError on malformed JSON.
    """
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = _client()

    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = resp.choices[0].message.content or ""
    content = content.strip()
    if not content:
        raise GroqJSONError("empty response from Groq")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise GroqJSONError(f"Groq returned non-JSON: {exc}") from exc