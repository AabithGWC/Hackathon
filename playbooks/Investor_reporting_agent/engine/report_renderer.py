"""
==============================================================================
REPORT RENDERER - Investor / Board Reporting Agent
==============================================================================
Turns the payload into the four artefacts the finance team actually uses:

  generated_investor_report.json   the frontend contract (output_schema.json)
  generated_commentary.md          the commentary pack for review
  generated_commentary.pdf         a printable board-ready version
  reporting_assistant.html         a standalone reporting-assistant screen that
                                   mirrors the Atlas Hub layout - key metrics
                                   alongside editable draft commentary, which is
                                   the deliverable named in the use case

Nothing here computes a financial figure; every number comes from the payload.
==============================================================================
"""

import os
import json
import html

from .paths import ensure_output_dir, resolve

REPORTLAB_AVAILABLE = False
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table,
                                    TableStyle, HRFlowable, Spacer, PageBreak)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except Exception:
    pass


COPPER = "#C86537"
CHARCOAL = "#1F1E1B"
CREAM = "#FAF8F3"
BORDER = "#E6E2D8"
MUTED = "#6B675E"
GREEN = "#5C7C52"
RED = "#B4442E"


def _sent_colour(sentiment):
    return {"favourable": GREEN, "adverse": RED}.get(sentiment, MUTED)


# ==============================================================================
# MARKDOWN
# ==============================================================================

def build_markdown(payload):
    m = payload["meta"]
    sym = m.get("currency_symbol", "₹")
    out = []

    out.append(f"# {m['company_name']} — Investor / Board Commentary Pack")
    out.append(f"**Quarter**: {m['reporting_quarter']}  |  "
               f"**Comparators**: {m['prior_quarter']} (QoQ), {m['yoy_quarter']} (YoY)  |  "
               f"**Audience**: {m['audience']}  |  "
               f"**Standard**: {m['accounting_standard']} / {m['ecl_standard']}")
    out.append(f"*{m['confidentiality']}*")
    out.append("")
    out.append("> **This is a first draft.** Every section below is for the finance team to "
               "refine and the CFO to approve before it enters the deck. Sections marked "
               "`NEEDS REVIEW` failed an automated check — the specific reason is listed "
               "against each one.")
    out.append("")
    out.append("---\n")

    # ---- readiness ----------------------------------------------------------
    rr = payload["report_readiness"]
    out.append(f"## Report Readiness — {rr['score']} / {rr['total']}")
    out.append("| Item | Progress | Owner |")
    out.append("| :--- | :---: | :---: |")
    for c in rr["counters"]:
        out.append(f"| {c['label']} | {c['display']} | {c['owner']} |")
    out.append("")

    # ---- comparison table ---------------------------------------------------
    out.append("## Quarter-over-Quarter Comparison")
    out.append(f"*Historical actuals and approved plan figures*\n")
    out.append("| Metric | Prior Actual | Current Actual | Forecast | QoQ Change | YoY Change |")
    out.append("| :--- | ---: | ---: | ---: | ---: | ---: |")
    for r in payload["kpi_comparison"]:
        def mark(disp, sent):
            return f"**{disp}**" if sent == "adverse" else disp
        out.append(f"| {r['label']} | {r['prior_actual_display']} | "
                   f"**{r['current_actual_display']}** | {r['forecast_display']} | "
                   f"{mark(r['qoq_change_display'], r['qoq_sentiment'])} | "
                   f"{mark(r['yoy_change_display'], r['yoy_sentiment'])} |")
    bv = payload["budget_variance"]
    out.append(f"\n**Variance to budget** ({bv['label']}, on {bv['metric_key']}): "
               f"{bv['display']}\n")

    # ---- stage movement -----------------------------------------------------
    sm = payload["stage_movement"]
    out.append("## Stage Movement")
    out.append(f"*{sm['subtitle']}*\n")
    out.append("| Stage | Current | Share | Forecast | QoQ Change |")
    out.append("| :--- | ---: | ---: | ---: | ---: |")
    for r in sm["rows"]:
        share = f"{r['current_share_pct']:.1f}%" if r.get("current_share_pct") else "—"
        chg = f"{r['qoq_change']:+,.1f}" if r.get("qoq_change") is not None else "—"
        out.append(f"| {r['label']} | {r['current_display']} | {share} | "
                   f"{r['forecast_display']} | {chg} |")
    nf = sm["net_flow"]
    out.append(f"\n**{nf['label']}**: {nf['display']}\n")

    # ---- commentary ---------------------------------------------------------
    out.append("---\n")
    out.append("## AI-Generated Commentary")
    out.append("*Editable financial narratives drafted for deck inclusion*\n")
    for s in payload["ai_commentary"]["sections"]:
        badge = (s.get("delta_badge") or {}).get("text", "")
        status = "NEEDS REVIEW" if s["flags"] else "DRAFT"
        out.append(f"### {s['order']}. {s['title']}  `{badge}`  — *{status}* "
                   f"({s['word_count']} words)")
        out.append("")
        out.append(s["body"] or "_No draft produced for this section._")
        out.append("")
        if s["flags"]:
            out.append("**Automated checks flagged:**")
            for f in s["flags"]:
                ex = f" — excerpt: \"{f['excerpt']}\"" if f.get("excerpt") else ""
                out.append(f"- `{f['type']}` [{f['severity']}] {f['message']}{ex}")
            out.append("")
        if s.get("unexplained_movements"):
            out.append("**The agent could not attribute:**")
            for u in s["unexplained_movements"]:
                out.append(f"- {u}")
            out.append("")

    # ---- risk items ---------------------------------------------------------
    out.append("---\n")
    out.append(f"## Risk / Attention Items ({len(payload['risk_attention_items'])})")
    out.append("| Severity | Category | Item |")
    out.append("| :--- | :--- | :--- |")
    for r in payload["risk_attention_items"]:
        out.append(f"| {r['severity']} | {r['category']} | {r['message']} |")
    out.append("")

    # ---- attribution --------------------------------------------------------
    out.append("## Driver Attribution")
    out.append("*The only evidence the agent is permitted to use for a causal claim.*\n")
    for key, att in (payload.get("attribution") or {}).items():
        flag = "sufficient" if att["sufficient"] else "**INSUFFICIENT — flagged**"
        out.append(f"### {att['label']} — total movement {att['total_change']} "
                   f"{att['unit']} ({att['explained_share_pct']}% explained, {flag})")
        out.append(f"*{att['description']}*\n")
        has_split = any(c.get("stock_effect") is not None for c in att["contributors"])
        if has_split:
            out.append("| Segment | Change | Share | Stock effect | Denominator effect |")
            out.append("| :--- | ---: | ---: | ---: | ---: |")
            for c in att["contributors"]:
                out.append(f"| {c['label']} | {c['change']} | "
                           f"{c.get('share_of_change_pct', '—')}% | "
                           f"{c.get('stock_effect', '—')} | "
                           f"{c.get('denominator_effect', '—')} |")
        else:
            out.append("| Segment | Prior | Current | Change | Share of movement |")
            out.append("| :--- | ---: | ---: | ---: | ---: |")
            for c in att["contributors"]:
                out.append(f"| {c['label']} | {c['prior']} | {c['current']} | "
                           f"{c['change']} | {c.get('share_of_change_pct', '—')}% |")
        out.append("")

    # ---- one-offs -----------------------------------------------------------
    if payload.get("one_off_items"):
        out.append("## Exceptional / Non-Recurring Items")
        out.append("| Item | Description | Metric | Impact | Nature |")
        out.append("| :--- | :--- | :--- | ---: | :--- |")
        for o in payload["one_off_items"]:
            out.append(f"| {o['item_id']} | {o['description']} | {o['metric_affected']} | "
                       f"{sym}{o['impact_value']} Cr | {o['nature']} |")
        d = payload.get("derived_metrics", {})
        if d.get("pat_ex_oneoff_cr") is not None:
            out.append(f"\n**PAT excluding exceptional items**: {sym}{d['pat_ex_oneoff_cr']} Cr "
                       f"({d.get('pat_ex_oneoff_qoq_pct')}% QoQ) against a reported "
                       f"{sym}{next(r['current_actual'] for r in payload['kpi_comparison'] if r['metric_key'] == 'pat_cr')} Cr.")
        if d.get("aum_organic_growth_cr") is not None:
            out.append(f"\n**Organic AUM growth**: {sym}{d['aum_organic_growth_cr']} Cr "
                       f"({d.get('aum_organic_growth_pct')}%) of a total "
                       f"{sym}{d['aum_growth_total_cr']} Cr increase; "
                       f"{sym}{d['aum_inorganic_cr']} Cr was acquired.")
        out.append("")

    # ---- reconciliation & validation ---------------------------------------
    out.append("## Cross-Source Reconciliation")
    out.append("| Field | Segment Ledger | Reporting System | Variance | Tolerance | Status |")
    out.append("| :--- | ---: | ---: | ---: | ---: | :---: |")
    for rc in payload["reconciliations"]:
        badge = "**UNRECONCILED**" if rc.get("breached") else "Agreed"
        out.append(f"| {rc['label']} | {rc['bottom_up_value']} | {rc['reported_value']} | "
                   f"{rc['variance_pct']}% | {rc['tolerance_pct']}% | {badge} |")
    out.append("")

    vr = payload["validation_results"]
    passed = sum(1 for v in vr if v["status"] == "PASS")
    out.append(f"## Validation Rules ({passed}/{len(vr)} passed)")
    out.append("| ID | Rule | Observed | Status |")
    out.append("| :--- | :--- | :--- | :---: |")
    for v in vr:
        out.append(f"| {v['id']} | {v['name']} | {v['observed']} | {v['status']} |")
    out.append("")

    # ---- review queue -------------------------------------------------------
    out.append(f"## Review Queue — Finance Team ({len(payload['review_queue'])})")
    if payload["review_queue"]:
        out.append("| Severity | Origin | Section / Field | Action |")
        out.append("| :--- | :--- | :--- | :--- |")
        for q in payload["review_queue"]:
            where = q.get("section_id") or q.get("field") or "—"
            out.append(f"| {q['severity']} | {q['origin']} | {where} | "
                       f"{q['recommended_action']} |")
    else:
        out.append("Nothing outstanding.")
    out.append("")

    # ---- provenance ---------------------------------------------------------
    gr, ts, lm = payload["guardrail_report"], payload["time_saved"], payload["llm_metadata"]
    out.append("---\n")
    out.append("## How This Draft Was Produced")
    out.append(f"- **Figures**: computed deterministically in Python from the source feeds "
               f"before the model was called. {len(payload['arithmetic_audit'])} discrepancy(ies) "
               f"against an independent recompute.")
    out.append(f"- **Prose**: {lm['provider']} / {lm['model']}, {lm['passes']} pass(es), "
               f"{lm['repair_rounds']} guardrail repair round(s), {lm.get('latency_s', 0)}s.")
    out.append(f"- **Guardrails**: {gr['figures_checked']} figures checked against the fact "
               f"base, {gr['ungrounded_figures']} ungrounded. "
               f"{gr['sections_clean']}/{gr['sections_drafted']} sections passed every check.")
    out.append(f"- **Time saved**: {ts['hours_saved']} hours. {ts['basis']}")
    out.append(f"- **Sign-off required**: finance team review, then CFO approval. "
               f"This draft is not board-ready until both are complete.")
    out.append("")
    return "\n".join(out)


# ==============================================================================
# HTML REPORTING ASSISTANT SCREEN
# ==============================================================================

def build_html(payload):
    """
    The screen named in the use case: key financial metrics for the quarter
    alongside auto-drafted commentary, editable before being pulled into the
    final deck. Self-contained - no external assets.
    """
    m = payload["meta"]
    sym = m.get("currency_symbol", "₹")
    e = html.escape

    # ---- comparison rows ----
    kpi_rows = []
    for r in payload["kpi_comparison"]:
        kpi_rows.append(f"""
        <tr>
          <td class="metric-name">{e(r['label'])}<span class="def" title="{e(r['definition'])}">?</span></td>
          <td class="num">{e(r['prior_actual_display'])}</td>
          <td class="num strong">{e(r['current_actual_display'])}</td>
          <td class="num muted">{e(r['forecast_display'])}</td>
          <td class="num" style="color:{_sent_colour(r['qoq_sentiment'])}">{e(r['qoq_change_display'])}</td>
          <td class="num" style="color:{_sent_colour(r['yoy_sentiment'])}">{e(r['yoy_change_display'])}</td>
        </tr>""")

    # ---- stage rows ----
    sm = payload["stage_movement"]
    stage_rows = "".join(f"""
        <tr>
          <td>{e(r['label'])}</td>
          <td class="num">{e(r['current_display'])}</td>
          <td class="num muted">{e(r['forecast_display'])}</td>
        </tr>""" for r in sm["rows"]) or \
        f'<tr><td colspan="3" class="empty">{e(sm.get("empty_message", "No records found"))}</td></tr>'

    # ---- risk items ----
    risk_items = "".join(f"""
        <div class="risk {r['severity'].lower()}">
          <span class="risk-icon">!</span>
          <div><div class="risk-msg">{e(r['message'])}</div>
          <div class="risk-meta">{e(r['severity'])} &middot; {e(r['category'])}</div></div>
        </div>""" for r in payload["risk_attention_items"])

    # ---- traceability ----
    trace = "".join(f"""
        <div class="trace">
          <div><div class="trace-label">{e(t['label'])}</div>
          <div class="trace-sys">{e(t['system'])}</div></div>
          <span class="tick {t['status']}">{'&#10003;' if t['status'] == 'verified' else '&#33;'}</span>
        </div>""" for t in payload["source_traceability"])

    # ---- readiness donut ----
    rr = payload["report_readiness"]
    pct = max(0.0, min(1.0, rr["score"] / rr["total"] if rr["total"] else 0))
    circ = 2 * 3.14159 * 54
    dash = f"{circ * pct:.1f} {circ:.1f}"
    counters = "".join(f"""
        <div class="counter"><span>{e(c['label'])}</span>
        <b>{e(c['display'])}</b></div>""" for c in rr["counters"])

    # ---- tone select ----
    tone_opts = "".join(
        f'<option value="{e(t["id"])}"'
        + (" selected" if t["id"] == payload["ai_commentary"]["tone"] else "")
        + f'>{e(t["label"])}</option>'
        for t in payload["ai_commentary"]["tone_options"])

    # ---- commentary cards ----
    cards = []
    for s in payload["ai_commentary"]["sections"]:
        badge = s.get("delta_badge") or {}
        badge_html = (f'<span class="badge {badge.get("sentiment", "neutral")}">'
                      f'{e(badge.get("text", ""))}</span>') if badge.get("text") else ""
        flags_html = ""
        if s["flags"]:
            items = "".join(
                f'<li><b>{e(f["type"])}</b> [{e(f["severity"])}] {e(f["message"])}'
                + (f' <span class="ex">&ldquo;{e(f["excerpt"])}&rdquo;</span>'
                   if f.get("excerpt") else "") + '</li>'
                for f in s["flags"])
            flags_html = (f'<div class="flags"><div class="flags-head">'
                          f'Automated checks flagged {len(s["flags"])} item(s) '
                          f'&mdash; review before accepting</div><ul>{items}</ul></div>')
        unexp = ""
        if s.get("unexplained_movements"):
            items = "".join(f"<li>{e(u)}</li>" for u in s["unexplained_movements"])
            unexp = (f'<div class="unexp"><div class="flags-head">The agent could not '
                     f'attribute these movements</div><ul>{items}</ul></div>')
        status_cls = "needs" if s["flags"] else "ok"
        status_txt = "Needs review" if s["flags"] else "Draft"
        cards.append(f"""
        <div class="ccard" data-section="{e(s['id'])}">
          <div class="ccard-head">
            <div class="ccard-title">{e(s['title'])} {badge_html}</div>
            <div class="ccard-actions">
              <span class="wc"><span class="wcount">{s['word_count']}</span> words</span>
              <span class="status {status_cls}">{status_txt}</span>
              <button class="accept" onclick="accept(this)">Accept</button>
            </div>
          </div>
          <textarea class="cbody" oninput="recount(this)">{e(s['body'])}</textarea>
          {flags_html}{unexp}
        </div>""")

    degraded = ("" if not m.get("degraded_run") else
                '<div class="degraded">Degraded run &mdash; the drafting model was '
                'unreachable and this commentary was produced from deterministic '
                'templates. The figures are unaffected.</div>')

    bv = payload["budget_variance"]
    gr, ts = payload["guardrail_report"], payload["time_saved"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(m['company_name'])} &mdash; Investor Reporting {e(m['reporting_quarter'])}</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;background:{CREAM};color:{CHARCOAL};
       font:14px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
  .wrap{{display:flex;min-height:100vh}}
  .side{{width:230px;flex:0 0 230px;background:#fff;border-right:1px solid {BORDER};padding:18px 14px}}
  .brand{{display:flex;gap:10px;align-items:center;margin-bottom:26px}}
  .logo{{width:30px;height:30px;border-radius:50%;background:{COPPER};color:#fff;
         display:flex;align-items:center;justify-content:center;font-weight:700}}
  .brand b{{display:block;font-size:14px}} .brand span{{font-size:11px;color:{MUTED}}}
  .navlabel{{font-size:10px;letter-spacing:.09em;color:{MUTED};text-transform:uppercase;margin:0 0 10px 8px}}
  .nav a{{display:block;padding:8px 12px;border-radius:8px;color:{CHARCOAL};
          text-decoration:none;font-size:13px;margin-bottom:2px}}
  .nav a.on{{background:{COPPER};color:#fff;font-weight:600}}
  .main{{flex:1;padding:22px 26px;min-width:0}}
  .top{{display:flex;justify-content:space-between;align-items:flex-end;
        flex-wrap:wrap;gap:12px;margin-bottom:18px}}
  h1{{font-family:Georgia,"Times New Roman",serif;font-size:24px;margin:0 0 4px}}
  .sub{{color:{MUTED};font-size:12.5px}}
  .cols{{display:grid;grid-template-columns:minmax(0,2.1fr) minmax(280px,1fr);gap:18px;align-items:start}}
  .card{{background:#fff;border:1px solid {BORDER};border-radius:12px;padding:16px 18px;margin-bottom:16px}}
  .card h2{{font-family:Georgia,serif;font-size:17px;margin:0 0 2px}}
  .card .cap{{color:{MUTED};font-size:12px;margin-bottom:12px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:right;font-size:10.5px;letter-spacing:.06em;color:{MUTED};
      text-transform:uppercase;font-weight:600;padding:0 8px 9px;border-bottom:1px solid {BORDER}}}
  th:first-child{{text-align:left}}
  td{{padding:11px 8px;border-bottom:1px solid #F1EEE7}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  td.strong{{font-weight:700}} td.muted{{color:{MUTED}}}
  .metric-name{{font-weight:500}}
  .def{{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
        margin-left:6px;border:1px solid {BORDER};border-radius:50%;font-size:9px;
        color:{MUTED};cursor:help;vertical-align:middle}}
  .varbar{{display:flex;justify-content:space-between;background:{CREAM};
           border:1px solid {BORDER};border-radius:10px;padding:11px 14px;margin-bottom:16px;font-size:13px}}
  .empty{{text-align:center;color:{MUTED};padding:28px 0}}
  .netflow{{display:flex;justify-content:space-between;background:{CREAM};
            border:1px solid {BORDER};border-radius:9px;padding:9px 12px;margin-top:12px;font-size:12.5px}}
  .risk{{display:flex;gap:9px;background:{CREAM};border:1px solid {BORDER};
         border-radius:9px;padding:10px 12px;margin-bottom:8px}}
  .risk-icon{{color:{RED};font-weight:700;flex:0 0 auto}}
  .risk.medium .risk-icon{{color:{COPPER}}} .risk.low .risk-icon{{color:{MUTED}}}
  .risk-msg{{font-size:12.5px;line-height:1.45}}
  .risk-meta{{font-size:10.5px;color:{MUTED};margin-top:3px}}
  .trace{{display:flex;justify-content:space-between;align-items:center;background:{CREAM};
          border:1px solid {BORDER};border-radius:9px;padding:9px 12px;margin-bottom:8px}}
  .trace-label{{font-size:12.5px;font-weight:600}}
  .trace-sys{{font-size:10.5px;color:{MUTED}}}
  .tick{{width:19px;height:19px;border-radius:50%;display:flex;align-items:center;
         justify-content:center;font-size:11px;color:#fff;background:{GREEN}}}
  .tick.conflict,.tick.partial,.tick.unverified{{background:{RED}}}
  .donutwrap{{display:flex;justify-content:center;padding:6px 0 12px}}
  .legend{{display:flex;gap:16px;justify-content:center;font-size:11.5px;color:{MUTED};
           padding-bottom:12px;border-bottom:1px solid {BORDER};margin-bottom:12px}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}}
  .counter{{display:flex;justify-content:space-between;font-size:12.5px;padding:5px 0}}
  .ctools{{display:flex;gap:10px;align-items:center}}
  select,button{{font:inherit;border:1px solid {BORDER};background:#fff;border-radius:8px;
                 padding:7px 12px;cursor:pointer;color:{CHARCOAL}}}
  button.primary{{background:{COPPER};color:#fff;border-color:{COPPER};font-weight:600}}
  .ccard{{border:1px solid {BORDER};border-radius:11px;padding:13px 15px;margin-bottom:13px;background:#fff}}
  .ccard-head{{display:flex;justify-content:space-between;align-items:center;
               gap:10px;flex-wrap:wrap;margin-bottom:9px}}
  .ccard-title{{font-weight:600;font-size:14px}}
  .badge{{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:20px;
          font-size:11px;font-weight:600;background:#EEF3EC;color:{GREEN}}}
  .badge.adverse{{background:#FBEDEA;color:{RED}}}
  .badge.neutral{{background:{CREAM};color:{MUTED}}}
  .ccard-actions{{display:flex;gap:9px;align-items:center;font-size:11.5px;color:{MUTED}}}
  .status{{padding:2px 9px;border-radius:20px;font-weight:600;font-size:11px}}
  .status.ok{{background:#EEF3EC;color:{GREEN}}}
  .status.needs{{background:#FBEDEA;color:{RED}}}
  .accept{{padding:4px 12px;font-size:12px}}
  .accept.done{{background:{GREEN};color:#fff;border-color:{GREEN}}}
  textarea.cbody{{width:100%;min-height:104px;border:1px solid {BORDER};border-radius:9px;
                  padding:11px 13px;font:13.5px/1.6 inherit;resize:vertical;
                  background:{CREAM};color:{CHARCOAL}}}
  .flags,.unexp{{margin-top:10px;border-left:3px solid {RED};background:#FDF6F4;
                 border-radius:0 8px 8px 0;padding:9px 12px}}
  .unexp{{border-left-color:{COPPER};background:#FDF7F2}}
  .flags-head{{font-size:11.5px;font-weight:700;margin-bottom:5px}}
  .flags ul,.unexp ul{{margin:0;padding-left:17px;font-size:11.5px;line-height:1.5}}
  .ex{{color:{MUTED};font-style:italic}}
  .degraded{{background:#FBEDEA;border:1px solid {RED};color:{RED};border-radius:9px;
             padding:10px 13px;margin-bottom:14px;font-size:12.5px}}
  .note{{background:{CREAM};border:1px solid {BORDER};border-radius:10px;
         padding:11px 14px;font-size:12.5px;color:{MUTED};margin-bottom:16px}}
  .prov{{font-size:11.5px;color:{MUTED};line-height:1.6}}
  @media(max-width:1100px){{.cols{{grid-template-columns:1fr}} .side{{display:none}}}}
</style></head><body>
<div class="wrap">
  <aside class="side">
    <div class="brand"><div class="logo">A</div>
      <div><b>Atlas Hub</b><span>NBFC Enterprise Suite</span></div></div>
    <p class="navlabel">Finance &amp; Treasury</p>
    <nav class="nav">
      <a href="#">Reconciliation Agent</a>
      <a href="#">Fund-Raising Agent</a>
      <a href="#">Cash Flow Forecast</a>
      <a href="#">Cost of Funds</a>
      <a href="#">Expense Anomaly</a>
      <a href="#">NPA Provisioning</a>
      <a href="#" class="on">Investor Reporting</a>
    </nav>
  </aside>

  <main class="main">
    <div class="top">
      <div>
        <h1>{e(m['company_name'])}</h1>
        <div class="sub">Investor &amp; board reporting &mdash;
          <b>{e(m['reporting_quarter'])}</b> &middot; QoQ vs {e(m['prior_quarter'])}
          &middot; YoY vs {e(m['yoy_quarter'])} &middot; {e(m['accounting_standard'])}
          &middot; {e(m['confidentiality'])}</div>
      </div>
      <button class="primary" onclick="exportDeck()">Export to deck</button>
    </div>

    {degraded}
    <div class="note"><b>First draft.</b> Every commentary section below is editable
      and requires finance team refinement and CFO approval before it enters the deck.
      Figures were computed deterministically before the model was called; the model
      wrote only the prose, and every number in it was checked back against the
      computed figures ({gr['figures_checked']} checked,
      {gr['ungrounded_figures']} ungrounded).</div>

    <div class="varbar"><span>{e(bv['label'])} &mdash; {e(bv.get('metric_key') or '')}</span>
      <b style="color:{_sent_colour(bv['sentiment'])}">{e(bv['display'])}</b></div>

    <div class="cols">
      <div>
        <div class="card">
          <h2>Quarter-over-quarter comparison</h2>
          <div class="cap">Historical actuals and approved plan figures</div>
          <table>
            <thead><tr><th>Metric</th><th>Prior actual</th><th>Current actual</th>
              <th>Forecast</th><th>QoQ change</th><th>YoY change</th></tr></thead>
            <tbody>{''.join(kpi_rows)}</tbody>
          </table>
        </div>

        <div class="card">
          <div class="ccard-head" style="margin-bottom:12px">
            <div><h2>AI-generated commentary</h2>
              <div class="cap" style="margin:0">Editable financial narratives drafted
                for deck inclusion</div></div>
            <div class="ctools">
              <select id="tone">{tone_opts}</select>
              <button onclick="alert('Regenerate is wired to the agent in the live app. '
                + 'Re-run the agent with a different COMMENTARY_TONE to redraft.')">Regenerate all</button>
            </div>
          </div>
          {''.join(cards)}
        </div>
      </div>

      <div>
        <div class="card">
          <h2>Stage movement</h2>
          <div class="cap">{e(sm['subtitle'])}</div>
          <table><thead><tr><th>Stage</th><th>Current</th><th>Forecast</th></tr></thead>
            <tbody>{stage_rows}</tbody></table>
          <div class="netflow"><span>{e(sm['net_flow']['label'])}</span>
            <b style="color:{_sent_colour(sm['net_flow']['sentiment'])}">
              {e(sm['net_flow']['display'])}</b></div>
        </div>

        <div class="card">
          <h2>Risk / attention items</h2>
          <div class="cap">{len(payload['risk_attention_items'])} derived from the data by rule</div>
          {risk_items or '<div class="empty">None raised</div>'}
        </div>

        <div class="card">
          <h2>Source traceability</h2>
          <div class="cap">Systems of record behind each metric group</div>
          {trace}
        </div>

        <div class="card">
          <h2>Report readiness</h2>
          <div class="donutwrap">
            <svg width="140" height="140" viewBox="0 0 140 140">
              <circle cx="70" cy="70" r="54" fill="none" stroke="{BORDER}" stroke-width="17"/>
              <circle cx="70" cy="70" r="54" fill="none" stroke="{GREEN}" stroke-width="17"
                stroke-dasharray="{dash}" stroke-linecap="round"
                transform="rotate(-90 70 70)"/>
              <text x="70" y="68" text-anchor="middle" font-size="27"
                font-weight="700" fill="{CHARCOAL}">{rr['score']:.0f}</text>
              <text x="70" y="86" text-anchor="middle" font-size="10"
                fill="{MUTED}" letter-spacing="1.5">TOTAL</text>
            </svg>
          </div>
          <div class="legend">
            <span><span class="dot" style="background:{GREEN}"></span>Completed</span>
            <span><span class="dot" style="background:{BORDER}"></span>Pending</span>
          </div>
          {counters}
        </div>

        <div class="card">
          <h2>Provenance</h2>
          <div class="prov">
            Figures computed in Python from {len(payload['source_traceability'])} systems of
            record before the model was called.<br>
            Prose drafted by <b>{e(payload['llm_metadata']['provider'])} /
            {e(payload['llm_metadata']['model'])}</b> in
            {payload['llm_metadata']['passes']} pass(es) with
            {payload['llm_metadata']['repair_rounds']} guardrail repair round(s).<br>
            {gr['sections_clean']}/{gr['sections_drafted']} sections passed every
            automated check.<br>
            {len(payload['arithmetic_audit'])} arithmetic discrepancy(ies) against an
            independent recompute.<br>
            Estimated time saved: <b>{ts['hours_saved']} hours</b>.<br><br>
            <b>{len(payload['review_queue'])}</b> item(s) in the finance team review queue.
            CFO approval required before this content is final.
          </div>
        </div>
      </div>
    </div>
  </main>
</div>
<script>
  const PAYLOAD = {json.dumps(payload, default=str)};
  function recount(t){{
    const n = t.value.trim().split(/\\s+/).filter(Boolean).length;
    t.closest('.ccard').querySelector('.wcount').textContent = n;
    const b = t.closest('.ccard').querySelector('.accept');
    if (b.classList.contains('done')) {{
      b.classList.remove('done'); b.textContent = 'Accept';
      const s = t.closest('.ccard').querySelector('.status');
      s.className = 'status needs'; s.textContent = 'Edited';
    }}
  }}
  function accept(b){{
    const card = b.closest('.ccard'), s = card.querySelector('.status');
    b.classList.toggle('done');
    if (b.classList.contains('done')) {{ b.textContent = 'Accepted';
      s.className = 'status ok'; s.textContent = 'Accepted'; }}
    else {{ b.textContent = 'Accept'; s.className = 'status needs'; s.textContent = 'Draft'; }}
  }}
  function exportDeck(){{
    const out = JSON.parse(JSON.stringify(PAYLOAD));
    out.ai_commentary.tone = document.getElementById('tone').value;
    document.querySelectorAll('.ccard').forEach(c => {{
      const id = c.dataset.section;
      const sec = out.ai_commentary.sections.find(s => s.id === id);
      if (!sec) return;
      const body = c.querySelector('.cbody').value;
      sec.status = c.querySelector('.accept').classList.contains('done')
        ? 'accepted' : (body !== sec.body ? 'edited' : 'draft');
      sec.body = body;
      sec.word_count = body.trim().split(/\\s+/).filter(Boolean).length;
    }});
    const blob = new Blob([JSON.stringify(out, null, 2)], {{type:'application/json'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'investor_report_reviewed.json';
    a.click();
  }}
</script>
</body></html>"""


# ==============================================================================
# PDF
# ==============================================================================

def build_pdf(payload, path):
    if not REPORTLAB_AVAILABLE:
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_markdown(payload))
        return path

    m = payload["meta"]
    doc = SimpleDocTemplate(path, pagesize=A4, rightMargin=34, leftMargin=34,
                            topMargin=34, bottomMargin=34)
    ss = getSampleStyleSheet()
    copper, charcoal = colors.HexColor(COPPER), colors.HexColor(CHARCOAL)
    cream, border, muted = (colors.HexColor(CREAM), colors.HexColor(BORDER),
                            colors.HexColor(MUTED))

    st_title = ParagraphStyle("T", parent=ss["Heading1"], fontName="Helvetica-Bold",
                              fontSize=19, leading=23, textColor=copper, spaceAfter=3)
    st_sub = ParagraphStyle("S", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=11, leading=14, textColor=charcoal, spaceAfter=6)
    st_meta = ParagraphStyle("M", parent=ss["Normal"], fontSize=8.5, leading=11.5,
                             textColor=muted, spaceAfter=10)
    st_h2 = ParagraphStyle("H", parent=ss["Heading2"], fontName="Helvetica-Bold",
                           fontSize=11.5, leading=14, textColor=charcoal,
                           spaceBefore=13, spaceAfter=5)
    st_body = ParagraphStyle("B", parent=ss["Normal"], fontSize=9.5, leading=14,
                             textColor=charcoal, spaceAfter=6)
    st_small = ParagraphStyle("SM", parent=ss["Normal"], fontSize=8, leading=11,
                              textColor=muted, spaceAfter=5)
    st_cell = ParagraphStyle("C", parent=ss["Normal"], fontSize=7.8, leading=10,
                             textColor=charcoal)
    st_head = ParagraphStyle("HD", parent=ss["Normal"], fontName="Helvetica-Bold",
                             fontSize=7.8, leading=10, textColor=colors.white)

    def table(data, widths):
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), copper),
            ("GRID", (0, 0), (-1, -1), 0.4, border),
            ("PADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, cream])]))
        return t

    story = [
        Paragraph(m["company_name"], st_title),
        Paragraph(f"INVESTOR / BOARD COMMENTARY PACK &mdash; {m['reporting_quarter']}", st_sub),
        Paragraph(f"QoQ vs <b>{m['prior_quarter']}</b> &nbsp;|&nbsp; YoY vs "
                  f"<b>{m['yoy_quarter']}</b> &nbsp;|&nbsp; Audience: "
                  f"<b>{m['audience']}</b> &nbsp;|&nbsp; {m['accounting_standard']} / "
                  f"{m['ecl_standard']} &nbsp;|&nbsp; {m['confidentiality']}", st_meta),
        HRFlowable(width="100%", thickness=1.4, color=copper, spaceAfter=8),
        Paragraph("<b>FIRST DRAFT &mdash; NOT FOR CIRCULATION.</b> Requires finance team "
                  "refinement and CFO approval. Sections marked NEEDS REVIEW failed an "
                  "automated check.", st_small),
    ]

    # comparison table
    story.append(Paragraph("1. QUARTER-OVER-QUARTER COMPARISON", st_h2))
    data = [[Paragraph(h, st_head) for h in
             ("Metric", "Prior actual", "Current actual", "Forecast", "QoQ", "YoY")]]
    for r in payload["kpi_comparison"]:
        def col(disp, sent):
            c = {"favourable": GREEN, "adverse": RED}.get(sent, MUTED)
            return f"<font color='{c}'>{disp}</font>"
        data.append([Paragraph(r["label"], st_cell),
                     Paragraph(r["prior_actual_display"], st_cell),
                     Paragraph(f"<b>{r['current_actual_display']}</b>", st_cell),
                     Paragraph(r["forecast_display"], st_cell),
                     Paragraph(col(r["qoq_change_display"], r["qoq_sentiment"]), st_cell),
                     Paragraph(col(r["yoy_change_display"], r["yoy_sentiment"]), st_cell)])
    story.append(table(data, [1.55 * inch, 1.0 * inch, 1.05 * inch,
                              0.95 * inch, 0.85 * inch, 0.85 * inch]))
    bv = payload["budget_variance"]
    story.append(Paragraph(f"Variance to budget ({bv.get('metric_key')}): "
                           f"<b>{bv['display']}</b>", st_small))

    # stage movement
    story.append(Paragraph("2. STAGE MOVEMENT", st_h2))
    data = [[Paragraph(h, st_head) for h in
             ("Stage", "Current", "Share of book", "Forecast", "QoQ change")]]
    for r in payload["stage_movement"]["rows"]:
        data.append([
            Paragraph(r["label"], st_cell),
            Paragraph(r["current_display"], st_cell),
            Paragraph(f"{r['current_share_pct']:.1f}%" if r.get("current_share_pct") else "—", st_cell),
            Paragraph(r["forecast_display"], st_cell),
            Paragraph(f"{r['qoq_change']:+,.1f}" if r.get("qoq_change") is not None else "—", st_cell)])
    story.append(table(data, [1.4 * inch, 1.3 * inch, 1.1 * inch, 1.3 * inch, 1.15 * inch]))
    nf = payload["stage_movement"]["net_flow"]
    story.append(Paragraph(f"{nf['label']}: <b>{nf['display']}</b>", st_small))

    # commentary
    story.append(PageBreak())
    story.append(Paragraph("3. DRAFT COMMENTARY", st_h2))
    for s in payload["ai_commentary"]["sections"]:
        badge = (s.get("delta_badge") or {}).get("text", "")
        state = "NEEDS REVIEW" if s["flags"] else "DRAFT"
        colour = RED if s["flags"] else GREEN
        story.append(Paragraph(
            f"<b>{s['order']}. {s['title']}</b> &nbsp; <font color='{MUTED}'>{badge}</font> "
            f"&nbsp; <font color='{colour}'><b>[{state}]</b></font>", st_body))
        story.append(Paragraph(s["body"] or "<i>No draft produced.</i>", st_body))
        for f in s["flags"]:
            story.append(Paragraph(
                f"<font color='{RED}'>&#9888; {f['type']} [{f['severity']}]</font> "
                f"{f['message']}", st_small))
        for u in s.get("unexplained_movements", []):
            story.append(Paragraph(f"<font color='{COPPER}'>&#9888; unattributed</font> {u}",
                                   st_small))
        story.append(Spacer(1, 5))

    # risk items
    story.append(Paragraph("4. RISK / ATTENTION ITEMS", st_h2))
    data = [[Paragraph(h, st_head) for h in ("Severity", "Category", "Item")]]
    for r in payload["risk_attention_items"]:
        data.append([Paragraph(r["severity"], st_cell),
                     Paragraph(r["category"], st_cell),
                     Paragraph(r["message"], st_cell)])
    if len(data) == 1:
        data.append([Paragraph("—", st_cell)] * 3)
    story.append(table(data, [0.8 * inch, 1.1 * inch, 4.35 * inch]))

    # reconciliation + validation
    story.append(Paragraph("5. CROSS-SOURCE RECONCILIATION", st_h2))
    data = [[Paragraph(h, st_head) for h in
             ("Field", "Segment ledger", "Reporting system", "Variance", "Status")]]
    for rc in payload["reconciliations"]:
        badge = (f"<font color='{RED}'><b>UNRECONCILED</b></font>" if rc.get("breached")
                 else f"<font color='{GREEN}'>Agreed</font>")
        data.append([Paragraph(str(rc["label"]), st_cell),
                     Paragraph(str(rc["bottom_up_value"]), st_cell),
                     Paragraph(str(rc["reported_value"]), st_cell),
                     Paragraph(f"{rc['variance_pct']}%", st_cell),
                     Paragraph(badge, st_cell)])
    story.append(table(data, [1.6 * inch, 1.2 * inch, 1.3 * inch, 0.85 * inch, 1.3 * inch]))

    vr = payload["validation_results"]
    passed = sum(1 for v in vr if v["status"] == "PASS")
    story.append(Paragraph(f"6. VALIDATION RULES ({passed}/{len(vr)} PASSED)", st_h2))
    data = [[Paragraph(h, st_head) for h in ("ID", "Rule", "Observed", "Status")]]
    for v in vr:
        c = {"PASS": GREEN, "FAIL": RED}.get(v["status"], MUTED)
        data.append([Paragraph(v["id"], st_cell), Paragraph(v["name"], st_cell),
                     Paragraph(v["observed"], st_cell),
                     Paragraph(f"<font color='{c}'><b>{v['status']}</b></font>", st_cell)])
    story.append(table(data, [0.5 * inch, 1.85 * inch, 2.75 * inch, 1.15 * inch]))

    # review queue
    story.append(Paragraph(f"7. REVIEW QUEUE &mdash; FINANCE TEAM "
                           f"({len(payload['review_queue'])})", st_h2))
    for q in payload["review_queue"]:
        where = q.get("section_id") or q.get("field") or "—"
        story.append(Paragraph(
            f"&bull; <b>[{q['severity']}]</b> <i>{q['origin']}</i> &mdash; {where}: "
            f"{q['recommended_action']}", st_small))
    if not payload["review_queue"]:
        story.append(Paragraph("&bull; Nothing outstanding.", st_small))

    gr, ts, lm = payload["guardrail_report"], payload["time_saved"], payload["llm_metadata"]
    story.append(Paragraph("8. HOW THIS DRAFT WAS PRODUCED", st_h2))
    story.append(Paragraph(
        f"Figures computed deterministically in Python before the model was called; "
        f"{len(payload['arithmetic_audit'])} discrepancy(ies) against an independent "
        f"recompute. Prose drafted by {lm['provider']} / {lm['model']} in {lm['passes']} "
        f"pass(es) with {lm['repair_rounds']} guardrail repair round(s). "
        f"{gr['figures_checked']} figures in the prose were checked back against the fact "
        f"base, {gr['ungrounded_figures']} ungrounded. "
        f"{gr['sections_clean']}/{gr['sections_drafted']} sections passed every check. "
        f"Estimated time saved: {ts['hours_saved']} hours. "
        f"CFO approval is required before this content is final.", st_small))

    doc.build(story)
    return path


# ==============================================================================
# WRITE ALL
# ==============================================================================

def write_all(payload, config, base_dir=None):
    """
    Writes the four artefacts into output/. An OUTPUT_*_PATH environment variable
    overrides the filename, and an absolute value redirects the file entirely.
    """
    out_dir = base_dir or ensure_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    outputs = {}

    def _dest(env_key, default):
        return resolve(os.getenv(env_key, default), out_dir)

    p = _dest("OUTPUT_JSON_PATH", "generated_investor_report.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    outputs["JSON payload"] = p

    p = _dest("OUTPUT_MARKDOWN_PATH", "generated_commentary.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(build_markdown(payload))
    outputs["Markdown pack"] = p

    p = _dest("OUTPUT_HTML_PATH", "reporting_assistant.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(build_html(payload))
    outputs["Assistant screen"] = p

    p = _dest("OUTPUT_PDF_PATH", "generated_commentary.pdf")
    build_pdf(payload, p)
    outputs["PDF pack"] = p

    return outputs
