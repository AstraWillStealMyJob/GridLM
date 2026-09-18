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

if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
    )