from fastapi import APIRouter, HTTPException

from app.schemas import OptimizeRequest
from app.services.llm_interpreter import interpret_operator_notes

router = APIRouter(tags=["optimization"])


@router.post("/optimize-energy")
def optimize_energy(request: OptimizeRequest) -> dict:
    """Starter endpoint.

    For now this wires the request into the Groq interpretation stage.
    The deterministic guardrails + 24-hour optimizer are the next modules to add.
    """
    # Validate hour ordering/coverage at the API boundary.
    hours = [item.hour for item in request.hours]
    if hours != list(range(24)):
        raise HTTPException(
            status_code=400,
            detail="hours must contain exactly 24 entries in ascending order from 0 to 23",
        )

    try:
        directive_interpretation = interpret_operator_notes(request.operator_notes)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="LLM interpretation failed") from exc

    return {
        "scenario_id": request.scenario_id,
        "directive_interpretation": directive_interpretation,
        "hourly_plan": [],
        "total_grid_kwh": 0,
        "total_cost_bdt": 0,
        "peak_grid_kwh": 0,
        "plan_summary": "Starter implementation: LLM interpretation is wired; optimizer not implemented yet.",
    }
