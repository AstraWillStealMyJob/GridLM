"""LLM-based operator-note interpreter for GridWise."""
from __future__ import annotations

import json
from typing import Any

from .groq_client import GroqJSONError, call_json


SYSTEM_PROMPT = """\
You are a strict energy-directive interpreter for a 24-hour campus microgrid.

You will receive operator notes and must convert EACH note into exactly one
directive entry. Return ONLY JSON of this exact shape:

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

Allowed directive_type values and their exact structured_adjustment shapes:
- "solar_reduction":
    {"hours": [int, ...], "factor": <float 0..1>}
    factor = usable fraction REMAINING. "80% reduction" -> 0.2.
- "minimum_battery_reserve":
    {"hours": [int, ...], "minimum_energy_kwh": <float>}
- "no_charge_window":
    {"hours": [int, ...]}
- "no_discharge_window":
    {"hours": [int, ...]}
- "max_grid_window":
    {"hours": [int, ...], "max_grid_kwh": <float>}
- "no_op":
    null

Hard rules:
1. Return exactly one entry per operator note, in note_index order 0..N-1.
2. "hours" is a list of unique integers 0..23 in ascending order.
3. Time windows are start-inclusive, end-exclusive.
   Example: "1 PM to 3 PM" -> [13, 14].
   Example: "6 PM until 9 PM" -> [18, 19, 20].
4. For no_op: applies=false and structured_adjustment=null.
5. For every non-no_op directive: applies=true.
6. If a note is irrelevant to today's energy schedule, mark it no_op.
   Do NOT invent unsupported directive types.
7. Percentage normalization: "X% reduction" -> factor = 1 - X/100.
   "roughly 25% of forecast" -> factor = 0.25.
8. Relative reserve language ("50% of capacity") must be converted to an
   absolute kWh value using the battery capacity you are given.
9. Do not include any prose outside the JSON object.
"""


def _build_user_prompt(
    operator_notes: list[str],
    hours: list[dict],
    battery: dict,
) -> str:
    """Compose the payload the LLM actually reasons over."""
    battery_view = {
        "capacity_kwh": battery["capacity_kwh"],
        "initial_energy_kwh": battery["initial_energy_kwh"],
        "minimum_energy_kwh": battery["minimum_energy_kwh"],
        "max_charge_kwh_per_hour": battery["max_charge_kwh_per_hour"],
        "max_discharge_kwh_per_hour": battery["max_discharge_kwh_per_hour"],
    }
    hour_view = [
        {
            "hour": h["hour"],
            "demand_kwh": h["demand_kwh"],
            "solar_kwh": h["solar_kwh"],
            "tariff_bdt_per_kwh": h["tariff_bdt_per_kwh"],
        }
        for h in hours
    ]
    return (
        "BATTERY:\n"
        + json.dumps(battery_view, indent=2)
        + "\n\nHOURS:\n"
        + json.dumps(hour_view, indent=2)
        + "\n\nOPERATOR NOTES:\n"
        + json.dumps(operator_notes, indent=2)
    )


def _coerce_to_raw_interpretation(result: Any) -> dict:
    """
    Normalize whatever Groq returns into a dict with a
    'directive_interpretation' list.
    """
    if isinstance(result, dict) and "directive_interpretation" in result:
        return result
    if isinstance(result, list):
        return {"directive_interpretation": result}
    raise GroqJSONError(
        f"unexpected interpretation shape: {type(result).__name__}"
    )


def interpret_notes(
    operator_notes: list[str],
    hours: list[dict],
    battery: dict,
) -> dict:
    """
    Return the raw LLM interpretation as a dict with a
    'directive_interpretation' list.

    The caller (route) is responsible for running guardrails before
    handing the result to the optimizer.
    """
    user_prompt = _build_user_prompt(operator_notes, hours, battery)

    raw = call_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
    )
    return _coerce_to_raw_interpretation(raw)


def interpret_notes_with_retry(
    operator_notes: list[str],
    hours: list[dict],
    battery: dict,
    guardrail_error: str,
) -> dict:
    """
    Second-chance call: replay the previous guardrail error back to the LLM
    so it can correct its own output.
    """
    base_prompt = _build_user_prompt(operator_notes, hours, battery)
    user_prompt = (
        base_prompt
        + "\n\nYour previous response failed deterministic validation with:\n"
        + guardrail_error
        + "\n\nReturn corrected JSON only."
    )
    raw = call_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
    )
    return _coerce_to_raw_interpretation(raw)