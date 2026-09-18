"""Replays the 10 public sample cases against the live service."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


SAMPLES_PATH = (
    Path(__file__).resolve().parent.parent
    / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)

with SAMPLES_PATH.open() as f:
    SAMPLES = json.load(f)["cases"]

TOL = 0.01


# --- interpretation comparison ---------------------------------------------

def _check_interpretation(actual: list[dict], expected: list[dict]) -> None:
    assert len(actual) == len(expected), (len(actual), len(expected))
    for i, (a, e) in enumerate(zip(actual, expected)):
        assert a["note_index"] == e["note_index"], f"entry {i}"
        assert a["applies"] == e["applies"], f"entry {i}"
        assert a["directive_type"] == e["directive_type"], f"entry {i}"

        if e["structured_adjustment"] is None:
            assert a["structured_adjustment"] is None, f"entry {i}"
            continue

        adj_a = a["structured_adjustment"]
        adj_e = e["structured_adjustment"]
        assert adj_a["hours"] == adj_e["hours"], f"entry {i} hours"

        for key, val in adj_e.items():
            if key == "hours":
                continue
            assert abs(adj_a[key] - val) <= TOL, f"entry {i}.{key}"


# --- plan replay ------------------------------------------------------------

def _replay_plan(
    plan: list[dict],
    hours: list[dict],
    battery: dict,
    directives: list[dict],
) -> None:
    assert len(plan) == 24
    by_hour = {p["hour"]: p for p in plan}
    assert set(by_hour) == set(range(24))

    eff_solar = {h["hour"]: float(h["solar_kwh"]) for h in hours}
    min_reserve = {h["hour"]: float(battery["minimum_energy_kwh"]) for h in hours}

    for d in directives:
        if not d["applies"]:
            continue
        adj = d["structured_adjustment"]
        if d["directive_type"] == "solar_reduction":
            for h in adj["hours"]:
                eff_solar[h] *= adj["factor"]
        elif d["directive_type"] == "minimum_battery_reserve":
            for h in adj["hours"]:
                min_reserve[h] = max(min_reserve[h], adj["minimum_energy_kwh"])

    cap = float(battery["capacity_kwh"])
    init = float(battery["initial_energy_kwh"])
    max_ch = float(battery["max_charge_kwh_per_hour"])
    max_dis = float(battery["max_discharge_kwh_per_hour"])

    prev_e = init
    for h in range(24):
        p = by_hour[h]
        demand = float(hours[h]["demand_kwh"])

        # Non-negative magnitudes
        assert p["grid_kwh"] >= -TOL, f"hour {h} grid negative"
        assert p["solar_used_kwh"] >= -TOL, f"hour {h} solar_used negative"
        assert p["battery_kwh"] >= -TOL, f"hour {h} battery_kwh negative"

        b_in = p["battery_kwh"] if p["battery_action"] == "charge" else 0.0
        b_out = p["battery_kwh"] if p["battery_action"] == "discharge" else 0.0

        # Energy balance
        lhs = p["grid_kwh"] + p["solar_used_kwh"] + b_out
        rhs = demand + b_in
        assert abs(lhs - rhs) <= TOL, f"hour {h} energy balance {lhs} != {rhs}"

        # Solar usage
        assert p["solar_used_kwh"] <= eff_solar[h] + TOL, f"hour {h} solar cap"

        # Rate limits and idle semantics
        if p["battery_action"] == "charge":
            assert b_in <= max_ch + TOL, f"hour {h} charge rate"
        elif p["battery_action"] == "discharge":
            assert b_out <= max_dis + TOL, f"hour {h} discharge rate"
        else:
            assert p["battery_action"] == "idle"
            assert abs(p["battery_kwh"]) <= TOL, f"hour {h} idle magnitude"

        # State transition
        e_after = prev_e + b_in - b_out
        assert abs(e_after - p["battery_energy_after_kwh"]) <= TOL, f"hour {h} state"
        assert p["battery_energy_after_kwh"] >= min_reserve[h] - TOL, f"hour {h} reserve"
        assert p["battery_energy_after_kwh"] <= cap + TOL, f"hour {h} capacity"

        prev_e = p["battery_energy_after_kwh"]

    assert abs(prev_e - init) <= TOL, "end-of-day neutrality"

    # Directive application
    for d in directives:
        if not d["applies"]:
            continue
        adj = d["structured_adjustment"]
        for h in adj["hours"]:
            p = by_hour[h]
            if d["directive_type"] == "no_charge_window":
                if p["battery_action"] == "charge":
                    assert p["battery_kwh"] <= TOL, f"hour {h} no_charge violated"
            elif d["directive_type"] == "no_discharge_window":
                if p["battery_action"] == "discharge":
                    assert p["battery_kwh"] <= TOL, f"hour {h} no_discharge violated"
            elif d["directive_type"] == "max_grid_window":
                assert p["grid_kwh"] <= adj["max_grid_kwh"] + TOL, f"hour {h} grid cap"


# --- test cases -------------------------------------------------------------

@pytest.mark.parametrize("case", SAMPLES, ids=[c["id"] for c in SAMPLES])
def test_public_sample(case: dict) -> None:
    client = TestClient(app)

    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["scenario_id"] == case["input"]["scenario_id"]

    _check_interpretation(
        body["directive_interpretation"],
        case["expected_output"]["directive_interpretation"],
    )

    _replay_plan(
        body["hourly_plan"],
        case["input"]["hours"],
        case["input"]["battery"],
        body["directive_interpretation"],
    )

    # Recalculated totals must match the response fields.
    total_grid = sum(p["grid_kwh"] for p in body["hourly_plan"])
    total_cost = sum(
        p["grid_kwh"] * case["input"]["hours"][p["hour"]]["tariff_bdt_per_kwh"]
        for p in body["hourly_plan"]
    )
    peak_grid = max(p["grid_kwh"] for p in body["hourly_plan"])

    assert abs(body["total_grid_kwh"] - total_grid) <= TOL
    assert abs(body["total_cost_bdt"] - total_cost) <= TOL
    assert abs(body["peak_grid_kwh"] - peak_grid) <= TOL