"""Pydantic schemas for the GridWise /optimize-energy contract."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

BatteryAction = Literal["charge", "discharge", "idle"]


# --- request ---------------------------------------------------------------

class HourEntry(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0)
    solar_kwh: float = Field(ge=0)
    tariff_bdt_per_kwh: float = Field(ge=0)


class BatterySpec(BaseModel):
    capacity_kwh: float = Field(gt=0)
    initial_energy_kwh: float = Field(ge=0)
    minimum_energy_kwh: float = Field(ge=0)
    max_charge_kwh_per_hour: float = Field(ge=0)
    max_discharge_kwh_per_hour: float = Field(ge=0)


class OptimizeRequest(BaseModel):
    scenario_id: str = Field(min_length=1)
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourEntry]
    battery: BatterySpec

    @field_validator("operator_notes")
    @classmethod
    def _notes_nonempty(cls, v: list[str]) -> list[str]:
        if any(not n or not n.strip() for n in v):
            raise ValueError("operator_notes must be non-empty strings")
        return v

    @field_validator("hours")
    @classmethod
    def _hours_cover_24(cls, v: list[HourEntry]) -> list[HourEntry]:
        if len(v) != 24:
            raise ValueError("hours must contain exactly 24 entries")
        seen = sorted(h.hour for h in v)
        if seen != list(range(24)):
            raise ValueError("hours must be unique integers 0..23")
        return v


# --- directive interpretation (LLM output + response) ----------------------

class SolarReductionAdjustment(BaseModel):
    hours: list[int]
    factor: float = Field(ge=0.0, le=1.0)


class MinimumBatteryReserveAdjustment(BaseModel):
    hours: list[int]
    minimum_energy_kwh: float = Field(ge=0)


class HoursOnlyAdjustment(BaseModel):
    hours: list[int]


class MaxGridWindowAdjustment(BaseModel):
    hours: list[int]
    max_grid_kwh: float = Field(ge=0)


class DirectiveInterpretation(BaseModel):
    """
    A single per-note interpretation entry.

    structured_adjustment is intentionally typed as a union so Pydantic
    can round-trip the object without losing its exact shape, while the
    guardrails still do the authoritative validation.
    """
    note_index: int = Field(ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[
        Union[
            SolarReductionAdjustment,
            MinimumBatteryReserveAdjustment,
            HoursOnlyAdjustment,
            MaxGridWindowAdjustment,
            dict,
        ]
    ] = None
    explanation: str = ""


# --- response --------------------------------------------------------------

class HourlyPlanEntry(BaseModel):
    hour: int = Field(ge=0, le=23)
    grid_kwh: float = Field(ge=0)
    solar_used_kwh: float = Field(ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(ge=0)
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str