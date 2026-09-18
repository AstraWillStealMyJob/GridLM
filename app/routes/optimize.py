"""POST /optimize-energy route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import (
    DirectiveInterpretation,
    OptimizeRequest,
    OptimizeResponse,
)
from app.services.groq_client import GroqJSONError
from app.services.guardrails import GuardrailError, validate_interpretation
from app.services.llm_interpreter import interpret_notes, interpret_notes_with_retry
from app.services.optimizer import optimize


router = APIRouter()


@router.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(req: OptimizeRequest) -> OptimizeResponse:
    hours = [h.model_dump() for h in req.hours]
    battery = req.battery.model_dump()

    # 1. LLM interpretation (untrusted)
    try:
        raw = interpret_notes(req.operator_notes, hours, battery)
    except GroqJSONError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM unavailable: {exc}")

    # 2. Deterministic guardrails
    try:
        directives = validate_interpretation(raw, req.operator_notes, battery)
    except GuardrailError as first_err:
        # One-shot retry with the guardrail message fed back to the model.
        try:
            raw_retry = interpret_notes_with_retry(
                req.operator_notes, hours, battery, str(first_err)
            )
            directives = validate_interpretation(raw_retry, req.operator_notes, battery)
        except (GuardrailError, GroqJSONError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid LLM interpretation: {exc}",
            )

    # 3. Optimization
    try:
        result = optimize(hours, battery, directives)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"optimizer failed: {exc}")

    # 4. Assemble response (Pydantic validates shapes here)
    interpretation = [
        DirectiveInterpretation(
            note_index=d.note_index,
            applies=d.applies,
            directive_type=d.directive_type,  # type: ignore[arg-type]
            structured_adjustment=d.structured_adjustment,
            explanation=d.explanation,
        )
        for d in directives
    ]

    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=interpretation,
        hourly_plan=result["hourly_plan"],
        total_grid_kwh=result["total_grid_kwh"],
        total_cost_bdt=result["total_cost_bdt"],
        peak_grid_kwh=result["peak_grid_kwh"],
        plan_summary=(
            "Optimized 24-hour schedule respecting all applicable "
            "operator directives."
        ),
    )