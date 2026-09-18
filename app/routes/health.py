from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Readiness endpoint required by the hackathon."""
    return {"status": "ok"}
