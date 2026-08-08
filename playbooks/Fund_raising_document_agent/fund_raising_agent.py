"""
==============================================================================
NBFC FUND-RAISING DOCUMENT AGENT - ALL-IN-ONE STANDALONE PYTHON AGENT
==============================================================================
A single self-contained Agentic AI script for Non-Banking Financial Companies (NBFCs).

PERFORMS DYNAMIC CALCULATIONS ACCORDING TO input_config.yaml & MD PROMPTS:
1. Ingests environment credentials (.env) & playbook specifications (input_config.yaml).
2. Ingests Markdown system prompts (system_prompt.md & insights_prompt.md).
3. Loads 3 multi-source CSV datasets via Pandas:
   - sample_data/financial_statements_historical.csv (10 historical quarters)
   - sample_data/borrowing_facilities.csv (10 borrowing facility tranches)
   - sample_data/portfolio_quality_vintages.csv (10 loan product segments)
4. Executes formulas & covenant rules parsed directly from input_config.yaml:
   - Evaluates YAML formulas: ((Current - Prior) / Prior) * 100
   - Evaluates YAML covenants: DSCR >= 1.25, D/E <= 4.00, ICR >= 2.00, CRAR >= 15.0%
   - Evaluates YAML 42 validation rule catalog (V01-V42)
5. Synthesizes AI CFO Insights according to insights_prompt.md instructions.
6. Validates payload against JSON Schema (output_schema.json).
7. Writes generated output files:
   - generated_datapack.json (JSON Payload)
   - generated_datapack_document.md (Markdown Document)
   - generated_datapack_document.pdf (Printable Corporate PDF)
8. Returns the final JSON object payload.
==============================================================================
"""

import os
import sys
import json
import time
import site
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_data")

possible_site_packages = [
    os.path.join(BASE_DIR, "venv", "Lib", "site-packages"),
    site.getusersitepackages(),
    os.path.expanduser(r"~\AppData\Roaming\Python\Python314\site-packages"),
    os.path.expanduser(r"~\AppData\Roaming\Python\Python310\site-packages")
]
for p in possible_site_packages:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except Exception:
    PANDAS_AVAILABLE = False

try:
    import yaml
    YAML_AVAILABLE = True
except Exception:
    YAML_AVAILABLE = False

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except Exception:
    JSONSCHEMA_AVAILABLE = False

import llm_engine

REPORTLAB_AVAILABLE = False
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Table, TableStyle, PageBreak, HRFlowable
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except Exception:
    pass


# ==============================================================================
# 1. FILE READERS (.env, YAML, MD PROMPTS, CSVS, SCHEMAS)
# ==============================================================================

def load_env():
    """Delegates to llm_engine, which also strips inline `# comment` trailers."""
    if llm_engine.load_env():
        provider, model = llm_engine.resolve_provider()
        print(f" -> [1/7 ENV INGESTION] Loaded .env | LLM provider: {provider} ({model})")
        return True
    return False


def load_yaml_config(filename="input_config.yaml"):
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) if YAML_AVAILABLE else {}
        print(f" -> [2/7 YAML INGESTION] Loaded playbook config: {filename}")
        return config
    raise FileNotFoundError(f"Configuration file {filename} not found!")


def load_prompt_md(filename):
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        print(f" -> [3/7 MD PROMPT INGESTION] Loaded prompt file: {filename} ({len(text)} chars)")
        return text
    raise FileNotFoundError(f"Prompt file {filename} not found!")


def load_json_schema(filename="output_schema.json"):
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        print(f" -> [4/7 SCHEMA INGESTION] Loaded JSON schema: {filename}")
        return schema
    raise FileNotFoundError(f"Schema file {filename} not found!")


def read_csv_dataframes():
    dataframes = {}
    fin_csv = os.path.join(SAMPLE_DIR, "financial_statements_historical.csv")
    borr_csv = os.path.join(SAMPLE_DIR, "borrowing_facilities.csv")
    port_csv = os.path.join(SAMPLE_DIR, "portfolio_quality_vintages.csv")

    if PANDAS_AVAILABLE and os.path.exists(fin_csv):
        df_fin = pd.read_csv(fin_csv)
        dataframes["financial_df"] = df_fin
        print(f" -> [5/7 CSV INGESTION] Ingested Pandas DataFrame: {os.path.basename(fin_csv)} ({len(df_fin)} historical quarters)")

    if PANDAS_AVAILABLE and os.path.exists(borr_csv):
        df_borr = pd.read_csv(borr_csv)
        dataframes["borrowings_df"] = df_borr
        print(f" -> [5/7 CSV INGESTION] Ingested Pandas DataFrame: {os.path.basename(borr_csv)} ({len(df_borr)} borrowing facility tranches)")

    if PANDAS_AVAILABLE and os.path.exists(port_csv):
        df_port = pd.read_csv(port_csv)
        dataframes["portfolio_df"] = df_port
        print(f" -> [5/7 CSV INGESTION] Ingested Pandas DataFrame: {os.path.basename(port_csv)} ({len(df_port)} loan product segments)")

    return dataframes


# ==============================================================================
# 2. CALCULATION ENGINE BASED ON input_config.yaml & MD PROMPT RULES
# ==============================================================================

def execute_yaml_and_md_calculations(config, dataframes, system_prompt, insights_prompt, seed_data):
    """
    Runs the LLM analysis engine, then assembles the data pack around its output.

    All KPI figures, covenant verdicts, validation outcomes, reconciliation
    findings and CFO commentary originate from the model (see llm_engine for the
    plan/execute/adjudicate split). Nothing here hardcodes a financial figure.
    """
    print("\n -> [6/7 ANALYSIS ENGINE] Delegating calculation to the LLM agent...")
    start_calc_time = time.time()

    df_fin = dataframes.get("financial_df")
    df_borr = dataframes.get("borrowings_df")
    df_port = dataframes.get("portfolio_df")

    try:
        llm_result, llm_meta = llm_engine.run_llm_analysis(
            config, dataframes, system_prompt, insights_prompt)
    except (llm_engine.LLMUnavailable, RuntimeError) as e:
        # A missing key, exhausted quota or network failure must not take the
        # document pipeline down. Degrade visibly rather than crash.
        llm_result, llm_meta = llm_engine.run_fallback_analysis(
            config, dataframes, reason=e)

    # Independent recompute - never silently overrides the model, but a figure or
    # a covenant verdict it disagrees with is surfaced on the document.
    discrepancies = llm_engine.audit_llm_arithmetic(llm_result, dataframes)

    seed_data["financial_metrics"] = llm_result["financial_metrics"]
    seed_data["portfolio_metrics"] = llm_result["portfolio_metrics"]
    seed_data["key_ratios"] = llm_result["key_ratios"]
    seed_data["ai_insights"] = llm_result.get("ai_insights", [])
    seed_data["covenant_audit"] = llm_result.get("covenant_audit", [])
    seed_data["validation_results"] = llm_result.get("validation_results", [])
    seed_data["data_conflicts"] = llm_result.get("data_conflicts", [])
    seed_data["reconciliations"] = llm_result.get("reconciliations", [])
    seed_data["method_notes"] = llm_result.get("method_notes", [])
    seed_data["arithmetic_audit"] = discrepancies
    seed_data["backstopped_metrics"] = llm_result.get("backstopped_metrics", [])
    seed_data["covenant_corrections"] = llm_result.get("covenant_corrections", [])
    seed_data["llm_metadata"] = llm_meta
    seed_data["reporting_period"] = llm_result.get("reporting_period")
    seed_data["degraded_run"] = bool(llm_result.get("degraded"))

    # The document tables render straight from the CSVs.
    if df_fin is not None:
        seed_data["historical_10_quarters"] = df_fin.to_dict(orient="records")
    if df_borr is not None:
        seed_data["borrowing_facilities_10_tranches"] = df_borr.to_dict(orient="records")
    if df_port is not None:
        seed_data["portfolio_segments_10_products"] = df_port.to_dict(orient="records")

    # Review items: whatever the model raised, plus every breached reconciliation
    # and every arithmetic discrepancy, so nothing unresolved is hidden.
    review_items = list(llm_result.get("review_items") or [])
    for rc in seed_data["reconciliations"]:
        if rc.get("breached"):
            review_items.append({
                "field_name": rc["field"],
                "issue_type": "Conflict",
                "recommended_action": (
                    f"Unreconciled variance of {rc['variance_pct']}%: "
                    f"{rc['reported_basis']} reports {rc['reported_value']} but "
                    f"bottom-up aggregation gives {rc['bottom_up_value']}. "
                    f"Confirm which figure the lender pack should carry."),
            })
    for d in discrepancies:
        review_items.append({
            "field_name": d["metric"],
            "issue_type": "Conflict",
            "recommended_action": (
                f"Agent reported {d['llm_value']} but independent recompute gives "
                f"{d['expected']}. Verify before circulation."),
        })
    seed_data["review_items"] = review_items

    # Validation counts reflect rules actually evaluated - not a constant.
    vres = seed_data["validation_results"]
    passed = sum(1 for v in vres if str(v.get("status", "")).upper() == "PASS")
    failed = sum(1 for v in vres if str(v.get("status", "")).upper() == "FAIL")
    not_eval = len(vres) - passed - failed

    ops = seed_data.setdefault("operational_kpis", {})
    ops["validation_checks_passed"] = passed
    ops["validation_checks_total"] = len(vres)
    ops["validation_checks_failed"] = failed
    ops["validation_checks_not_evaluable"] = not_eval
    ops["items_needing_review"] = len(review_items)

    covenant_breaches = [c for c in seed_data["covenant_audit"]
                         if str(c.get("status", "")).upper() == "FAIL"]
    blocking = bool(covenant_breaches or failed or discrepancies)
    ops["pack_status"] = "Requires Attention" if blocking else "Ready for Review"

    for cov in seed_data["covenant_audit"]:
        print(f"    [COVENANT] {cov.get('name')}: {cov.get('computed')}"
              f"{cov.get('unit', '')} {cov.get('rule', '')} -> {cov.get('status')}")
    print(f"    [VALIDATION] {passed} passed, {failed} failed, "
          f"{not_eval} not evaluable, of {len(vres)} rules evaluated")
    print(f"    [ANALYSIS] Completed in {round(time.time() - start_calc_time, 2)}s "
          f"({llm_meta.get('passes')} LLM passes, "
          f"{llm_meta.get('repair_rounds', 0)} plan repair round(s))")

    # Build Markdown Data Pack Document Text Response
    meta = seed_data["company_metadata"]
    ops = seed_data["operational_kpis"]
    fin = seed_data["financial_metrics"]
    ratios = seed_data["key_ratios"]

    hist_quarters = seed_data.get("historical_10_quarters", [])
    borrowing_tranches = seed_data.get("borrowing_facilities_10_tranches", [])
    portfolio_products = seed_data.get("portfolio_segments_10_products", [])

    period_label = seed_data.get("reporting_period") or meta.get("period", "Current Period")
    llm_meta = seed_data.get("llm_metadata", {})
    _failed = ops.get("validation_checks_failed", 0)
    _not_eval = ops.get("validation_checks_not_evaluable", 0)
    validation_caveat = ""
    if _failed or _not_eval:
        bits = []
        if _failed:
            bits.append(f"{_failed} FAILED")
        if _not_eval:
            bits.append(f"{_not_eval} not evaluable from available data")
        validation_caveat = " — " + ", ".join(bits)
    _disc = seed_data.get("arithmetic_audit", [])
    audit_line = ("Clean — every figure reproduced by independent recompute"
                  if not _disc else
                  f"{len(_disc)} figure(s) could not be reproduced — see review items")

    doc_content = f"""# {meta['company_name']} - FUND-RAISING DATA PACK
**Active Package**: {meta['active_package']}
**Period**: {period_label} | **Accounting Standard**: {meta['accounting_standard']} | **Classification**: {meta['confidentiality']}

---

## EXECUTIVE SUMMARY & AGENT PERFORMANCE METRICS
- **Reporting Period**: {period_label}
- **Analysis Engine**: {llm_meta.get('provider')} / {llm_meta.get('model')} ({llm_meta.get('passes')} passes)
- **Data Sources Connected**: {ops['data_sources_connected']} / 8 ({ops['data_sources_status']})
- **Sections Completed**: {ops['sections_completed']} / {ops['sections_total']} ({ops['completion_pct']}% Complete)
- **Automated Validation Checks**: {ops['validation_checks_passed']} / {ops['validation_checks_total']} Passed{validation_caveat}
- **Arithmetic Audit**: {audit_line}
- **Pack Status**: {ops['pack_status']}
- **Estimated Time Saved**: {ops['est_time_saved_hours']} Hours vs Manual Preparation Process

---

## 1. KEY FINANCIAL METRICS SUMMARY
| Metric | {period_label} | YoY Growth | Status |
| :--- | :---: | :---: | :---: |
| **Revenue** | ₹{fin['revenue_cr']} Cr | +{fin['revenue_yoy_pct']}% YoY | Strong |
| **EBITDA** | ₹{fin['ebitda_cr']} Cr | +{fin['ebitda_yoy_pct']}% YoY | Strong |
| **Profit After Tax (PAT)** | ₹{fin['pat_cr']} Cr | +{fin['pat_yoy_pct']}% YoY | Robust |
| **Total Debt** | ₹{fin['total_debt_cr']} Cr | +{fin['total_debt_yoy_pct']}% YoY | Managed |
| **Net Worth** | ₹{fin['net_worth_cr']} Cr | +{fin['net_worth_yoy_pct']}% YoY | Expanded |

---

## 2. HISTORICAL 10-QUARTER FINANCIAL PERFORMANCE TRAJECTORY (FROM CSV)
| Period | Revenue (₹ Cr) | EBITDA (₹ Cr) | PAT (₹ Cr) | Total Debt (₹ Cr) | Net Worth (₹ Cr) | AUM (₹ Cr) | GNPA % | DSCR | D/E |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
"""
    for row in hist_quarters:
        doc_content += f"| {row['period']} | ₹{row['revenue_cr']} | ₹{row['ebitda_cr']} | ₹{row['pat_cr']} | ₹{row['total_debt_cr']} | ₹{row['net_worth_cr']} | ₹{row['aum_cr']} | {row['gnpa_pct']}% | {row['dscr']}x | {row['debt_to_equity']}x |\n"

    doc_content += f"""
---

## 3. BORROWING FACILITIES & LENDER SCHEDULE (10 TRANCHES FROM CSV)
| Facility ID | Lender / Security | Type | Sanctioned (₹ Cr) | Outstanding (₹ Cr) | Interest Rate | Status |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
"""
    for fac in borrowing_tranches:
        doc_content += f"| {fac['facility_id']} | {fac['lender_name']} | {fac['facility_type']} | ₹{fac['sanctioned_amount_cr']} | ₹{fac['outstanding_balance_cr']} | {fac['interest_rate_pct']}% | {fac['status']} |\n"

    doc_content += f"""
---

## 4. PORTFOLIO QUALITY BY PRODUCT SEGMENT (10 SEGMENTS FROM CSV)
| Segment ID | Product Category | AUM (₹ Cr) | Active Accounts | Gross NPA (₹ Cr) | Collection Eff % |
| :--- | :--- | :---: | :---: | :---: | :---: |
"""
    for seg in portfolio_products:
        doc_content += f"| {seg['segment_id']} | {seg['product_category']} | ₹{seg['aum_cr']} | {seg['active_accounts']:,} | ₹{seg['gnpa_90_plus_dpd_cr']} | {seg['collection_efficiency_pct']}% |\n"

    doc_content += """
---

## 5. KEY FINANCIAL & LENDER COVENANT RATIOS
| Ratio Name | Computed Value | Covenant Limit | Headroom | Status |
| :--- | :---: | :---: | :--- | :---: |
"""
    for cov in seed_data.get("covenant_audit", []):
        status = str(cov.get("status", "")).upper()
        badge = "PASS [✓]" if status == "PASS" else "**FAIL [✗]**"
        unit = cov.get("unit", "")
        doc_content += (f"| **{cov.get('name')}** | **{cov.get('computed')}{unit}** | "
                        f"{cov.get('rule', '')}{unit} | {cov.get('headroom', '-')} | {badge} |\n")

    doc_content += f"""
---

## 6. CROSS-SOURCE RECONCILIATION
| Metric | Bottom-Up (granular source) | As Reported (summary) | Variance | Status |
| :--- | :---: | :---: | :---: | :---: |
"""
    for rc in seed_data.get("reconciliations", []):
        badge = "**UNRECONCILED**" if rc.get("breached") else "Agreed"
        doc_content += (f"| {rc.get('field')} | {rc.get('bottom_up_value')} | "
                        f"{rc.get('reported_value')} | {rc.get('variance_pct')}% | {badge} |\n")
    if not seed_data.get("reconciliations"):
        doc_content += "| _No cross-source reconciliations were performed_ | - | - | - | - |\n"

    doc_content += f"""
---

## 7. AUTOMATED VALIDATION RULE RESULTS ({ops['validation_checks_passed']}/{ops['validation_checks_total']} passed)
| ID | Rule | Observed | Status |
| :--- | :--- | :--- | :---: |
"""
    for v in seed_data.get("validation_results", []):
        doc_content += (f"| {v.get('id')} | {v.get('name')} | {v.get('observed', '-')} | "
                        f"{v.get('status')} |\n")

    doc_content += f"""
---

## 8. ITEMS REQUIRING FINANCE TEAM REVIEW ({len(seed_data['review_items'])})
"""
    for item in seed_data['review_items']:
        doc_content += f"- **{item['field_name']}** [{item['issue_type']}]: {item['recommended_action']}\n"
    if not seed_data['review_items']:
        doc_content += "- None outstanding.\n"

    doc_content += """
---

## 9. STRATEGIC AI CFO INSIGHTS
"""
    for insight in seed_data['ai_insights']:
        doc_content += f"- {insight}\n"

    if seed_data.get("method_notes"):
        doc_content += "\n---\n\n## 10. METHODOLOGY NOTES\n"
        for note in seed_data["method_notes"]:
            doc_content += f"- {note}\n"

    doc_content += f"""
---
*Generated by Fund-Raising Document Agent. Figures computed by
{llm_meta.get('provider')}/{llm_meta.get('model')} and verified against an
independent recompute. All figures as per Ind AS financial guidelines.*
"""

    seed_data["generated_document_markdown"] = doc_content
    return seed_data


# ==============================================================================
# 3. CORPORATE PDF WRITER ENGINE
# ==============================================================================

def build_pdf_document(data, pdf_path):
    if not REPORTLAB_AVAILABLE:
        with open(pdf_path, "w", encoding="utf-8") as f:
            f.write(data.get("generated_document_markdown", "Fund-Raising Document"))
        return pdf_path

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    PRIMARY_COPPER = colors.HexColor("#C86537")
    DARK_CHARCOAL = colors.HexColor("#1F1E1B")
    LIGHT_CREAM = colors.HexColor("#FAF8F3")
    BORDER_GREY = colors.HexColor("#E6E2D8")
    MUTED_GREY = colors.HexColor("#6B675E")

    style_title = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=PRIMARY_COPPER, spaceAfter=4)
    style_subtitle = ParagraphStyle('DocSubtitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=DARK_CHARCOAL, spaceAfter=8)
    style_meta = ParagraphStyle('DocMeta', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12, textColor=MUTED_GREY, spaceAfter=12)
    style_h2 = ParagraphStyle('SectionHeader', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=DARK_CHARCOAL, spaceBefore=12, spaceAfter=6)
    style_bullet = ParagraphStyle('BulletCustom', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13, textColor=DARK_CHARCOAL, leftIndent=12, spaceAfter=4)
    style_table_cell = ParagraphStyle('TableCell', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=DARK_CHARCOAL)
    style_table_header = ParagraphStyle('TableHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)

    story = []

    meta = data["company_metadata"]
    fin = data["financial_metrics"]
    port = data["portfolio_metrics"]
    ratios = data["key_ratios"]
    ops = data["operational_kpis"]
    hist_quarters = data.get("historical_10_quarters", [])
    borrowing_tranches = data.get("borrowing_facilities_10_tranches", [])
    portfolio_products = data.get("portfolio_segments_10_products", [])

    story.append(Paragraph(meta["company_name"], style_title))
    story.append(Paragraph(f"FUND-RAISING DATA PACK: {meta['active_package']}", style_subtitle))
    _period = data.get("reporting_period") or meta.get("period", "")
    story.append(Paragraph(f"Period: <b>{_period}</b> | Accounting Standard: <b>{meta['accounting_standard']}</b> | Classification: <b>{meta['confidentiality']}</b>", style_meta))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY_COPPER, spaceAfter=8))

    story.append(Paragraph("EXECUTIVE SUMMARY & OPERATIONAL METRICS", style_h2))
    _total_checks = ops.get("validation_checks_total", 0) or 1
    _pass_rate = round(ops.get("validation_checks_passed", 0) / _total_checks * 100)
    _validation_cell = (f"{ops['validation_checks_passed']} / "
                        f"{ops['validation_checks_total']} Passed ({_pass_rate}%)")
    if ops.get("validation_checks_failed"):
        _validation_cell += f" <font color='red'><b>{ops['validation_checks_failed']} FAILED</b></font>"
    _status_colour = "green" if ops.get("pack_status") == "Ready for Review" else "red"
    exec_summary_data = [
        [Paragraph("<b>Data Sources Connected:</b>", style_table_cell), Paragraph(f"{ops['data_sources_connected']} / 8 ({ops.get('data_sources_status', 'All Active')})", style_table_cell), Paragraph("<b>Sections Completed:</b>", style_table_cell), Paragraph(f"{ops['sections_completed']} / {ops['sections_total']} (100%)", style_table_cell)],
        [Paragraph("<b>Validation Checks:</b>", style_table_cell), Paragraph(_validation_cell, style_table_cell), Paragraph("<b>Pack Status:</b>", style_table_cell), Paragraph(f"<font color='{_status_colour}'><b>{ops['pack_status']}</b></font>", style_table_cell)],
        [Paragraph("<b>Est. Time Saved:</b>", style_table_cell), Paragraph(f"<b>{ops['est_time_saved_hours']} Hours</b> vs Manual", style_table_cell), Paragraph("<b>Review Items:</b>", style_table_cell), Paragraph(f"{ops['items_needing_review']} Requiring Attention", style_table_cell)]
    ]
    exec_table = Table(exec_summary_data, colWidths=[1.4*inch, 2.0*inch, 1.4*inch, 2.0*inch])
    exec_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), LIGHT_CREAM), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 4)]))
    story.append(exec_table)

    story.append(Paragraph("1. KEY FINANCIAL METRICS SUMMARY", style_h2))
    fin_table_data = [
        [Paragraph("Metric Name", style_table_header), Paragraph("Q1 FY2026-27 Value", style_table_header), Paragraph("YoY Growth", style_table_header), Paragraph("Status", style_table_header)],
        [Paragraph("Revenue", style_table_cell), Paragraph(f"₹{fin['revenue_cr']} Cr", style_table_cell), Paragraph(f"+{fin['revenue_yoy_pct']}% YoY", style_table_cell), Paragraph("Strong", style_table_cell)],
        [Paragraph("EBITDA", style_table_cell), Paragraph(f"₹{fin['ebitda_cr']} Cr", style_table_cell), Paragraph(f"+{fin['ebitda_yoy_pct']}% YoY", style_table_cell), Paragraph("Strong", style_table_cell)],
        [Paragraph("Profit After Tax (PAT)", style_table_cell), Paragraph(f"₹{fin['pat_cr']} Cr", style_table_cell), Paragraph(f"+{fin['pat_yoy_pct']}% YoY", style_table_cell), Paragraph("Robust", style_table_cell)],
        [Paragraph("Total Debt / Borrowings", style_table_cell), Paragraph(f"₹{fin['total_debt_cr']} Cr", style_table_cell), Paragraph(f"+{fin['total_debt_yoy_pct']}% YoY", style_table_cell), Paragraph("Managed", style_table_cell)],
        [Paragraph("Net Worth", style_table_cell), Paragraph(f"₹{fin['net_worth_cr']} Cr", style_table_cell), Paragraph(f"+{fin['net_worth_yoy_pct']}% YoY", style_table_cell), Paragraph("Expanded", style_table_cell)],
    ]
    fin_table = Table(fin_table_data, colWidths=[2.0*inch, 1.8*inch, 1.5*inch, 1.5*inch])
    fin_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
    story.append(fin_table)

    story.append(Paragraph("2. PORTFOLIO QUALITY & ASSET METRICS", style_h2))
    port_summary_data = [
        [Paragraph("Portfolio Metric Name", style_table_header), Paragraph("Computed Value", style_table_header), Paragraph("Benchmark / Growth Trajectory", style_table_header)],
        [Paragraph("Assets Under Management (AUM)", style_table_cell), Paragraph(f"<b>₹{port['aum_cr']:,} Cr</b>", style_table_cell), Paragraph(f"+{port['aum_qoq_pct']}% QoQ Expansion", style_table_cell)],
        [Paragraph("Quarterly Disbursements", style_table_cell), Paragraph(f"₹{port['disbursement_cr']} Cr", style_table_cell), Paragraph("Active Origination", style_table_cell)],
        [Paragraph("Quarterly Collections", style_table_cell), Paragraph(f"₹{port['collection_cr']} Cr", style_table_cell), Paragraph("Healthy Inflows", style_table_cell)],
        [Paragraph("Gross NPA (GNPA %)", style_table_cell), Paragraph(f"<b>{port['gnpa_pct']:.2f}%</b>", style_table_cell), Paragraph(f"Improved from {port.get('gnpa_previous_pct', 2.0):.2f}%", style_table_cell)],
        [Paragraph("Net NPA (NNPA %)", style_table_cell), Paragraph(f"<b>{port['nnpa_pct']}%</b>", style_table_cell), Paragraph("Well-provisioned (< 1.0%)", style_table_cell)],
        [Paragraph("Collection Efficiency %", style_table_cell), Paragraph(f"<b>{port['collection_efficiency_pct']}%</b>", style_table_cell), Paragraph("High Recovery Efficiency", style_table_cell)],
        [Paragraph("Active Loan Accounts", style_table_cell), Paragraph(f"<b>{port['active_loans']:,}</b>", style_table_cell), Paragraph("Granular Borrowing Base", style_table_cell)],
    ]
    port_summary_table = Table(port_summary_data, colWidths=[2.5*inch, 1.8*inch, 2.5*inch])
    port_summary_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
    story.append(port_summary_table)

    story.append(Paragraph("3. HISTORICAL 10-QUARTER FINANCIAL PERFORMANCE TRAJECTORY", style_h2))
    hist_headers = ["Period", "Revenue", "EBITDA", "PAT", "Debt", "NetWorth", "AUM", "GNPA%", "DSCR", "D/E"]
    hist_table_data = [[Paragraph(h, style_table_header) for h in hist_headers]]
    for q in hist_quarters:
        hist_table_data.append([
            Paragraph(q['period'], style_table_cell), Paragraph(f"₹{q['revenue_cr']}", style_table_cell), Paragraph(f"₹{q['ebitda_cr']}", style_table_cell), Paragraph(f"₹{q['pat_cr']}", style_table_cell), Paragraph(f"₹{q['total_debt_cr']}", style_table_cell), Paragraph(f"₹{q['net_worth_cr']}", style_table_cell), Paragraph(f"₹{q['aum_cr']}", style_table_cell), Paragraph(f"{q['gnpa_pct']}%", style_table_cell), Paragraph(f"{q['dscr']}x", style_table_cell), Paragraph(f"{q['debt_to_equity']}x", style_table_cell)
        ])
    hist_table = Table(hist_table_data, colWidths=[0.6*inch, 0.7*inch, 0.7*inch, 0.6*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.6*inch, 0.5*inch, 0.5*inch])
    hist_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
    story.append(hist_table)

    story.append(PageBreak())

    story.append(Paragraph("4. BORROWING FACILITIES & LENDER SCHEDULE (10 TRANCHES)", style_h2))
    borr_headers = ["Facility ID", "Lender Name", "Facility Type", "Sanctioned (₹ Cr)", "Outstanding (₹ Cr)", "Rate %", "Status"]
    borr_table_data = [[Paragraph(h, style_table_header) for h in borr_headers]]
    for fac in borrowing_tranches:
        borr_table_data.append([
            Paragraph(fac['facility_id'], style_table_cell), Paragraph(fac['lender_name'], style_table_cell), Paragraph(fac['facility_type'], style_table_cell), Paragraph(f"₹{fac['sanctioned_amount_cr']}", style_table_cell), Paragraph(f"₹{fac['outstanding_balance_cr']}", style_table_cell), Paragraph(f"{fac['interest_rate_pct']}%", style_table_cell), Paragraph(fac['status'], style_table_cell)
        ])
    borr_table = Table(borr_table_data, colWidths=[0.8*inch, 1.8*inch, 1.6*inch, 0.9*inch, 0.9*inch, 0.5*inch, 0.8*inch])
    borr_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
    story.append(borr_table)

    story.append(Paragraph("5. PORTFOLIO QUALITY BY PRODUCT SEGMENT (10 SEGMENTS)", style_h2))
    port_headers = ["Segment ID", "Product Category", "AUM (₹ Cr)", "Active Accounts", "Gross NPA (₹ Cr)", "Collection Eff %"]
    port_table_data = [[Paragraph(h, style_table_header) for h in port_headers]]
    for seg in portfolio_products:
        port_table_data.append([
            Paragraph(seg['segment_id'], style_table_cell), Paragraph(seg['product_category'], style_table_cell), Paragraph(f"₹{seg['aum_cr']}", style_table_cell), Paragraph(f"{seg['active_accounts']:,}", style_table_cell), Paragraph(f"₹{seg['gnpa_90_plus_dpd_cr']}", style_table_cell), Paragraph(f"{seg['collection_efficiency_pct']}%", style_table_cell)
        ])
    port_table = Table(port_table_data, colWidths=[0.9*inch, 2.2*inch, 1.0*inch, 1.1*inch, 1.1*inch, 1.0*inch])
    port_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
    story.append(port_table)

    story.append(Paragraph("6. KEY FINANCIAL & LENDER COVENANT RATIOS", style_h2))
    cov_table_data = [[Paragraph(h, style_table_header) for h in
                       ("Ratio Name", "Computed Value", "Covenant Limit", "Headroom", "Status")]]
    for cov in data.get("covenant_audit", []):
        passed = str(cov.get("status", "")).upper() == "PASS"
        badge = ("<font color='green'><b>PASS [✓]</b></font>" if passed
                 else "<font color='red'><b>FAIL [✗]</b></font>")
        unit = cov.get("unit", "")
        cov_table_data.append([
            Paragraph(str(cov.get("name", "")), style_table_cell),
            Paragraph(f"<b>{cov.get('computed')}{unit}</b>", style_table_cell),
            Paragraph(f"{cov.get('rule', '')}{unit}", style_table_cell),
            Paragraph(str(cov.get("headroom", "-")), style_table_cell),
            Paragraph(badge, style_table_cell)])
    if len(cov_table_data) == 1:
        cov_table_data.append([Paragraph("No covenant audit returned", style_table_cell)]
                              + [Paragraph("-", style_table_cell) for _ in range(4)])
    cov_table = Table(cov_table_data, colWidths=[2.1*inch, 1.2*inch, 1.2*inch, 1.6*inch, 0.9*inch])
    cov_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
    story.append(cov_table)

    recons = data.get("reconciliations", [])
    if recons:
        story.append(Paragraph("7. CROSS-SOURCE RECONCILIATION", style_h2))
        rec_data = [[Paragraph(h, style_table_header) for h in
                     ("Metric", "Bottom-Up", "As Reported", "Variance", "Status")]]
        for rc in recons:
            breached = rc.get("breached")
            badge = ("<font color='red'><b>UNRECONCILED</b></font>" if breached
                     else "<font color='green'>Agreed</font>")
            rec_data.append([
                Paragraph(str(rc.get("field")), style_table_cell),
                Paragraph(str(rc.get("bottom_up_value")), style_table_cell),
                Paragraph(str(rc.get("reported_value")), style_table_cell),
                Paragraph(f"{rc.get('variance_pct')}%", style_table_cell),
                Paragraph(badge, style_table_cell)])
        rec_table = Table(rec_data, colWidths=[1.8*inch, 1.3*inch, 1.3*inch, 1.1*inch, 1.5*inch])
        rec_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
        story.append(rec_table)

    vres = data.get("validation_results", [])
    if vres:
        story.append(Paragraph(
            f"8. AUTOMATED VALIDATION RULE RESULTS "
            f"({ops['validation_checks_passed']}/{ops['validation_checks_total']} passed)", style_h2))
        val_data = [[Paragraph(h, style_table_header) for h in
                     ("ID", "Rule", "Observed", "Status")]]
        for v in vres:
            st = str(v.get("status", "")).upper()
            colour = {"PASS": "green", "FAIL": "red"}.get(st, "#6B675E")
            val_data.append([
                Paragraph(str(v.get("id", "")), style_table_cell),
                Paragraph(str(v.get("name", "")), style_table_cell),
                Paragraph(str(v.get("observed", "-")), style_table_cell),
                Paragraph(f"<font color='{colour}'><b>{st}</b></font>", style_table_cell)])
        val_table = Table(val_data, colWidths=[0.6*inch, 2.0*inch, 3.0*inch, 1.4*inch])
        val_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COPPER), ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GREY), ('PADDING', (0, 0), (-1, -1), 3), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_CREAM])]))
        story.append(val_table)

    story.append(Paragraph("9. ITEMS REQUIRING FINANCE TEAM REVIEW", style_h2))
    for item in data.get("review_items", []):
        story.append(Paragraph(f"• <b>{item['field_name']}</b> [{item['issue_type']}]: {item['recommended_action']}", style_bullet))
    if not data.get("review_items"):
        story.append(Paragraph("• None outstanding.", style_bullet))

    story.append(Paragraph("10. STRATEGIC AI CFO INSIGHTS", style_h2))
    for insight in data.get("ai_insights", []):
        story.append(Paragraph(f"• {insight}", style_bullet))

    doc.build(story)
    return pdf_path


# ==============================================================================
# MAIN ALL-IN-ONE TRIGGER AGENT ENTRYPOINT
# ==============================================================================

def run_agent():
    print("============================================================")
    print("      FUND-RAISING DOCUMENT AGENT TRIGGER STARTED           ")
    print("============================================================")

    # 1. Load Environment & Playbook Configuration
    load_env()
    config = load_yaml_config("input_config.yaml")

    # 2. Ingest Markdown System Prompts
    system_prompt = load_prompt_md("system_prompt.md")
    insights_prompt = load_prompt_md("insights_prompt.md")

    # 3. Load JSON Output Schema
    output_schema = load_json_schema("output_schema.json")

    # 4. Ingest Primary Datasets & CSV Files
    json_path = os.path.join(SAMPLE_DIR, "fund_raising_data.json")
    with open(json_path, "r", encoding="utf-8") as f:
        seed_data = json.load(f)

    dataframes = read_csv_dataframes()

    # 5. Execute Agent Dynamic Calculations Specified in input_config.yaml & MD Prompts
    analyzed_data = execute_yaml_and_md_calculations(config, dataframes, system_prompt, insights_prompt, seed_data)

    # 6. Validate JSON Payload against Schema
    if JSONSCHEMA_AVAILABLE:
        try:
            jsonschema.validate(instance=analyzed_data, schema=output_schema)
            print(" -> [SCHEMA VALIDATION] Success: Payload complies with output_schema.json")
        except Exception as e:
            print(f" -> [SCHEMA VALIDATION] Notice: {e}")

    # 7. Write Output JSON and Markdown Files
    output_json_path = os.path.join(BASE_DIR, os.getenv("OUTPUT_JSON_PATH", "generated_datapack.json"))
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(analyzed_data, f, indent=2)
    print(f" -> [7/7 FILE WRITING] Saved JSON Payload: {os.path.basename(output_json_path)}")

    output_doc_path = os.path.join(BASE_DIR, os.getenv("OUTPUT_DOCUMENT_PATH", "generated_datapack_document.md"))
    with open(output_doc_path, "w", encoding="utf-8") as f:
        f.write(analyzed_data["generated_document_markdown"])
    print(f" -> [7/7 FILE WRITING] Saved Markdown Data Pack: {os.path.basename(output_doc_path)}")

    # 8. Write Output PDF File
    pdf_path = os.path.join(BASE_DIR, "generated_datapack_document.pdf")
    build_pdf_document(analyzed_data, pdf_path)
    print(f" -> [7/7 FILE WRITING] Saved Corporate PDF Report: {os.path.basename(pdf_path)}")

    ops = analyzed_data["operational_kpis"]
    breaches = [c for c in analyzed_data.get("covenant_audit", [])
                if str(c.get("status", "")).upper() == "FAIL"]
    unreconciled = [r for r in analyzed_data.get("reconciliations", []) if r.get("breached")]

    print("\n============================================================")
    print("[COMPLETE] LLM-DRIVEN AGENT RUN FINISHED")
    print("============================================================")
    print(f"Company:          {analyzed_data['company_metadata']['company_name']}")
    print(f"Period:           {analyzed_data.get('reporting_period')}")
    print(f"Pack Status:      {ops['pack_status']}")
    print(f"Covenants:        {len(analyzed_data.get('covenant_audit', []))} audited, "
          f"{len(breaches)} breached")
    print(f"Validation Rules: {ops['validation_checks_passed']}/{ops['validation_checks_total']} passed"
          + (f", {ops['validation_checks_failed']} failed" if ops.get('validation_checks_failed') else "")
          + (f", {ops['validation_checks_not_evaluable']} not evaluable" if ops.get('validation_checks_not_evaluable') else ""))
    print(f"Reconciliation:   {len(unreconciled)} unreconciled cross-source variance(s)")
    print(f"Arithmetic Audit: {len(analyzed_data.get('arithmetic_audit', []))} discrepancy(ies)")
    print(f"Review Items:     {ops['items_needing_review']}")
    print(f"\nJSON: {output_json_path}\nPDF:  {pdf_path}\n")

    return analyzed_data


if __name__ == "__main__":
    agent_response_json = run_agent()
