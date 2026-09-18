"""
Stress test: 100 randomly-ordered sample cases with configurable sleeps.

Two test functions:
  test_public_sample_quick  -- original 10-case parametrized suite (fast feedback)
  test_stress_100           -- 100 randomized runs with per-run timing + summary

Run only the stress test:
    pytest tests/test_samples.py::test_stress_100 -v -s

Run only the quick test:
    pytest tests/test_samples.py::test_public_sample_quick -v

Run both:
    pytest tests/test_samples.py -v -s
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.llm_interpreter import cache_stats, clear_cache


# --- configuration ----------------------------------------------------------

SAMPLES_PATH = (
    Path(__file__).resolve().parent.parent
    / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)

with SAMPLES_PATH.open() as f:
    SAMPLES = json.load(f)["cases"]

TOL = 0.01

N_RUNS = 100
SLEEP_BETWEEN = 0.5          # seconds between requests
RANDOM_SEED = 42             # fixed for reproducibility; change to reroll
CLEAR_CACHE_EACH_RUN = False # True -> every run hits the API (stress rate limits)
                              # False -> only first occurrence of each case hits the API


# --- helpers ----------------------------------------------------------------

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

        assert p["grid_kwh"] >= -TOL, f"hour {h} grid negative"
        assert p["solar_used_kwh"] >= -TOL, f"hour {h} solar_used negative"
        assert p["battery_kwh"] >= -TOL, f"hour {h} battery_kwh negative"

        b_in = p["battery_kwh"] if p["battery_action"] == "charge" else 0.0
        b_out = p["battery_kwh"] if p["battery_action"] == "discharge" else 0.0

        lhs = p["grid_kwh"] + p["solar_used_kwh"] + b_out
        rhs = demand + b_in
        assert abs(lhs - rhs) <= TOL, f"hour {h} energy balance {lhs} != {rhs}"

        assert p["solar_used_kwh"] <= eff_solar[h] + TOL, f"hour {h} solar cap"

        if p["battery_action"] == "charge":
            assert b_in <= max_ch + TOL, f"hour {h} charge rate"
        elif p["battery_action"] == "discharge":
            assert b_out <= max_dis + TOL, f"hour {h} discharge rate"
        else:
            assert p["battery_action"] == "idle"
            assert abs(p["battery_kwh"]) <= TOL, f"hour {h} idle magnitude"

        e_after = prev_e + b_in - b_out
        assert abs(e_after - p["battery_energy_after_kwh"]) <= TOL, f"hour {h} state"
        assert p["battery_energy_after_kwh"] >= min_reserve[h] - TOL, f"hour {h} reserve"
        assert p["battery_energy_after_kwh"] <= cap + TOL, f"hour {h} capacity"

        prev_e = p["battery_energy_after_kwh"]

    assert abs(prev_e - init) <= TOL, "end-of-day neutrality"

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


def _validate_response(body: dict, case: dict) -> None:
    """Full validation of one response against its expected output."""
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

    total_grid = sum(p["grid_kwh"] for p in body["hourly_plan"])
    total_cost = sum(
        p["grid_kwh"] * case["input"]["hours"][p["hour"]]["tariff_bdt_per_kwh"]
        for p in body["hourly_plan"]
    )
    peak_grid = max(p["grid_kwh"] for p in body["hourly_plan"])

    assert abs(body["total_grid_kwh"] - total_grid) <= TOL
    assert abs(body["total_cost_bdt"] - total_cost) <= TOL
    assert abs(body["peak_grid_kwh"] - peak_grid) <= TOL


def _build_run_plan() -> list[dict]:
    """Deterministic random ordering of N_RUNS sample draws."""
    rng = random.Random(RANDOM_SEED)
    return [rng.choice(SAMPLES) for _ in range(N_RUNS)]


RUN_PLAN = _build_run_plan()


# --- fast test: original 10-case suite --------------------------------------

@pytest.mark.parametrize("case", SAMPLES, ids=[c["id"] for c in SAMPLES])
def test_public_sample_quick(case: dict) -> None:
    client = TestClient(app)
    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200, resp.text
    _validate_response(resp.json(), case)


# --- stress test: 100 randomized runs ---------------------------------------

def test_stress_100() -> None:
    """
    Fire 100 randomly-ordered sample cases with SLEEP_BETWEEN seconds between.

    Prints per-run durations and a summary that exposes progressive slowdown.
    Fails only if any individual run failed.
    """
    client = TestClient(app)
    clear_cache()

    durations: list[float] = []
    failures: list[tuple[int, str, str, float]] = []
    per_case_first_seen: dict[str, int] = {}

    print()
    print("=" * 78)
    print(f"STRESS TEST: {N_RUNS} runs, sleep={SLEEP_BETWEEN}s between, "
          f"clear_cache_each_run={CLEAR_CACHE_EACH_RUN}")
    print("=" * 78)

    for run_idx, case in enumerate(RUN_PLAN):
        if CLEAR_CACHE_EACH_RUN:
            clear_cache()

        case_id = case["id"]
        first_seen = case_id not in per_case_first_seen
        if first_seen:
            per_case_first_seen[case_id] = run_idx

        t0 = time.perf_counter()
        status = "ok"
        err = ""

        try:
            resp = client.post("/optimize-energy", json=case["input"])
            if resp.status_code != 200:
                status = "fail"
                err = f"HTTP {resp.status_code}: {resp.text[:160]}"
            else:
                _validate_response(resp.json(), case)
        except Exception as e:  # noqa: BLE001
            status = "fail"
            err = f"{type(e).__name__}: {str(e)[:160]}"

        dt = time.perf_counter() - t0
        durations.append(dt)

        if status == "fail":
            failures.append((run_idx, case_id, err, dt))

        marker = " *" if first_seen else "  "
        print(
            f"  [{run_idx + 1:3d}/{N_RUNS}] {case_id:10s}{marker} "
            f"{dt:6.2f}s  {status}"
            + (f"  | {err}" if err else "")
        )

        if run_idx < N_RUNS - 1:
            time.sleep(SLEEP_BETWEEN)

    # --- summary ------------------------------------------------------------
    sorted_d = sorted(durations)
    n = len(sorted_d)
    p50 = sorted_d[n // 2]
    p95 = sorted_d[min(n - 1, int(n * 0.95))]
    p99 = sorted_d[min(n - 1, int(n * 0.99))]

    first_quartile = sorted_d[: n // 4]
    last_quartile = sorted_d[-n // 4:]
    first_mean = sum(first_quartile) / len(first_quartile)
    last_mean = sum(last_quartile) / len(last_quartile)
    drift = last_mean - first_mean

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  runs               {n}")
    print(f"  failures           {len(failures)}")
    print(f"  unique cases seen  {len(per_case_first_seen)}")
    print(f"  cache              {cache_stats()}")
    print()
    print(f"  mean               {sum(durations) / n:.2f}s")
    print(f"  min                {min(durations):.2f}s")
    print(f"  p50                {p50:.2f}s")
    print(f"  p95                {p95:.2f}s")
    print(f"  p99                {p99:.2f}s")
    print(f"  max                {max(durations):.2f}s")
    print(f"  wall (excluding sleeps) {sum(durations):.2f}s")
    print()
    print(f"  first 25% mean     {first_mean:.2f}s")
    print(f"  last  25% mean     {last_mean:.2f}s")
    print(f"  drift              {drift:+.2f}s  "
          f"({'SLOWDOWN DETECTED' if drift > 1.0 else 'stable'})")

    if failures:
        print()
        print("FAILURES:")
        for run_idx, case_id, err, dt in failures:
            print(f"  run {run_idx + 1:3d}  {case_id:10s}  {dt:6.2f}s  {err}")

    print("=" * 78)

    assert not failures, f"{len(failures)}/{n} runs failed"