"""LLM-based operator-note interpreter for GridWise."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Any

from .groq_client import GroqJSONError, call_json

logger = logging.getLogger(__name__)


# --- interpretation cache ---------------------------------------------------

_CACHE_MAX = 256
_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(operator_notes: list[str], battery: dict) -> str:
    payload = json.dumps(
        {
            "notes": list(operator_notes),
            "capacity_kwh": float(battery["capacity_kwh"]),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        if key not in _cache:
            return None
        _cache.move_to_end(key)
        return _cache[key]


def _cache_put(key: str, value: dict) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cache_stats() -> dict:
    with _cache_lock:
        return {"size": len(_cache), "max": _CACHE_MAX}


def _looks_cacheable(raw: dict) -> bool:
    interp = raw.get("directive_interpretation")
    return isinstance(interp, list) and len(interp) > 0


# --- prompt construction ----------------------------------------------------

# IMPORTANT: The word "json" MUST appear in this string.
# Groq rejects response_format={"type": "json_object"} otherwise.
SYSTEM_PROMPT = """\
You convert campus energy operator notes into strict JSON directives.

Return ONLY a JSON object of this exact shape:
{
  "directive_interpretation": [
    {
      "note_index": <int>,
      "applies": <bool>,
      "directive_type": <string>,
      "structured_adjustment": <object|null>,
      "explanation": <string>
    }
  ]
}

Directive types and their structured_adjustment shape:
- solar_reduction:          {"hours": [int], "factor": 0.0-1.0}
- minimum_battery_reserve:  {"hours": [int], "minimum_energy_kwh": float}
- no_charge_window:         {"hours": [int]}
- no_discharge_window:      {"hours": [int]}
- max_grid_window:          {"hours": [int], "max_grid_kwh": float}
- no_op:                    null

Rules:
1. Return exactly one entry per operator note, in note_index order 0..N-1.
2. "hours" is a list of unique integers 0..23 in ascending order.
3. Time windows are start-inclusive, end-exclusive.
   "1 PM to 3 PM" -> [13, 14]. "6 PM until 9 PM" -> [18, 19, 20].
4. no_op: applies=false, structured_adjustment=null.
   Every other directive: applies=true.
5. "X% reduction" -> factor = 1 - X/100.
   "X% of forecast" -> factor = X/100.
6. "X% of capacity" reserve -> absolute kWh using the battery capacity given.
7. If a note is unrelated to today's energy schedule, mark it no_op.
8. Do not include any prose outside the JSON object.
"""


def _build_user_prompt(operator_notes, hours, battery) -> str:
    """Only send what the LLM needs: notes + battery capacity."""
    battery_view = {"capacity_kwh": battery["capacity_kwh"]}
    return (
        "BATTERY:\n"
        + json.dumps(battery_view, indent=2)
        + "\n\nOPERATOR NOTES:\n"
        + json.dumps(operator_notes, indent=2)
    )


def _coerce_to_raw_interpretation(result: Any) -> dict:
    if isinstance(result, dict) and "directive_interpretation" in result:
        return result
    if isinstance(result, list):
        return {"directive_interpretation": result}
    raise GroqJSONError(
        f"unexpected interpretation shape: {type(result).__name__}"
    )


# --- public API -------------------------------------------------------------

def interpret_notes(
    operator_notes: list[str],
    hours: list[dict],
    battery: dict,
) -> dict:
    key = _cache_key(operator_notes, battery)
    hit = _cache_get(key)
    if hit is not None:
        logger.info("interpret_notes: cache hit key=%s", key[:8])
        return hit

    user_prompt = _build_user_prompt(operator_notes, hours, battery)
    raw = call_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=1024,
    )
    result = _coerce_to_raw_interpretation(raw)

    if _looks_cacheable(result):
        _cache_put(key, result)
    return result


def interpret_notes_with_retry(
    operator_notes: list[str],
    hours: list[dict],
    battery: dict,
    guardrail_error: str,
) -> dict:
    base_prompt = _build_user_prompt(operator_notes, hours, battery)
    user_prompt = (
        base_prompt
        + "\n\nYour previous JSON failed deterministic validation with:\n"
        + guardrail_error
        + "\n\nReturn corrected JSON only."
    )
    raw = call_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=768,
    )
    return _coerce_to_raw_interpretation(raw)