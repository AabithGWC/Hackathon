"""
FastAPI Server Entrypoint for NBFC Agentic AI Financial Operations Platform.

Run server:
    python -m uvicorn back_end.main:app --reload --port 8000
    or from inside back_end:
    uvicorn main:app --reload --port 8000
"""
import os
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Ensure root workspace is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

load_dotenv(os.path.join(REPO_ROOT, ".env"))

from back_end.routers import router as api_router

app = FastAPI(
    title="NBFC Financial Operations Agentic AI API",
    description=(
        "Production-ready REST API for the 7-Agent NBFC Financial Operations Platform. "
        "Exposes automated workflows for Reconciliation, Cashflow Forecasting, Cost of Funds Optimization, "
        "Expense Anomaly Detection, NPA Provisioning Forecast, Investor & Board Reporting, and Fund Raising Document Assembly."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for fullstack web application integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust origin list for production deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/", include_in_schema=False)
def root():
    return {
        "message": "NBFC Agentic AI Financial Operations API is running.",
        "documentation": "/docs",
        "health_check": "/api/v1/health",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
