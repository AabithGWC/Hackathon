# Fund-Raising Document Agent (Agentic AI)

An automated **Fund-Raising Document Agentic AI System** for Non-Banking Financial Companies (NBFCs) and Financial Institutions. The agent ingests multi-source financial statements, loan portfolio metrics, debt profiles, and lender covenant limits to calculate all required KPIs, audit covenant compliance (DSCR, Debt/Equity, CRAR), generate strategic AI CFO insights, and assemble ready-to-submit Fund-Raising Data Packs for **NCD Issuances, Bank Facility Renewals, and Credit Rating Submissions**.

---

## 🚀 How to Run the System

### Option 1: Run via Virtual Environment (Recommended)

1. **Activate Environment & Run Agent**:
   ```powershell
   .\venv\Scripts\python.exe fund_raising_agent.py
   ```

### Option 2: Run via CLI Runner

```powershell
.\venv\Scripts\python.exe agent_runner.py
```

Both entry points run the same pipeline and produce the same figures.
`agent_runner.py` only adds a KPI summary to stdout.

---

## 🛠 System Pipeline & Execution Flow

```text
1. INGESTION STAGE: Loads .env, input_config.yaml, system_prompt.md, insights_prompt.md, output_schema.json, and 3 CSV datasets via Pandas.
2. ANALYSIS STAGE (LLM-driven, see below).
3. DOCUMENT WRITING STAGE: Renders the agent response into generated_datapack_document.md and corporate PDF generated_datapack_document.pdf.
```

### The analysis stage: LLM decides, Python evaluates

The LLM performs the analysis; Python performs the arithmetic. Testing showed
the model reads the source data correctly and describes the correct method
correctly, then mis-adds the columns — given the exact addends
`[1.5, 0.9, 1.2, 1.1, 0.8, 0.5, 0.3, 0.3, 0.1, 0.1]` it returned `22.1` instead
of `6.8`. So it is never asked to add:

```text
PASS 1  PLAN        The LLM decides the reporting period, the authoritative source
                    for each metric, and the method for every KPI. It emits a
                    machine-executable plan containing no computed numbers.
        EXECUTE     Python resolves the plan against the DataFrames — exact
                    aggregation, exact arithmetic.
        REPAIR      Figures outside plausible bounds, or contradicted by an
                    independent recompute, are fed back so the LLM revises its
                    own plan (up to LLM_MAX_PLAN_REPAIRS rounds).
        BACKSTOP    Any metric the model still cannot express is filled from a
                    fixed plan, so one bad entry cannot invalidate the payload.
PASS 2  ADJUDICATE  The LLM receives the exact figures and does the judgement:
                    covenant audit, validation outcomes, reconciliation
                    commentary, CFO insights.
        ENFORCE     Each covenant PASS/FAIL is re-derived from arithmetic, so a
                    mislabelled verdict cannot reach the document.
```

Nothing about the methodology is hardcoded. If the LLM is unreachable (missing
key, exhausted quota, network failure) the pipeline degrades to the same
executor running a fixed plan, and labels the output as a degraded run rather
than crashing.

**Cross-source reconciliation.** Portfolio and leverage figures are aggregated
bottom-up from the granular files; the summary columns in the financial
statements are treated as the claim being verified. Variances beyond tolerance
are reported as unreconciled and raised as review items — they are not silently
resolved.

**Model choice matters.** `openai/gpt-oss-120b` and `llama-3.3-70b-versatile`
produce workable plans. `llama-3.1-8b-instant` does not — it fails to converge
and fabricates covenant verdicts (caught by the enforcement step, but the run is
not trustworthy). Set `LLM_MODEL` in `.env`.

---

## 📁 Repository File Structure

- `fund_raising_agent.py`: Main Agent Execution Pipeline
- `llm_engine.py`: LLM calculation engine (plan / execute / adjudicate / audit)
- `preprocess.py`: Legacy standalone CSV/ratio helper — **not used by the pipeline**
- `pdf_generator.py`: PDF Report Generator (ReportLab / FPDF2)
- `agent_runner.py`: CLI Runner Script
- `input_config.yaml`: Playbook Configuration & KPI Definitions
- `system_prompt.md`: Agent Persona & System Prompt
- `insights_prompt.md`: AI Strategic Insights Prompt
- `output_schema.json`: Strict Output JSON Schema
- `.env`: Environment API Keys & Settings
- `requirements.txt`: Python Package Dependencies
- `sample_data/`:
  - `financial_statements_historical.csv`: 10-Quarter Financial Trajectory
  - `borrowing_facilities.csv`: 10 Lender Borrowing Tranches
  - `portfolio_quality_vintages.csv`: 10 Loan Product Segments
  - `fund_raising_data.json`: Primary Seed Dataset

---

## 📄 Output Artifacts Generated

1. **Agent JSON Payload**: `generated_datapack.json`
2. **Markdown Data Pack**: `generated_datapack_document.md`
3. **Printable Corporate PDF**: `generated_datapack_document.pdf`
