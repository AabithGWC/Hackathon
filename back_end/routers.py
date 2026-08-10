"""
API Routers for the 7-Agent Financial AI Platform.
Provides clean REST endpoints for fullstack frontend consumption.
"""
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks

# Ensure project root is in sys.path for engine & playbook imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from engine import run_agent as generic_runner
from engine import store
from engine.chatbot import AgentChatbot
try:
    from back_end.schemas import (
        HealthResponse,
        RunAgentRequest,
        InvestorReportRunRequest,
        GenericAgentResponse,
        AgentRunSummary,
        AgentRunDetailResponse,
        ChatRequest,
        ChatResponse,
    )
except ImportError:
    from schemas import (
        HealthResponse,
        RunAgentRequest,
        InvestorReportRunRequest,
        GenericAgentResponse,
        AgentRunSummary,
        AgentRunDetailResponse,
        ChatRequest,
        ChatResponse,
    )


router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse, tags=["System"])
def health_check():
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
        version="1.0.0",
        total_agents=7,
        available_playbooks=[
            "reconciliation",
            "cashflow_forecasting",
            "cof_optimization",
            "expense_anomaly_detection",
            "npa_provisioning_forecast",
            "investor_reporting",
            "fund_raising_document",
        ],
    )


# ------------------------------------------------------------------------------
# 1. RECONCILIATION AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/reconciliation/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_reconciliation(body: Optional[RunAgentRequest] = None):
    try:
        result = generic_runner.run("reconciliation")
        run_id = result.get("run_metadata", {}).get("run_id", "recon_latest")
        return GenericAgentResponse(
            message="Reconciliation Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconciliation Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# 2. CASHFLOW FORECASTING AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/cashflow/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_cashflow(body: Optional[RunAgentRequest] = None):
    try:
        result = generic_runner.run("cashflow_forecasting")
        run_id = result.get("run_metadata", {}).get("run_id", "cashflow_latest")
        return GenericAgentResponse(
            message="Cashflow Forecasting Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cashflow Forecasting Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# 3. COST OF FUNDS (CoF) OPTIMIZATION AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/cof/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_cof(body: Optional[RunAgentRequest] = None):
    try:
        result = generic_runner.run("cof_optimization")
        run_id = result.get("run_metadata", {}).get("run_id", "cof_latest")
        return GenericAgentResponse(
            message="Cost of Funds Optimization Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cost of Funds Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# 4. EXPENSE ANOMALY DETECTION AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/expense-anomalies/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_expense_anomalies(body: Optional[RunAgentRequest] = None):
    try:
        result = generic_runner.run("expense_anomaly_detection")
        run_id = result.get("run_metadata", {}).get("run_id", "expense_latest")
        return GenericAgentResponse(
            message="Expense Anomaly Detection Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Expense Anomaly Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# 5. NPA & PROVISIONING FORECAST AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/npa-forecast/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_npa_forecast(body: Optional[RunAgentRequest] = None):
    try:
        result = generic_runner.run("npa_provisioning_forecast")
        run_id = result.get("run_metadata", {}).get("run_id", "npa_latest")
        return GenericAgentResponse(
            message="NPA & Provisioning Forecast Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NPA Forecast Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# 6. INVESTOR & BOARD REPORTING AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/investor-report/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_investor_report(body: Optional[InvestorReportRunRequest] = None):
    try:
        tone = body.tone if body else "board_formal"
        audience = body.audience if body else "board"

        from playbooks.Investor_reporting_agent.investor_engine.pipeline import run_agent as inv_pipeline_run

        result = inv_pipeline_run(tone=tone, audience=audience, verbose=False)
        run_id = f"investor_report_{datetime.now().strftime('%Y-%m-%d_%H%M')}"
        store.save_run(run_id, "investor_reporting", datetime.now().isoformat(), "completed", result)

        return GenericAgentResponse(
            message="Investor & Board Reporting Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Investor Report Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# 7. FUND RAISING DOCUMENT AGENT
# ------------------------------------------------------------------------------
@router.post("/agents/fund-raising/run", response_model=GenericAgentResponse, tags=["Agents"])
def run_fund_raising(body: Optional[RunAgentRequest] = None):
    try:
        fr_path = os.path.join(REPO_ROOT, "playbooks", "Fund_raising_document_agent")
        if fr_path not in sys.path:
            sys.path.insert(0, fr_path)

        from fund_raising_agent import run_agent as fr_pipeline_run

        result = fr_pipeline_run()
        run_id = f"fund_raising_{datetime.now().strftime('%Y-%m-%d_%H%M')}"
        store.save_run(run_id, "fund_raising_document", datetime.now().isoformat(), "completed", result)

        return GenericAgentResponse(
            message="Fund Raising Document Agent completed successfully",
            run_id=run_id,
            data=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fund Raising Agent error: {str(e)}")


# ------------------------------------------------------------------------------
# HISTORY & RUN AUDIT TRAIL
# ------------------------------------------------------------------------------
@router.get("/history", response_model=List[AgentRunSummary], tags=["Run History"])
def get_run_history(playbook: Optional[str] = Query(None, description="Filter runs by playbook name")):
    try:
        runs = store.list_runs(playbook_name=playbook)
        return [
            AgentRunSummary(
                run_id=r["run_id"],
                playbook_name=r["playbook_name"],
                timestamp=r["timestamp"],
                status=r["status"],
            )
            for r in runs
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch run history: {str(e)}")


@router.get("/history/{run_id}", response_model=AgentRunDetailResponse, tags=["Run History"])
def get_run_detail(run_id: str):
    run_record = store.get_run(run_id)
    if not run_record:
        raise HTTPException(status_code=404, detail=f"Run ID '{run_id}' not found in database")
    return AgentRunDetailResponse(
        run_id=run_record["run_id"],
        playbook_name=run_record["playbook_name"],
        timestamp=run_record["timestamp"],
        status=run_record["status"],
        result=run_record["result"],
    )


# ------------------------------------------------------------------------------
# 7-AGENT OUTCOMES CHATBOT ASSISTANT
# ------------------------------------------------------------------------------
chatbot_instance = AgentChatbot()


@router.post("/chat", response_model=ChatResponse, tags=["Chatbot Assistant"])
def chat_with_agents(body: ChatRequest):
    try:
        history_dicts = [h.dict() for h in body.history] if body.history else None
        res = chatbot_instance.ask(
            message=body.message,
            playbook_name=body.playbook_name,
            chat_history=history_dicts,
        )
        return ChatResponse(
            success=True,
            reply=res["reply"],
            playbooks_referenced=res["playbooks_referenced"],
            timestamp=res["timestamp"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chatbot Assistant error: {str(e)}")

