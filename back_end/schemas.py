"""
Pydantic schemas for the 7-Agent Financial AI Platform API.
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., example="healthy")
    timestamp: str = Field(..., example="2026-08-09T05:30:00Z")
    version: str = Field(..., example="1.0.0")
    total_agents: int = Field(..., example=7)
    available_playbooks: List[str] = Field(
        ...,
        example=[
            "reconciliation",
            "cashflow_forecasting",
            "cof_optimization",
            "expense_anomaly_detection",
            "npa_provisioning_forecast",
            "investor_reporting",
            "fund_raising",
        ],
    )


class RunAgentRequest(BaseModel):
    model_override: Optional[str] = Field(
        None,
        description="Optional Groq or provider model override (e.g., 'llama-3.3-70b-versatile')",
        example="llama-3.3-70b-versatile",
    )
    async_execution: Optional[bool] = Field(
        False, description="Run in background asynchronously if set to true"
    )


class InvestorReportRunRequest(BaseModel):
    tone: Optional[str] = Field(
        "board_formal",
        description="Tone preset: board_formal | investor_narrative | analyst_detailed | concise_summary",
        example="board_formal",
    )
    audience: Optional[str] = Field(
        "board",
        description="Target audience: board | investor",
        example="board",
    )
    model_override: Optional[str] = Field(None, example="llama-3.3-70b-versatile")


class AgentRunSummary(BaseModel):
    run_id: str = Field(..., example="cashflow_2026-08-09_0530")
    playbook_name: str = Field(..., example="cashflow_forecasting")
    timestamp: str = Field(..., example="2026-08-09T05:30:00+05:30")
    status: str = Field(..., example="completed")


class AgentRunDetailResponse(BaseModel):
    run_id: str
    playbook_name: str
    timestamp: str
    status: str
    result: Dict[str, Any]


class GenericAgentResponse(BaseModel):
    success: bool = True
    message: str = "Agent execution completed successfully"
    run_id: str
    data: Dict[str, Any]


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
