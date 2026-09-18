"""24-hour energy optimizer using linear programming."""
from __future__ import annotations

import numpy as np
from scipy.optimize import linprog

from .guardrails import Directive


TOL = 1e-6
HORIZON = 24
# Variable layout per hour h: [grid, solar_used, charge, discharge, E_after]
NVARS = HORIZON * 5


def _v(h: int, k: int) -> int:
    return 5 * h + k


def _adj(d: Directive) -> dict:
    """
    Narrow Optional[dict] to dict.

    Guardrails guarantee structured_adjustment is non-None for every
    non-no_op directive; this accessor makes that contract explicit to
    the type checker and enforces it at runtime.
    """
    if d.structured_adjustment is None:
        raise RuntimeError(
            f"directive {d.directive_type} missing structured_adjustment"
        )
    return d.structured_adjustment


def build_effective_solar(hours: list[dict], directives: list[Directive]) -> list[float]:
    """Apply solar_reduction directives to the base solar profile."""
    eff = [float(h["solar_kwh"]) for h in hours]
    for d in directives:
        if d.directive_type == "solar_reduction":
            adj = _adj(d)
            factor = float(adj["factor"])
            for h in adj["hours"]:
                eff[h] *= factor
    return eff


def build_min_reserve(
    hours: list[dict],
    battery: dict,
    directives: list[Directive],
) -> list[float]:
    """Apply minimum_battery_reserve directives to the base reserve."""
    base = float(battery["minimum_energy_kwh"])
    reserves = [base] * HORIZON
    for d in directives:
        if d.directive_type == "minimum_battery_reserve":
            adj = _adj(d)
            r = float(adj["minimum_energy_kwh"])
            for h in adj["hours"]:
                reserves[h] = max(reserves[h], r)
    return reserves


def optimize(
    hours: list[dict],
    battery: dict,
    directives: list[Directive],
) -> dict:
    """
    Solve the 24-hour battery/solar/grid scheduling LP.

    Returns a dict with hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh.
    Raises RuntimeError if the LP is infeasible.
    """
    demand = [float(h["demand_kwh"]) for h in hours]
    tariff = [float(h["tariff_bdt_per_kwh"]) for h in hours]
    eff_solar = build_effective_solar(hours, directives)
    min_reserve = build_min_reserve(hours, battery, directives)

    cap = float(battery["capacity_kwh"])
    init = float(battery["initial_energy_kwh"])
    max_ch = float(battery["max_charge_kwh_per_hour"])
    max_dis = float(battery["max_discharge_kwh_per_hour"])

    # Per-hour upper bounds, overridden by directives.
    charge_cap = [max_ch] * HORIZON
    discharge_cap = [max_dis] * HORIZON
    grid_cap: list[float | None] = [None] * HORIZON

    for d in directives:
        if d.directive_type == "no_charge_window":
            adj = _adj(d)
            for h in adj["hours"]:
                charge_cap[h] = 0.0
        elif d.directive_type == "no_discharge_window":
            adj = _adj(d)
            for h in adj["hours"]:
                discharge_cap[h] = 0.0
        elif d.directive_type == "max_grid_window":
            adj = _adj(d)
            cap_g = float(adj["max_grid_kwh"])
            for h in adj["hours"]:
                existing = grid_cap[h]
                grid_cap[h] = cap_g if existing is None else min(existing, cap_g)

    bounds = []
    for h in range(HORIZON):
        bounds.append((0.0, grid_cap[h]))           # grid_kwh
        bounds.append((0.0, eff_solar[h]))           # solar_used_kwh
        bounds.append((0.0, charge_cap[h]))          # charge_kwh
        bounds.append((0.0, discharge_cap[h]))       # discharge_kwh
        bounds.append((min_reserve[h], cap))         # battery_energy_after

    A_rows: list[np.ndarray] = []
    b_rows: list[float] = []

    # Energy balance: grid + solar_used + discharge - charge = demand
    for h in range(HORIZON):
        row = np.zeros(NVARS)
        row[_v(h, 0)] = 1.0    # grid
        row[_v(h, 1)] = 1.0    # solar_used
        row[_v(h, 2)] = -1.0   # charge
        row[_v(h, 3)] = 1.0    # discharge
        A_rows.append(row)
        b_rows.append(demand[h])

    # Battery state: E_after[h] - E_after[h-1] - charge[h] + discharge[h] = 0
    for h in range(HORIZON):
        row = np.zeros(NVARS)
        row[_v(h, 4)] = 1.0
        row[_v(h, 2)] = -1.0
        row[_v(h, 3)] = 1.0
        if h == 0:
            A_rows.append(row)
            b_rows.append(init)
        else:
            row[_v(h - 1, 4)] = -1.0
            A_rows.append(row)
            b_rows.append(0.0)

    # End-of-day neutrality
    row = np.zeros(NVARS)
    row[_v(HORIZON - 1, 4)] = 1.0
    A_rows.append(row)
    b_rows.append(init)

    A_eq = np.array(A_rows)
    b_eq = np.array(b_rows)

    # Objective: minimize sum(tariff[h] * grid[h])
    c = np.zeros(NVARS)
    for h in range(HORIZON):
        c[_v(h, 0)] = tariff[h]

    res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"optimizer infeasible: {res.message}")

    x = res.x

    plan: list[dict] = []
    for h in range(HORIZON):
        grid = max(0.0, x[_v(h, 0)])
        solar_used = max(0.0, x[_v(h, 1)])
        charge = max(0.0, x[_v(h, 2)])
        discharge = max(0.0, x[_v(h, 3)])
        e_after = float(x[_v(h, 4)])

        # Net simultaneous charge/discharge for action consistency.
        net = charge - discharge
        if net > TOL:
            action, b_kwh = "charge", net
        elif net < -TOL:
            action, b_kwh = "discharge", -net
        else:
            action, b_kwh = "idle", 0.0

        plan.append({
            "hour": h,
            "grid_kwh": grid,
            "solar_used_kwh": solar_used,
            "battery_action": action,
            "battery_kwh": b_kwh,
            "battery_energy_after_kwh": e_after,
        })

    total_grid = sum(p["grid_kwh"] for p in plan)
    total_cost = sum(p["grid_kwh"] * tariff[p["hour"]] for p in plan)
    peak_grid = max(p["grid_kwh"] for p in plan)

    return {
        "hourly_plan": plan,
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak_grid,
    }