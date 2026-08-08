"""
==============================================================================
INVESTOR / BOARD REPORTING AGENT - MAIN PIPELINE
==============================================================================
Atlas Hub > Finance & Treasury > Investor Reporting

Runs once per quarter, triggered by quarter-close, and produces the first-draft
commentary pack the finance team refines and the CFO approves.

    1. ENV        .env - Groq credentials and model rate limits
    2. CONFIG     config/input_config.yaml - every technical aspect: KPI catalog,
                  comparators, attribution rules, risk rules, guardrails,
                  validation catalog, and the frontend panel contract
    3. PROMPTS    prompts/*.md - every narrative instruction: persona, drafting
                  playbook, house style
    4. FEEDS      sample_data/ - the five feeds from Section 5 of the design note
    5. COMPUTE    metrics_engine - all arithmetic, attribution, risk items,
                  reconciliation, validation, independent audit
    6. DRAFT      llm_engine - Groq drafts the five commentary cards, and three
                  guardrail layers check the prose against the figures
    7. ASSEMBLE   payload matching config/output_schema.json
    8. RENDER     output/ - JSON, Markdown, PDF, and a standalone
                  reporting-assistant screen mirroring the Atlas Hub layout

No financial figure is hardcoded anywhere in this file.
==============================================================================
"""

import os
import sys
import json
import site

from .paths import ROOT, SCHEMA_FILE
from . import metrics_engine
from . import llm_engine
from . import report_renderer

for _p in (os.path.join(ROOT, "venv", "Lib", "site-packages"),
           site.getusersitepackages()):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except Exception:
    JSONSCHEMA_AVAILABLE = False


# ==============================================================================
# REVIEW QUEUE
# ==============================================================================

def build_review_queue(facts, ai_commentary):
    """
    Everything the finance team must resolve before the CFO signs off, gathered
    from every check in the pipeline. Nothing unresolved is hidden.
    """
    queue, seq = [], 0

    def add(origin, issue_type, severity, action, section_id=None, field=None):
        nonlocal seq
        seq += 1
        queue.append({
            "id": f"RQ-{seq:03d}", "origin": origin, "section_id": section_id,
            "field": field, "issue_type": issue_type, "severity": severity,
            "recommended_action": action,
        })

    for s in ai_commentary.get("sections", []):
        for f in s.get("flags", []):
            add("guardrail", f["type"], f.get("severity", "Medium"),
                f["message"] + (f" Excerpt: \"{f['excerpt']}\"" if f.get("excerpt") else ""),
                section_id=s["id"])

    for rc in facts.get("reconciliations", []):
        if rc.get("breached"):
            add("reconciliation", "Conflict", "High",
                f"{rc['label']}: the segment ledger gives {rc['bottom_up_value']} but the "
                f"reporting system gives {rc['reported_value']}, a variance of "
                f"{rc['variance_pct']}% against a {rc['tolerance_pct']}% tolerance. "
                f"Confirm which figure the deck should carry before circulation.",
                field=rc["field"])

    for v in facts.get("validation_results", []):
        if v["status"] == "FAIL":
            add("validation", "Breach", v.get("severity", "High"),
                f"{v['id']} {v['name']} failed: {v['observed']}.", field=v["id"])
        elif v["status"] == "NOT_EVALUABLE":
            add("validation", "Missing", "Low",
                f"{v['id']} {v['name']} could not be evaluated: {v['observed']}.",
                field=v["id"])

    for key, att in (facts.get("attribution") or {}).items():
        if not att.get("sufficient"):
            add("attribution", "Unexplained", "Medium",
                f"Named segment drivers explain only {att['explained_share_pct']}% of the "
                f"movement in {att['label']}. Supply the granular data needed to attribute "
                f"the remainder, or confirm the commentary should leave it unexplained.",
                field=key)

    for disc in facts.get("arithmetic_audit", []):
        add("arithmetic_audit", "Conflict", "High",
            f"{disc['metric']}: the table shows {disc['reported']} but an independent "
            f"recompute from the raw feed gives {disc['expected']} ({disc['note']}). "
            f"Verify before circulation.", field=disc["metric"])

    order = {"High": 0, "Medium": 1, "Low": 2}
    queue.sort(key=lambda q: order.get(q["severity"], 9))
    return queue


# ==============================================================================
# PAYLOAD ASSEMBLY
# ==============================================================================

def assemble_payload(config, facts, ai_commentary, guardrail_report, llm_meta):
    readiness = metrics_engine.compute_report_readiness(
        config, facts["kpi_comparison"], ai_commentary["sections"],
        facts["risk_attention_items"])
    time_saved = metrics_engine.compute_time_saved(
        config, ai_commentary["sections"], facts["risk_attention_items"])

    meta = dict(facts["meta"])
    meta["degraded_run"] = bool(llm_meta.get("degraded"))

    payload = {
        "meta": meta,
        "kpi_comparison": facts["kpi_comparison"],
        "budget_variance": facts["budget_variance"],
        "stage_movement": facts["stage_movement"],
        "risk_attention_items": facts["risk_attention_items"],
        "source_traceability": facts["source_traceability"],
        "report_readiness": readiness,
        "ai_commentary": ai_commentary,
        "attribution": facts["attribution"],
        "reconciliations": facts["reconciliations"],
        "validation_results": facts["validation_results"],
        "one_off_items": facts["one_off_items"],
        "derived_metrics": facts["derived_metrics"],
        "review_queue": build_review_queue(facts, ai_commentary),
        "guardrail_report": guardrail_report,
        "arithmetic_audit": facts["arithmetic_audit"],
        "time_saved": time_saved,
        "llm_metadata": llm_meta,
    }
    return payload


# ==============================================================================
# ENTRYPOINT
# ==============================================================================

def run_agent(tone=None, audience=None, verbose=True):
    print("=" * 72)
    print("   ATLAS HUB - INVESTOR / BOARD REPORTING AGENT")
    print("=" * 72)

    # ---- 1. environment -----------------------------------------------------
    if llm_engine.load_env():
        provider, model = llm_engine.resolve_provider()
        print(f" -> [1/8 ENV]      .env loaded | LLM provider: {provider} ({model})")
    else:
        print(" -> [1/8 ENV]      no .env found - the run will use the deterministic fallback")

    # ---- 2. technical configuration -----------------------------------------
    config = metrics_engine.load_config()
    print(f" -> [2/8 CONFIG]   config/input_config.yaml | playbook '{config.get('playbook_name')}' "
          f"| {len(config.get('kpi_catalog', {}))} KPIs, "
          f"{len(config.get('commentary_sections', []))} commentary sections, "
          f"{len(config.get('risk_rules', []))} risk rules, "
          f"{len(config.get('validation_rules', []))} validation rules")

    # ---- 3. narrative instructions ------------------------------------------
    system_prompt = metrics_engine.load_prompt("system_prompt.md")
    drafting_prompt = metrics_engine.load_prompt("drafting_prompt.md")
    style_guide = metrics_engine.load_prompt("style_guide.md")
    print(f" -> [3/8 PROMPTS]  prompts/system_prompt.md ({len(system_prompt):,} chars), "
          f"drafting_prompt.md ({len(drafting_prompt):,}), "
          f"style_guide.md ({len(style_guide):,})")

    # ---- 4. data feeds ------------------------------------------------------
    feeds = metrics_engine.load_feeds(config)
    for key in ("quarterly_metrics", "segment_breakdown", "one_off_items",
                "forecast_plan", "budget_targets"):
        df = feeds.get(key)
        print(f" -> [4/8 FEED]     {key:<20} "
              + (f"{len(df)} rows" if df is not None else "not supplied"))
    decks = len((feeds.get("past_commentary") or {}).get("decks", []))
    print(f" -> [4/8 FEED]     past_commentary      {decks} approved deck(s) as style reference")

    # ---- 5. deterministic computation ---------------------------------------
    print("\n -> [5/8 METRICS]  Computing every figure in Python before the model is called...")
    facts = metrics_engine.build_facts(config, feeds, verbose=verbose)

    # ---- 6. commentary drafting ---------------------------------------------
    tones = config.get("tone_presets", {})
    tone = tone or os.getenv("COMMENTARY_TONE") or tones.get("default", "board_formal")
    audience = audience or os.getenv("DECK_AUDIENCE") or "board"
    facts["meta"]["audience"] = audience

    print(f"\n -> [6/8 DRAFTING] Delegating the prose to the LLM "
          f"(tone: {tone}, audience: {audience})...")
    try:
        ai_commentary, guardrail_report, llm_meta = llm_engine.run_commentary_drafting(
            config, facts, feeds, system_prompt, drafting_prompt, style_guide,
            tone_id=tone, audience=audience, verbose=verbose)
    except (llm_engine.LLMUnavailable, RuntimeError) as e:
        print(f"    [LLM] Unavailable: {e}")
        ai_commentary, guardrail_report = llm_engine.fallback_commentary(
            config, facts, audience=audience, verbose=verbose)
        llm_meta = {"provider": "none (fallback)", "model": "deterministic templates",
                    "passes": 0, "repair_rounds": 0, "latency_s": 0.0,
                    "degraded": True, "reason": str(e)}

    gr = guardrail_report
    print(f"    [GUARDRAIL] {gr['sections_clean']}/{gr['sections_drafted']} sections clean, "
          f"{gr['total_flags']} flag(s), {gr['ungrounded_figures']} ungrounded figure(s) "
          f"of {gr['figures_checked']} checked")
    if gr["flags_by_type"]:
        for t, n in sorted(gr["flags_by_type"].items(), key=lambda kv: -kv[1]):
            print(f"       - {t}: {n}")

    # ---- 7. payload ---------------------------------------------------------
    payload = assemble_payload(config, facts, ai_commentary, guardrail_report, llm_meta)

    if JSONSCHEMA_AVAILABLE and os.path.exists(SCHEMA_FILE):
        with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
            schema = json.load(f)
        try:
            jsonschema.validate(instance=payload, schema=schema)
            print("\n -> [7/8 SCHEMA]   Payload complies with config/output_schema.json")
        except Exception as e:
            print(f"\n -> [7/8 SCHEMA]   Notice: {str(e)[:300]}")
    else:
        print("\n -> [7/8 SCHEMA]   jsonschema not installed - validation skipped")

    # ---- 8. outputs ---------------------------------------------------------
    outputs = report_renderer.write_all(payload, config)
    for label, path in outputs.items():
        print(f" -> [8/8 OUTPUT]   {label:<22} {os.path.basename(path)}")

    _print_summary(payload)
    return payload


def _print_summary(payload):
    m, ops = payload["meta"], payload["report_readiness"]
    gr, ts = payload["guardrail_report"], payload["time_saved"]
    risks = payload["risk_attention_items"]

    print("\n" + "=" * 72)
    print("[COMPLETE] FIRST-DRAFT COMMENTARY PACK READY FOR FINANCE TEAM REVIEW")
    print("=" * 72)
    print(f"Company:        {m['company_name']}")
    print(f"Quarter:        {m['reporting_quarter']}  "
          f"(QoQ vs {m['prior_quarter']}, YoY vs {m['yoy_quarter']})")
    print(f"Audience:       {m['audience']}   Tone: {payload['ai_commentary']['tone_label']}")
    print(f"Engine:         {payload['llm_metadata']['provider']} / "
          f"{payload['llm_metadata']['model']}"
          + ("  [DEGRADED RUN]" if m["degraded_run"] else ""))

    print("\nHeadline KPIs")
    for r in payload["kpi_comparison"]:
        mark = {"favourable": "+", "adverse": "!", "neutral": " "}[r["qoq_sentiment"]]
        print(f"  {mark} {r['label']:<22} {r['current_actual_display']:>10}   "
              f"QoQ {r['qoq_change_display']:>8}   YoY {r['yoy_change_display']:>8}   "
              f"fcst {r['forecast_display']:>8}")

    bv = payload["budget_variance"]
    print(f"\nVariance to budget ({bv['label']}): {bv['display']}  [{bv['sentiment']}]")

    print(f"\nRisk / attention items ({len(risks)})")
    for r in risks:
        print(f"  [{r['severity']:<6}] {r['message']}")

    print(f"\nCommentary sections ({len(payload['ai_commentary']['sections'])})")
    for s in payload["ai_commentary"]["sections"]:
        state = "clean" if not s["flags"] else f"{len(s['flags'])} flag(s)"
        badge = (s.get("delta_badge") or {}).get("text", "")
        print(f"  - {s['title']:<28} {badge:<18} {s['word_count']:>3}w  {state}")

    print(f"\nReport readiness: {ops['score']}/{ops['total']}")
    for c in ops["counters"]:
        print(f"  - {c['label']:<22} {c['display']:>8}   ({c['owner']}-owned)")

    print(f"\nGuardrails:     {gr['sections_clean']}/{gr['sections_drafted']} clean, "
          f"{gr['total_flags']} flag(s), {gr['repair_rounds_used']} repair round(s)")
    print(f"Review queue:   {len(payload['review_queue'])} item(s) for the finance team")
    print(f"Time saved:     {ts['hours_saved']} hours "
          f"({ts['manual_minutes']:.0f} min manual vs {ts['assisted_minutes']:.0f} min review)")
    print()


if __name__ == "__main__":
    run_agent()
