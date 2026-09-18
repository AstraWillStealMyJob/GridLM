from fastapi import FastAPI
from app.routes.health import router as health_router
from app.routes.optimize import router as optimize_router

app = FastAPI(
    title="GridWise Energy Optimizer",
    version="0.1.0",
    description="BUP CSE Fest 2026 preliminary hackathon starter service.",
)

app.include_router(health_router)
app.include_router(optimize_router)
