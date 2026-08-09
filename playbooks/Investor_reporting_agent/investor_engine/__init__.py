"""
Investor / Board Reporting Agent - investor_engine package.

Not named `engine`: a sibling agent in the integration process owns that name.

The division of labour across these modules is the whole design:

    metrics_engine   every figure, variance, driver decomposition, risk item and
                     verdict. Deterministic. No LLM.
    llm_engine       the prose, and only the prose. Groq drafts the commentary
                     cards; three guardrail layers check what it wrote back
                     against what metrics_engine computed.
    report_renderer  JSON, Markdown, PDF and the reporting-assistant screen.
    pipeline         orchestration and payload assembly.

The model writes. It does not calculate, and it does not decide what is true.
"""

from .pipeline import run_agent

__all__ = ["run_agent"]
