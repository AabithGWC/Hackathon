"""
7-Agent Executive Financial AI Suite Outcome Explainer Chatbot Engine.
Loads stored outputs from SQLite (`agent_runs.db`) across all 7 NBFC playbooks
and uses Groq LLM to explain calculations, reasoning, breaks, forecasts, and insights.
"""
import os
import json
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Ensure project root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

load_dotenv(os.path.join(REPO_ROOT, ".env"))

from engine.llm_client import LLMClient
from engine import store


ALL_PLAYBOOKS = [
    "reconciliation",
    "cashflow_forecasting",
    "cof_optimization",
    "expense_anomaly_detection",
    "npa_provisioning_forecast",
    "investor_reporting",
    "fund_raising_document",
]

PLAYBOOK_TITLES = {
    "reconciliation": "1. Three-Way Reconciliation Agent",
    "cashflow_forecasting": "2. Cashflow & Liquidity Forecasting Agent",
    "cof_optimization": "3. Cost of Funds (CoF) Optimization Agent",
    "expense_anomaly_detection": "4. Expense Anomaly Detection Agent",
    "npa_provisioning_forecast": "5. NPA & Provisioning Forecast Agent",
    "investor_reporting": "6. Investor & Board Reporting Agent",
    "fund_raising_document": "7. Fund Raising Document Agent",
}


def format_agent_run_context(playbook: str, run_data: Optional[Dict[str, Any]]) -> str:
    title = PLAYBOOK_TITLES.get(playbook, playbook.upper())
    if not run_data:
        return f"### {title}\n* Status: No run recorded yet in SQLite database.\n"

    meta = run_data.get("run_metadata", {})
    summary = run_data.get("summary") or run_data.get("reconciliation_summary") or {}
    insights = run_data.get("key_insights") or run_data.get("ai_insights") or run_data.get("ai_commentary") or []

    lines = [f"### {title}"]
    if meta:
        lines.append(f"* Run ID: {meta.get('run_id', 'N/A')} | Date: {str(meta.get('timestamp', ''))[:16]}")
    
    if summary and isinstance(summary, dict):
        # Format key scalar metrics concisely
        summary_str = ", ".join([f"{k}: {v}" for k, v in summary.items() if not isinstance(v, (dict, list))][:8])
        lines.append(f"* Summary: {summary_str}")

    # Specific compact formatting per playbook
    if playbook == "reconciliation":
        top_breaks = run_data.get("top_breaks", [])
        if top_breaks:
            lines.append(f"* Top Breaks Adjudicated ({len(top_breaks)} total):")
            for b in top_breaks[:4]:
                amt = b.get('amount')
                amt_str = f"₹{amt:,.0f}" if isinstance(amt, (int, float)) else str(amt)
                lines.append(
                    f"  - Break {b.get('break_id')}: {amt_str} | {b.get('reason')} | Risk: {b.get('risk')} | LLM Reasoning: {b.get('reasoning')}"
                )

    elif playbook == "cashflow_forecasting":
        ai_rec = run_data.get("ai_recommendation", {})
        if isinstance(ai_rec, dict) and ai_rec:
            lines.append(
                f"* Liquidity & Borrowing Rec: {ai_rec.get('narrative')} (Rec Borrowing: ₹{ai_rec.get('recommended_borrowing_cr')} Cr in {ai_rec.get('recommended_drawdown_window')}, Min Cash: ₹{ai_rec.get('minimum_projected_cash_balance_cr')} Cr)"
            )

    elif playbook == "cof_optimization":
        ai_rec = run_data.get("ai_recommendation", {})
        current_mix = run_data.get("current_mix", {})
        rec_mix = run_data.get("recommended_mix", {})
        if isinstance(ai_rec, dict) and ai_rec:
            lines.append(f"* CoF Strategy: Action={ai_rec.get('primary_action')}, Projected Savings={ai_rec.get('projected_cost_savings_bps')} bps, Action Window={ai_rec.get('recommended_action_window')}")
        if current_mix and rec_mix:
            lines.append(f"* Mix Shift: Current CoF={current_mix.get('blended_cost_pct')}% -> Rec CoF={rec_mix.get('blended_cost_pct')}%")

    elif playbook == "expense_anomaly_detection":
        anomalies = run_data.get("top_anomalies") or run_data.get("flagged_items") or []
        if anomalies and isinstance(anomalies, list):
            lines.append(f"* Flagged Anomalies: {len(anomalies)} items detected.")

    elif playbook == "npa_provisioning_forecast":
        npa_summary = run_data.get("provisioning_summary") or {}
        if isinstance(npa_summary, dict) and npa_summary:
            lines.append(f"* NPA Forecast: Gross NPA={npa_summary.get('gross_npa_pct')}%, Total Provision Required=₹{npa_summary.get('total_provision_cr')} Cr")

    elif playbook == "investor_reporting":
        kpi = run_data.get("kpi_comparison", {})
        if isinstance(kpi, dict) and kpi:
            # Extract top metrics concisely
            metrics_summary = []
            for category, items in list(kpi.items())[:3]:
                if isinstance(items, dict):
                    for k, v in list(items.items())[:3]:
                        if isinstance(v, dict):
                            metrics_summary.append(f"{k}: {v.get('actual') or v.get('current')}")
            lines.append(f"* Investor Report KPIs: {', '.join(metrics_summary[:6])}")

    elif playbook == "fund_raising_document":
        datapack = run_data.get("datapack_summary") or {}
        if isinstance(datapack, dict) and datapack:
            lines.append(f"* Fund Raising Highlights: Target Facility=₹{datapack.get('target_amount_cr')} Cr, Proposed Tenor={datapack.get('tenor_months')} months")

    if insights and isinstance(insights, list):
        clean_insights = []
        for item in insights[:3]:
            text = item.get("text", "") if isinstance(item, dict) else str(item)
            if text:
                clean_insights.append(text[:150])
        if clean_insights:
            lines.append(f"* AI Insights: {' | '.join(clean_insights)}")

    return "\n".join(lines) + "\n"



SYSTEM_PROMPT = """You are the Lead Financial Operations AI Assistant for an NBFC (Non-Banking Financial Company) AI Suite.
You have access to real-time calculated outcomes, financial metrics, break adjudications, cashflow projections, cost of funds optimizations, and risk alerts produced by 7 specialized AI calculation agents:

1. Reconciliation Agent
2. Cashflow & Liquidity Forecasting Agent
3. Cost of Funds (CoF) Optimization Agent
4. Expense Anomaly Detection Agent
5. NPA & Provisioning Forecast Agent
6. Investor & Board Reporting Agent
7. Fund Raising Document Agent

YOUR INSTRUCTIONS:
- Explain, interpret, and answer user queries accurately based strictly on the provided calculation results and AI reasoning from the 7 agent runs.
- Provide crisp, professional, executive-ready responses suitable for CFOs, Treasury Heads, Risk Officers, and Board Members.
- Use bullet points, bold emphasis, and clear financial terms (e.g. ₹ Crores, bps savings, break aging, NPA stages).
- If the user asks about an agent that has no recorded run data yet, explain clearly that no execution data is currently stored for that playbook and offer to summarize the agents that do have active run results.
- If asked a question completely unrelated to NBFC financial operations or these 7 agents, politely state your role as the 7-Agent Financial AI Suite Assistant and bring the focus back to explaining the 7-agent outcomes.
"""


class AgentChatbot:
    def __init__(self, model: str = None):
        self.llm = LLMClient(model=model)

    def ask(
        self,
        message: str,
        playbook_name: Optional[str] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Processes a user query and returns a detailed LLM response explaining agent outcomes.
        """
        playbooks_to_fetch = [playbook_name] if playbook_name and playbook_name in ALL_PLAYBOOKS else ALL_PLAYBOOKS
        referenced_playbooks = []

        context_blocks = []
        for pb in playbooks_to_fetch:
            run_data = store.get_last_run(pb)
            if run_data:
                referenced_playbooks.append(pb)
            context_blocks.append(format_agent_run_context(pb, run_data))

        aggregated_context = "\n".join(context_blocks)

        user_content_parts = [
            "LATEST 7-AGENT CALCULATION RUN OUTPUTS & OUTCOMES:",
            aggregated_context,
            "--------------------------------------------------",
        ]

        if chat_history:
            user_content_parts.append("CONVERSATION HISTORY:")
            for turn in chat_history[-6:]:  # keep last 6 turns for brevity
                role = "User" if turn.get("role") == "user" else "Assistant"
                user_content_parts.append(f"{role}: {turn.get('content', '')}")
            user_content_parts.append("--------------------------------------------------")

        user_content_parts.append(f"USER QUESTION: {message}")
        full_user_content = "\n".join(user_content_parts)

        reply = self.llm.complete_text(
            system_prompt=SYSTEM_PROMPT,
            user_content=full_user_content,
            temperature=0.3,
        )

        return {
            "reply": reply,
            "playbooks_referenced": referenced_playbooks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
