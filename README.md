# GridLM

**GridLM** is an HTTP API for the BUP CSE Fest 2026 Smart Campus Energy Optimization Challenge. It reads a 24-hour campus energy scenario together with short, free-text notes from a campus operator, uses a language model to turn those notes into structured scheduling directives, validates the result deterministically, and then computes a cost-minimal 24-hour grid/solar/battery schedule that respects every applicable directive.

This page documents the service as it is deployed: what it does, how it is structured, how to run it, and how to call it.

---

## Table of contents

- [Overview](#overview)
- [Pipeline](#pipeline)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
  - [Requirements](#requirements)
  - [Environment variables](#environment-variables)
  - [Running locally](#running-locally)
  - [Running with Docker](#running-with-docker)
- [Supported operator directives](#supported-operator-directives)
- [API reference](#api-reference)
  - [GET /health](#get-health)
  - [POST /optimize-energy](#post-optimize-energy)
  - [Error responses](#error-responses)
- [Testing](#testing)
- [Design notes](#design-notes)
- [Limitations](#limitations)
- [Credits](#credits)

---

## Overview

A campus draws power from three sources over a 24-hour horizon: the electricity grid, rooftop solar, and a battery energy storage system. Demand, solar availability, and grid tariff vary hour by hour and are supplied with every request. Operators may also attach one to three natural-language notes describing temporary conditions — a maintenance window, a reserve requirement, a distractor note that changes nothing at all.

GridLM's job is to read those notes, decide which ones matter, convert the relevant ones into one of six supported directive types, and produce a 24-hour schedule that is both valid and low-cost.

> **Note:** The service never trusts the language model's output directly. Every interpretation passes through a deterministic guardrail layer before it can influence the optimizer.

---

## Pipeline

```text
    Client
      │
      ▼
 FastAPI application
      │
      ├── GET /health
      │
      └── POST /optimize-energy
              │
              ▼
        LLM interpreter (Groq)
              │
              ▼
        deterministic guardrails
              │
              ▼
        24-hour LP optimizer (SciPy / HiGHS)
              │
              ▼
        validated JSON response
```

1. **Request validation.** Incoming JSON is checked against a strict schema: exactly 24 hourly entries covering hours 0–23, and one to three non-empty operator notes.
2. **LLM interpretation.** The operator notes and the battery capacity are sent to a language model, which returns a candidate `directive_interpretation` array in strict JSON form.
3. **Guardrail validation.** Deterministic code checks every field of the model's output — directive type, note ordering, hour ranges, numeric bounds, and the required shape of each directive — before anything is trusted. If validation fails, the guardrail error is fed back to the model once for a single self-correction attempt.
4. **Optimization.** Validated directives are translated into linear constraints — tighter solar bounds, charge/discharge restrictions, reserve floors, grid caps — and a 24-hour linear program is solved to minimize total grid electricity cost.
5. **Response assembly.** The final schedule and its totals are returned together with the interpretation that produced it. Totals are always recalculated from the hourly plan itself.

---

## Project structure

```text
app/
├── main.py                     FastAPI application and router registration
├── schemas.py                  Request and response schema definitions
├── routes/
│   ├── health.py                GET /health
│   └── optimize.py              POST /optimize-energy
└── services/
    ├── groq_client.py           LLM API client, JSON-mode calls, model fallback
    ├── llm_interpreter.py       Prompt construction, caching, retry logic
    ├── guardrails.py            Deterministic validation of LLM output
    └── optimizer.py             Linear program formulation and solve
tests/
├── test_health.py
└── test_samples.py
BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
Dockerfile
compose.yaml
requirements.txt
```

---

## Getting started

### Requirements

- Python 3.12+
- A Groq API key
- Docker (optional, for containerized deployment)

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | API key used for LLM calls. |
| `GROQ_MODEL` | No | Primary interpretation model. Defaults to `openai/gpt-oss-20b`. |
| `GROQ_FALLBACK_MODEL` | No | Model used if the primary call fails. Defaults to `openai/gpt-oss-120b`. |
| `PORT` | No | Port the service listens on. Defaults to `8000` locally and `8080` in the Docker image. |

### Running locally

```bash
git clone https://github.com/AstraWillStealMyJob/GridLM
cd GridLM
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GROQ_API_KEY=your_key_here

uvicorn app.main:app --reload
```

Verify the service is ready:

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status": "ok"}
```

Run a request against a public sample case:

```bash
curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @<(python3 -c "import json; print(json.dumps(json.load(open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json'))['cases'][0]['input']))")
```

### Running with Docker

**Option A — build locally**

```bash
docker build -t gridlm .
docker run --rm -p 8080:8080 --env-file .env gridlm
```

**Option B — pull the published fallback image**

A prebuilt image is published to GitHub Container Registry on every push to `main` and is publicly pullable — no registry login required.

```bash
docker pull ghcr.io/astrawillstealmyjob/gridlm:latest
docker run --rm -p 8080:8080 --env-file .env ghcr.io/astrawillstealmyjob/gridlm:latest
```

A version-pinned tag is also available:

```bash
docker pull ghcr.io/astrawillstealmyjob/gridlm:1.0.0
```

Either way, verify readiness the same way:

```bash
curl http://127.0.0.1:8080/health
```

```json
{"status": "ok"}
```

The image runs as a non-root user, binds to `0.0.0.0`, exposes port `8080`, and includes a built-in health check against `/health`.

---

## Supported operator directives

Every operator note resolves to exactly one of the following. A note that does not affect the current schedule resolves to `no_op`.

| Directive | Meaning | `structured_adjustment` |
|---|---|---|
| `solar_reduction` | Usable solar is reduced during specific hours. `factor` is the fraction of solar that remains. | `{"hours": [...], "factor": number}` |
| `minimum_battery_reserve` | Battery energy must stay at or above a level during specific hours. | `{"hours": [...], "minimum_energy_kwh": number}` |
| `no_charge_window` | Battery charging is disabled during specific hours. | `{"hours": [...]}` |
| `no_discharge_window` | Battery discharging is disabled during specific hours. | `{"hours": [...]}` |
| `max_grid_window` | Grid import is capped during specific hours. | `{"hours": [...], "max_grid_kwh": number}` |
| `no_op` | The note does not change today's schedule. | `null` |

> **Note:** Time windows are start-inclusive and end-exclusive. "1 PM to 3 PM" is expressed as hours `[13, 14]`.

---

## API reference

### `GET /health`

Returns service readiness.

**Response — `200 OK`**

```json
{"status": "ok"}
```

### `POST /optimize-energy`

Accepts one 24-hour scenario and returns an interpretation of the operator notes together with the optimized schedule.

**Request body**

| Field | Type | Description |
|---|---|---|
| `scenario_id` | string | Unique scenario identifier. |
| `operator_notes` | array[1–3] of string | Natural-language operator notes. |
| `hours` | array[24] | Hourly demand, solar, and tariff data. |
| `battery` | object | Battery capacity and operating limits. |

```json
{
  "scenario_id": "GRID-101",
  "operator_notes": [
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "Do not charge the battery between 2 PM and 4 PM."
  ],
  "hours": [
    {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
  ],
  "battery": {
    "capacity_kwh": 500,
    "initial_energy_kwh": 200,
    "minimum_energy_kwh": 50,
    "max_charge_kwh_per_hour": 100,
    "max_discharge_kwh_per_hour": 100
  }
}
```

**Response body**

| Field | Type | Description |
|---|---|---|
| `scenario_id` | string | Echoes the request scenario ID. |
| `directive_interpretation` | array | One entry per operator note, in order. |
| `hourly_plan` | array[24] | The final schedule. |
| `total_grid_kwh` | number | Total grid energy purchased. |
| `total_cost_bdt` | number | Total grid electricity cost. |
| `peak_grid_kwh` | number | Maximum hourly grid draw. |
| `plan_summary` | string | Short description of the resulting strategy. |

```json
{
  "scenario_id": "GRID-101",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
      "explanation": "Solar availability drops during the stated maintenance window."
    },
    {
      "note_index": 1,
      "applies": true,
      "directive_type": "no_charge_window",
      "structured_adjustment": {"hours": [14, 15]},
      "explanation": "Charging is disabled for the stated hours."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 12.4,
      "solar_used_kwh": 0.0,
      "battery_action": "discharge",
      "battery_kwh": 5.1,
      "battery_energy_after_kwh": 194.9
    }
  ],
  "total_grid_kwh": 2130.5,
  "total_cost_bdt": 21873.2,
  "peak_grid_kwh": 210.0,
  "plan_summary": "Optimized 24-hour schedule respecting all applicable operator directives."
}
```

### Error responses

| Code | Meaning |
|---|---|
| `400` | Malformed JSON or a request that fails schema validation. |
| `422` | LLM interpretation still fails deterministic guardrails after one correction attempt. |
| `502` | The language model is unreachable or returns unusable output. |
| `500` | The optimizer could not find a feasible schedule. |

> **Warning:** Error responses never include stack traces, prompts, or credential values.

---

## Testing

```bash
pytest tests/test_health.py -v
pytest tests/test_samples.py::test_public_sample_quick -v
pytest tests/test_samples.py::test_stress_100 -v -s
```

`test_public_sample_quick` validates the service against the public sample cases. `test_stress_100` runs 100 randomized sample requests and reports latency percentiles (p50/p95/p99) alongside pass/fail status for every run.

---

## Design notes

- The language model receives only the operator notes and battery capacity — never the full demand, solar, or tariff data — so it cannot influence or invent scheduling numbers directly.
- Every field returned by the model is re-validated and rebuilt from scratch by the guardrail layer before it reaches the optimizer.
- The optimizer is a linear program: directives adjust bounds and constraints, and the solver finds the true minimum-cost schedule under those constraints.
- `total_grid_kwh`, `total_cost_bdt`, and `peak_grid_kwh` are always recomputed from `hourly_plan`, so the reported totals cannot disagree with the schedule itself.
- Interpretation results are cached by operator notes and battery capacity, since interpretation depends only on those two inputs.

---

## Limitations

- Operator-note interpretation depends on the availability of the configured language model provider.
- The interpretation cache is in-memory and per process instance.
- The optimizer assumes feasible input scenarios, as guaranteed by the challenge specification, and returns a controlled error if no feasible schedule exists.

---

## Credits

Built with [FastAPI](https://fastapi.tiangolo.com/), [Groq](https://groq.com/) for LLM inference, and [SciPy](https://scipy.org/)'s HiGHS-backed linear programming solver, for BUP CSE Fest 2026 in association with Poridhi.io.
