from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


ALLOWED_DIRECTIVES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

# Exact structured_adjustment keys per directive type.
REQUIRED_KEYS: dict[str, set[str]] = {
    "solar_reduction":          {"hours", "factor"},
    "minimum_battery_reserve":  {"hours", "minimum_energy_kwh"},
    "no_charge_window":         {"hours"},
    "no_discharge_window":      {"hours"},
    "max_grid_window":          {"hours", "max_grid_kwh"},
    "no_op":                    set(),
}


class GuardrailError(ValueError):
    """Raised when LLM output fails deterministic validation."""


@dataclass
class Directive:
    note_index: int
    applies: bool
    directive_type: str
    structured_adjustment: Optional[dict]
    explanation: str

    def to_dict(self) -> dict:
        return {
            "note_index": self.note_index,
            "applies": self.applies,
            "directive_type": self.directive_type,
            "structured_adjustment": self.structured_adjustment,
            "explanation": self.explanation,
        }


# --- internal helpers -------------------------------------------------------

def _check_hours(value: Any, field: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise GuardrailError(f"{field}: hours must be a non-empty list")
    seen: set[int] = set()
    out: list[int] = []
    for h in value:
        if isinstance(h, bool) or not isinstance(h, int):
            raise GuardrailError(f"{field}: hour must be int, got {h!r}")
        if not 0 <= h <= 23:
            raise GuardrailError(f"{field}: hour out of range: {h}")
        if h in seen:
            raise GuardrailError(f"{field}: duplicate hour {h}")
        seen.add(h)
        out.append(h)
    if out != sorted(out):
        raise GuardrailError(f"{field}: hours must be ascending")
    return out


def _check_number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GuardrailError(f"{field}: must be a number, got {value!r}")
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):
        raise GuardrailError(f"{field}: must be finite")
    if f < minimum:
        raise GuardrailError(f"{field}: must be >= {minimum}")
    return f


# --- public API -------------------------------------------------------------

def validate_interpretation(
    raw: Any,
    operator_notes: list[str],
    battery: dict,
) -> list[Directive]:
    """
    Validate the LLM's raw directive list and return sanitized Directive objects.

    Raises GuardrailError if any check fails.
    """
    if isinstance(raw, dict) and "directive_interpretation" in raw:
        raw = raw["directive_interpretation"]

    if not isinstance(raw, list):
        raise GuardrailError("directive_interpretation must be a list")

    n = len(operator_notes)
    if len(raw) != n:
        raise GuardrailError(
            f"expected {n} directive entries, got {len(raw)}"
        )

    capacity = _check_number(battery["capacity_kwh"], "battery.capacity_kwh")

    out: list[Directive] = []
    for expected_idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise GuardrailError(f"entry {expected_idx}: must be an object")

        if entry.get("note_index") != expected_idx:
            raise GuardrailError(
                f"entry {expected_idx}: note_index must be {expected_idx}"
            )

        applies = entry.get("applies")
        if not isinstance(applies, bool):
            raise GuardrailError(f"entry {expected_idx}: applies must be bool")

        dtype = entry.get("directive_type")
        if dtype not in ALLOWED_DIRECTIVES:
            raise GuardrailError(
                f"entry {expected_idx}: unsupported directive_type {dtype!r}"
            )

        adj = entry.get("structured_adjustment")
        explanation = entry.get("explanation", "")
        if not isinstance(explanation, str):
            raise GuardrailError(f"entry {expected_idx}: explanation must be string")

        # no_op branch
        if dtype == "no_op":
            if applies is not False:
                raise GuardrailError(f"entry {expected_idx}: no_op requires applies=false")
            if adj is not None:
                raise GuardrailError(f"entry {expected_idx}: no_op requires null adjustment")
            out.append(Directive(expected_idx, False, "no_op", None, explanation))
            continue

        if applies is not True:
            raise GuardrailError(f"entry {expected_idx}: {dtype} requires applies=true")
        if not isinstance(adj, dict):
            raise GuardrailError(f"entry {expected_idx}: structured_adjustment must be object")

        required = REQUIRED_KEYS[dtype]
        extra = set(adj) - required
        missing = required - set(adj)
        if extra:
            raise GuardrailError(f"entry {expected_idx}: unexpected keys {sorted(extra)}")
        if missing:
            raise GuardrailError(f"entry {expected_idx}: missing keys {sorted(missing)}")

        hours = _check_hours(adj["hours"], f"entry {expected_idx}.hours")

        if dtype == "solar_reduction":
            factor = _check_number(adj["factor"], f"entry {expected_idx}.factor")
            if factor > 1.0:
                raise GuardrailError(f"entry {expected_idx}: factor must be <= 1.0")
            clean_adj = {"hours": hours, "factor": factor}
        elif dtype == "minimum_battery_reserve":
            reserve = _check_number(
                adj["minimum_energy_kwh"],
                f"entry {expected_idx}.minimum_energy_kwh",
            )
            if reserve > capacity:
                raise GuardrailError(
                    f"entry {expected_idx}: reserve {reserve} exceeds capacity {capacity}"
                )
            clean_adj = {"hours": hours, "minimum_energy_kwh": reserve}
        elif dtype == "max_grid_window":
            cap = _check_number(
                adj["max_grid_kwh"],
                f"entry {expected_idx}.max_grid_kwh",
            )
            clean_adj = {"hours": hours, "max_grid_kwh": cap}
        else:  # no_charge_window, no_discharge_window
            clean_adj = {"hours": hours}

        out.append(Directive(expected_idx, True, dtype, clean_adj, explanation))

    return out