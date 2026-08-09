"""
==============================================================================
METRICS ENGINE - Investor / Board Reporting Agent
==============================================================================
Everything numeric on the Atlas Hub Investor Reporting screen is computed here,
in Python, before the LLM is called at all.

WHY THE SPLIT IS THIS WAY

The sibling agent in this suite learned the hard way that a 70B model
transcribes source data perfectly, describes the correct method perfectly, and
then mis-adds the column. So nothing in this pipeline asks a model to do
arithmetic.

But this use case does not need it to. Unlike a fund-raising data pack, where
the model has to decide which source is authoritative for each metric, the
Investor Reporting screen has a FIXED metric contract: eight KPIs, named
columns, named comparators, all declared in input_config.yaml. There is no
methodological judgement left for a model to make.

What is genuinely hard here is the WRITING - explaining why a metric moved, in
board-ready prose, without guessing. So the division of labour is:

    metrics_engine.py  ->  every figure, every variance, every driver
                           decomposition, every risk item, every verdict
    llm_engine.py      ->  the prose, and only the prose

The model is handed exact figures and a deterministic attribution decomposition,
and its causal claims are constrained to what that decomposition evidences.

WHAT THIS MODULE PRODUCES

A single `facts` dict - the complete numeric picture of the quarter. It is both
the payload handed to the drafting model and the source of truth the guardrails
check the drafted prose against.
==============================================================================
"""

import os
import re
import json
from datetime import datetime, timezone

from .paths import CONFIG_FILE, PROMPTS_DIR, ROOT, resolve

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except Exception:                                    # pragma: no cover
    PANDAS_AVAILABLE = False

try:
    import yaml
    YAML_AVAILABLE = True
except Exception:                                    # pragma: no cover
    YAML_AVAILABLE = False


# ==============================================================================
# LOADERS
# ==============================================================================

def load_config(path=None):
    """Reads config/input_config.yaml - every technical aspect of the agent."""
    path = path or CONFIG_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"Configuration file {path} not found")
    if not YAML_AVAILABLE:
        raise RuntimeError("pyyaml is required to read input_config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompt(filename):
    """Reads one of the Markdown instruction files from prompts/."""
    path = os.path.join(PROMPTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file {path} not found")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_feeds(config):
    """Reads every feed declared under `inputs` in the config."""
    if not PANDAS_AVAILABLE:
        raise RuntimeError("pandas is required to read the data feeds")

    inputs = config.get("inputs", {})
    feeds = {}

    def _path(key):
        # Feed paths are written relative to the project root in the config, so
        # they read the way a human would write them.
        return resolve(inputs.get(key), ROOT)

    for key in ("quarterly_metrics", "segment_breakdown", "one_off_items",
                "forecast_plan", "budget_targets"):
        p = _path(key)
        feeds[key] = pd.read_csv(p) if p and os.path.exists(p) else None

    for key in ("past_commentary", "company_profile"):
        p = _path(key)
        if p and os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                feeds[key] = json.load(f)
        else:
            feeds[key] = {}

    return feeds


# ==============================================================================
# FORMATTING
# ==============================================================================
# Rounding and presentation are decided here, by metric, and never by the model.

def fmt_number(value, precision=0, thousands=True):
    if value is None:
        return "—"
    if thousands and precision == 0:
        return f"{value:,.0f}"
    return f"{value:,.{precision}f}" if thousands else f"{value:.{precision}f}"


def fmt_kpi_value(value, kpi):
    """Renders a KPI value exactly as the comparison table should show it."""
    if value is None:
        return "—"
    precision = int(kpi.get("precision", 2))
    if kpi.get("unit") == "Percentage":
        return f"{value:.{precision}f}%"
    return f"{value:,.{precision}f}"


def fmt_change(change, change_type, precision=2):
    """
    pct_change -> '+12.3%'   (a percentage change in an amount)
    pp_change  -> '-0.20%'   (a movement in percentage points)

    Both print a % sign, which is what the screen shows, but they are different
    quantities and the commentary must describe them differently. The
    change_type travels with the figure so the model cannot conflate them.
    """
    if change is None:
        return "—"
    nd = 1 if change_type == "pct_change" else precision
    return f"{change:+.{nd}f}%"


def sentiment_for(change, direction):
    if change is None or abs(change) < 1e-12:
        return "neutral"
    if direction == "neutral":
        return "neutral"
    favourable = (change > 0) if direction == "higher_is_better" else (change < 0)
    return "favourable" if favourable else "adverse"


# ==============================================================================
# PERIOD RESOLUTION
# ==============================================================================

def resolve_periods(config, df):
    """
    Resolves current / QoQ / YoY rows by negative offset, so the pipeline works
    unchanged however many quarters of history the data team supplies.
    """
    rep = config.get("reporting", {})
    n = len(df)
    out = {}
    for name, key, default in (("current", "current_row_offset", -1),
                               ("qoq", "qoq_row_offset", -2),
                               ("yoy", "yoy_row_offset", -5)):
        off = int(rep.get(key, default))
        out[name] = df.iloc[off] if abs(off) <= n else None
    return out


def _val(row, column):
    if row is None or column not in row.index:
        return None
    v = row[column]
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


# ==============================================================================
# KPI COMPARISON TABLE
# ==============================================================================

def compute_kpi_comparison(config, feeds, periods):
    """
    Builds the 'Quarter-over-quarter comparison' rows: PRIOR ACTUAL, CURRENT
    ACTUAL, FORECAST, QOQ CHANGE, YOY CHANGE - one row per entry in the KPI
    catalog, in the catalog's declared order.
    """
    catalog = config.get("kpi_catalog", {})
    rep = config.get("reporting", {})
    fc_cfg = config.get("forecast", {})
    trace = {t["id"]: t for t in config.get("source_traceability", [])}

    forecast_lookup = {}
    dff = feeds.get("forecast_plan")
    if dff is not None and fc_cfg.get("source") == "plan_feed":
        fq = rep.get("forecast_quarter")
        sub = dff[dff["reporting_quarter"] == fq] if fq else dff
        for _, r in sub.iterrows():
            forecast_lookup[r["metric_key"]] = {
                "value": float(r["forecast_value"]),
                "basis": r.get("basis"),
                "approved_by": r.get("approved_by"),
            }

    budget_lookup = {}
    dfb = feeds.get("budget_targets")
    if dfb is not None:
        cq = periods["current"]["reporting_quarter"] if periods["current"] is not None else None
        sub = dfb[dfb["reporting_quarter"] == cq] if cq else dfb
        for _, r in sub.iterrows():
            budget_lookup[r["metric_key"]] = float(r["budget_value"])

    rows = []
    for key, kpi in sorted(catalog.items(), key=lambda kv: kv[1].get("order", 99)):
        col = kpi.get("column", key)
        cur = _val(periods["current"], col)
        prior = _val(periods["qoq"], col)
        yoy = _val(periods["yoy"], col)

        change_type = kpi.get("change_type", "pct_change")
        precision = int(kpi.get("precision", 2))
        direction = kpi.get("direction", "neutral")

        def _change(base):
            """QoQ/YoY movement, in the unit the metric is conventionally moved in."""
            if cur is None or base is None:
                return None, None
            if change_type == "pp_change":
                # A ratio moves in percentage points. The absolute movement and
                # the reported movement are the same number here.
                d = cur - base
                return round(d, 4), round(d, 4)
            if base == 0:
                return None, round(cur - base, 4)
            return round((cur - base) / base * 100, 4), round(cur - base, 4)

        qoq_change, qoq_abs = _change(prior)
        yoy_change, yoy_abs = _change(yoy)

        materiality = float(kpi.get("materiality", 0))
        material = qoq_abs is not None and abs(qoq_abs) >= materiality

        fc = forecast_lookup.get(key)
        budget = budget_lookup.get(key)
        bud_var = round(cur - budget, 4) if (cur is not None and budget) else None
        bud_var_pct = round((cur - budget) / budget * 100, 4) if (cur is not None and budget) else None

        src_id = kpi.get("source_system", "")
        rows.append({
            "metric_key": key,
            "label": kpi.get("label", key),
            "short_label": kpi.get("short_label", key),
            "unit": kpi.get("unit", ""),
            "definition": kpi.get("definition", ""),
            "order": kpi.get("order", 99),
            "precision": precision,

            "prior_actual": prior,
            "current_actual": cur,
            "yoy_actual": yoy,
            "forecast": fc["value"] if fc else None,
            "forecast_basis": fc["basis"] if fc else None,
            "forecast_approved_by": fc["approved_by"] if fc else None,

            "prior_actual_display": fmt_kpi_value(prior, kpi),
            "current_actual_display": fmt_kpi_value(cur, kpi),
            "forecast_display": fmt_kpi_value(fc["value"], kpi) if fc else "—",

            "change_type": change_type,
            "qoq_change": qoq_change,
            "qoq_change_display": fmt_change(qoq_change, change_type, precision),
            "qoq_change_abs": qoq_abs,
            "yoy_change": yoy_change,
            "yoy_change_display": fmt_change(yoy_change, change_type, precision),
            "yoy_change_abs": yoy_abs,

            "direction": direction,
            "qoq_sentiment": sentiment_for(qoq_change, direction),
            "yoy_sentiment": sentiment_for(yoy_change, direction),
            "material": bool(material),

            "budget": budget,
            "budget_variance": bud_var,
            "budget_variance_pct": bud_var_pct,

            "source_system": src_id,
            "source_system_label": trace.get(src_id, {}).get("system", ""),
            "computed": cur is not None,
            "notes": [] if cur is not None else [f"column {col!r} not present in the quarterly feed"],
        })
    return rows


# ==============================================================================
# DERIVED FIGURES & ONE-OFF ADJUSTMENT
# ==============================================================================

def compute_derived(config, feeds, periods):
    """
    Figures quoted in commentary but not rendered as comparison-table rows,
    including the ex-one-off adjustments that keep a property sale from being
    presented as underlying performance.
    """
    cur, prior = periods["current"], periods["qoq"]
    rep = config.get("reporting", {})
    tax = float(rep.get("effective_tax_rate", 0.25))
    d = {}

    g = lambda c: _val(cur, c)
    p = lambda c: _val(prior, c)

    gnpa_cr, prov_cr = g("gnpa_cr"), g("provision_cr")
    if gnpa_cr:
        d["provision_coverage_pct"] = round(prov_cr / gnpa_cr * 100, 1)
        prior_g, prior_p = p("gnpa_cr"), p("provision_cr")
        if prior_g:
            d["provision_coverage_prior_pct"] = round(prior_p / prior_g * 100, 1)

    s1, s2, s3 = g("stage1_cr"), g("stage2_cr"), g("stage3_cr")
    if None not in (s1, s2, s3):
        d["stage_sum_cr"] = round(s1 + s2 + s3, 2)

    rev, eb, pat = g("revenue_cr"), g("ebitda_cr"), g("pat_cr")
    if rev:
        d["ebitda_margin_pct"] = round(eb / rev * 100, 1)
        d["pat_margin_pct"] = round(pat / rev * 100, 1)
        pr, pe, pp_ = p("revenue_cr"), p("ebitda_cr"), p("pat_cr")
        if pr:
            d["ebitda_margin_prior_pct"] = round(pe / pr * 100, 1)
            d["pat_margin_prior_pct"] = round(pp_ / pr * 100, 1)

    nw, borr = g("net_worth_cr"), g("borrowings_cr")
    if nw:
        d["debt_to_equity"] = round(borr / nw, 2)
        d["net_worth_cr"] = nw
        d["borrowings_cr"] = borr

    d["opex_cr"] = g("opex_cr")
    d["gnpa_cr"] = gnpa_cr
    d["provision_cr"] = prov_cr
    d["net_flow_to_stage3_cr"] = g("net_flow_to_stage3_cr")
    d["net_flow_to_stage3_prior_cr"] = p("net_flow_to_stage3_cr")
    d["active_accounts"] = g("active_accounts")

    # ---- one-off separation -------------------------------------------------
    one_offs = []
    df_oo = feeds.get("one_off_items")
    cq = cur["reporting_quarter"] if cur is not None else None
    if df_oo is not None and cq:
        for _, r in df_oo[df_oo["reporting_quarter"] == cq].iterrows():
            one_offs.append({
                "item_id": r.get("item_id"),
                "description": r.get("description"),
                "metric_affected": r.get("metric_affected"),
                "impact_value": float(r.get("impact_value", 0)),
                "impact_unit": r.get("impact_unit"),
                "nature": r.get("nature"),
                "recurring": r.get("recurring"),
                "disclosure_note": r.get("disclosure_note"),
            })

    pbt_impact = sum(o["impact_value"] for o in one_offs if o["metric_affected"] == "pat_cr")
    if pat is not None and abs(pbt_impact) > 1e-9:
        d["one_off_pbt_impact_cr"] = round(pbt_impact, 2)
        d["one_off_pat_impact_cr"] = round(pbt_impact * (1 - tax), 2)
        d["pat_ex_oneoff_cr"] = round(pat - pbt_impact * (1 - tax), 1)
        pp_ = p("pat_cr")
        if pp_:
            d["pat_ex_oneoff_qoq_pct"] = round((d["pat_ex_oneoff_cr"] - pp_) / pp_ * 100, 1)

    aum_inorganic = sum(o["impact_value"] for o in one_offs if o["metric_affected"] == "aum_cr")
    aum_cur, aum_prior = g("aum_cr"), p("aum_cr")
    if aum_cur is not None and aum_prior and abs(aum_inorganic) > 1e-9:
        total_growth = aum_cur - aum_prior
        d["aum_growth_total_cr"] = round(total_growth, 1)
        d["aum_inorganic_cr"] = round(aum_inorganic, 1)
        d["aum_organic_growth_cr"] = round(total_growth - aum_inorganic, 1)
        d["aum_organic_growth_pct"] = round((total_growth - aum_inorganic) / aum_prior * 100, 1)

    return d, one_offs


# ==============================================================================
# BUDGET VARIANCE STRIP
# ==============================================================================

def compute_budget_variance(config, kpi_rows):
    cfg = config.get("budget_variance", {})
    sym = config.get("reporting", {}).get("currency_symbol", "₹")
    headline_key = cfg.get("headline_metric")
    by_metric = []
    headline = None

    for row in kpi_rows:
        if row.get("budget") is None or row.get("budget_variance") is None:
            continue
        fav_above = cfg.get("favourable_when", "above") == "above"
        if row["direction"] == "lower_is_better":
            fav_above = False
        var = row["budget_variance"]
        sent = "neutral" if abs(var) < 1e-9 else (
            "favourable" if (var > 0) == fav_above else "adverse")
        entry = {
            "metric_key": row["metric_key"],
            "label": row["label"],
            "actual": row["current_actual"],
            "budget": row["budget"],
            "variance": round(var, 2),
            "variance_pct": round(row["budget_variance_pct"], 2),
            "sentiment": sent,
        }
        by_metric.append(entry)
        if row["metric_key"] == headline_key:
            headline = entry

    if headline is None:
        return {"metric_key": None, "label": cfg.get("label", "Variance to budget"),
                "variance": 0, "variance_pct": 0, "display": "—",
                "sentiment": "neutral", "by_metric": by_metric}

    return {
        "metric_key": headline["metric_key"],
        "label": cfg.get("label", "Variance to budget"),
        "actual": headline["actual"],
        "budget": headline["budget"],
        "variance": headline["variance"],
        "variance_pct": headline["variance_pct"],
        "display": f"{sym}{headline['variance']:,.1f} Cr "
                   f"({headline['variance_pct']:+.1f}% vs budget)",
        "sentiment": headline["sentiment"],
        "basis": "FY27 annual operating plan - quarterly phasing",
        "by_metric": by_metric,
    }


# ==============================================================================
# STAGE MOVEMENT CARD
# ==============================================================================

def compute_stage_movement(config, feeds, periods):
    cfg = config.get("stage_movement", {})
    rep = config.get("reporting", {})
    sym = rep.get("currency_symbol", "₹")
    cur, prior = periods["current"], periods["qoq"]

    forecast = {}
    dff = feeds.get("forecast_plan")
    if dff is not None:
        fq = rep.get("forecast_quarter")
        sub = dff[dff["reporting_quarter"] == fq] if fq else dff
        forecast = {r["metric_key"]: float(r["forecast_value"]) for _, r in sub.iterrows()}

    aum_cur = _val(cur, "aum_cr")
    aum_fc = forecast.get("aum_cr")

    rows = []
    for st in cfg.get("stages", []):
        key = st["key"]
        c, p_ = _val(cur, key), _val(prior, key)
        f = forecast.get(key)
        change = round(c - p_, 2) if (c is not None and p_ is not None) else None
        # Stage 1 growing is favourable; Stage 2 and 3 growing is adverse.
        direction = "higher_is_better" if key == "stage1_cr" else "lower_is_better"
        rows.append({
            "stage": key,
            "label": st.get("label", key),
            "description": st.get("description", ""),
            "current": c,
            "prior": p_,
            "forecast": f,
            "current_display": f"{sym}{c:,.1f} Cr" if c is not None else "—",
            "forecast_display": f"{sym}{f:,.1f} Cr" if f is not None else "—",
            "current_share_pct": round(c / aum_cur * 100, 2) if (c and aum_cur) else None,
            "forecast_share_pct": round(f / aum_fc * 100, 2) if (f and aum_fc) else None,
            "qoq_change": change,
            "sentiment": sentiment_for(change, direction),
        })

    nf_cfg = cfg.get("net_flow_metric", {})
    nf_key = nf_cfg.get("key", "net_flow_to_stage3_cr")
    nf_cur, nf_prior = _val(cur, nf_key), _val(prior, nf_key)
    nf_change = (nf_cur - nf_prior) if (nf_cur is not None and nf_prior is not None) else None

    return {
        "title": cfg.get("title", "Stage movement"),
        "subtitle": cfg.get("subtitle", "Portfolio migration & net flow"),
        "rows": rows,
        "net_flow": {
            "label": nf_cfg.get("label", "Net new flow to stage 3"),
            "value": nf_cur,
            "prior": nf_prior,
            "display": f"{sym}{nf_cur:.1f} Cr" if nf_cur is not None else "—",
            "sentiment": sentiment_for(nf_change, nf_cfg.get("direction", "lower_is_better")),
        },
    }


# ==============================================================================
# DRIVER ATTRIBUTION
# ==============================================================================
# This is the model's ONLY permitted evidence for a causal claim. Computed
# exactly, never by the model, and never inferred from the prose afterwards.

def compute_attribution(config, feeds, kpi_rows):
    cfg = config.get("attribution", {})
    seg = feeds.get("segment_breakdown")
    min_share = float(cfg.get("min_explained_share_pct", 60.0))
    kpi_by_key = {r["metric_key"]: r for r in kpi_rows}
    out = {}

    if seg is None:
        return out

    for metric_key, spec in cfg.items():
        if not isinstance(spec, dict) or spec.get("basis") != "segment":
            continue
        label_col = spec.get("label_column", "segment")
        top_n = int(spec.get("top_n", 4))
        contributors = []

        if "value_column" in spec:
            # Simple additive decomposition: each segment's change in the metric.
            vc, pc = spec["value_column"], spec["prior_column"]
            for _, r in seg.iterrows():
                c, p_ = float(r[vc]), float(r[pc])
                contributors.append({
                    "label": str(r[label_col]),
                    "current": round(c, 2),
                    "prior": round(p_, 2),
                    "change": round(c - p_, 2),
                    "stock_effect": None,
                    "denominator_effect": None,
                })
            total_change = round(sum(c["change"] for c in contributors), 2)
            unit = "INR Crore"

        else:
            # Ratio decomposition. A ratio can improve purely because the
            # denominator grew, which is a materially different story from the
            # numerator shrinking - so the two effects are separated and both
            # are handed to the model.
            nc, pnc = spec["numerator_column"], spec["prior_numerator_column"]
            dc, pdc = spec["denominator_column"], spec["prior_denominator_column"]
            num1, num0 = float(seg[nc].sum()), float(seg[pnc].sum())
            den1, den0 = float(seg[dc].sum()), float(seg[pdc].sum())
            if den0 == 0 or den1 == 0:
                continue
            r0 = num0 / den0
            for _, r in seg.iterrows():
                stock = (float(r[nc]) - float(r[pnc])) / den1 * 100
                denom = -r0 * (float(r[dc]) - float(r[pdc])) / den1 * 100
                contributors.append({
                    "label": str(r[label_col]),
                    "current": round(float(r[nc]) / float(r[dc]) * 100, 2),
                    "prior": round(float(r[pnc]) / float(r[pdc]) * 100, 2),
                    "change": round(stock + denom, 4),
                    "stock_effect": round(stock, 4),
                    "denominator_effect": round(denom, 4),
                })
            total_change = round(num1 / den1 * 100 - r0 * 100, 4)
            unit = "Percentage points"

        contributors.sort(key=lambda c: abs(c["change"]), reverse=True)
        for c in contributors:
            c["share_of_change_pct"] = (
                round(c["change"] / total_change * 100, 1) if abs(total_change) > 1e-9 else None)

        named = contributors[:top_n]
        explained = (abs(sum(c["change"] for c in named)) / abs(total_change) * 100
                     if abs(total_change) > 1e-9 else 0.0)
        explained = min(explained, 100.0)

        out[metric_key] = {
            "metric_key": metric_key,
            "label": kpi_by_key.get(metric_key, {}).get("label", metric_key),
            "description": spec.get("description", ""),
            "total_change": total_change,
            "unit": unit,
            "explained_share_pct": round(explained, 1),
            "sufficient": explained >= min_share,
            "contributors": contributors,
            "named_contributors": [c["label"] for c in named],
        }
    return out


# ==============================================================================
# CROSS-SOURCE RECONCILIATION
# ==============================================================================

def compute_reconciliations(config, feeds, periods):
    seg = feeds.get("segment_breakdown")
    cur = periods["current"]
    out = []

    for spec in config.get("reconciliations", []):
        try:
            bu_spec, rep_spec = spec["bottom_up"], spec["reported"]

            if bu_spec.get("source") == "segment" and seg is not None:
                if bu_spec["method"] == "sum":
                    bottom = float(seg[bu_spec["column"]].sum())
                elif bu_spec["method"] == "ratio_of_sums_pct":
                    bottom = (float(seg[bu_spec["numerator_column"]].sum())
                              / float(seg[bu_spec["denominator_column"]].sum()) * 100)
                else:
                    continue
            else:
                continue

            if rep_spec.get("method") == "ratio_of_columns_pct":
                reported = (_val(cur, rep_spec["numerator_column"])
                            / _val(cur, rep_spec["denominator_column"]) * 100)
            else:
                reported = _val(cur, rep_spec["column"])
            if reported is None:
                continue

            tol = float(spec.get("tolerance_pct", 1.0))
            denom = abs(reported) if abs(reported) > 1e-9 else 1.0
            var_pct = abs(bottom - reported) / denom * 100

            out.append({
                "field": spec["field"],
                "label": spec.get("label", spec["field"]),
                "bottom_up_value": round(bottom, 2),
                "reported_value": round(reported, 2),
                "variance": round(bottom - reported, 2),
                "variance_pct": round(var_pct, 2),
                "tolerance_pct": tol,
                "breached": var_pct > tol,
            })
        except Exception as e:                        # a bad spec must not stop the run
            out.append({"field": spec.get("field"), "label": spec.get("label"),
                        "bottom_up_value": None, "reported_value": None,
                        "variance": None, "variance_pct": None,
                        "tolerance_pct": None, "breached": False,
                        "error": f"{type(e).__name__}: {e}"})
    return out


# ==============================================================================
# VALIDATION CATALOG
# ==============================================================================

def run_validations(config, periods, derived):
    """
    Evaluated arithmetically. The model never sees these as an open question and
    is never permitted to overturn a verdict.
    """
    cur = periods["current"]
    exprs = config.get("validation_expressions", {})
    meta = {r["id"]: r for r in config.get("validation_rules", [])}

    def field(name):
        v = derived.get(name)
        if v is not None:
            return v
        return _val(cur, name)

    results = []
    for rid in [r["id"] for r in config.get("validation_rules", [])]:
        spec = exprs.get(rid)
        m = meta.get(rid, {})
        base = {"id": rid, "name": m.get("name", rid), "rule": m.get("rule", ""),
                "severity": m.get("severity", "Medium")}
        if not spec:
            results.append({**base, "observed": "no machine-readable expression",
                            "status": "NOT_EVALUABLE"})
            continue

        lhs = field(spec.get("field"))
        if lhs is None:
            results.append({**base, "observed": f"{spec.get('field')} not available",
                            "status": "NOT_EVALUABLE"})
            continue

        if "compare_field" in spec:
            rhs = field(spec["compare_field"])
            if rhs is None:
                results.append({**base, "observed": f"{spec['compare_field']} not available",
                                "status": "NOT_EVALUABLE"})
                continue
            rhs *= float(spec.get("compare_multiplier", 1.0))
            rhs_label = spec["compare_field"]
        else:
            rhs = float(spec.get("threshold"))
            rhs_label = f"{rhs}"

        op = spec.get("op")
        tol = float(spec.get("tolerance", 0.0))
        if op == "<":
            ok = lhs < rhs
        elif op == "<=":
            ok = lhs <= rhs + tol
        elif op == ">":
            ok = lhs > rhs
        elif op == ">=":
            ok = lhs >= rhs - tol
        elif op == "==":
            ok = abs(lhs - rhs) <= max(tol, 1e-9)
        else:
            results.append({**base, "observed": f"unknown operator {op!r}",
                            "status": "NOT_EVALUABLE"})
            continue

        results.append({**base,
                        "observed": f"{spec['field']} = {round(lhs, 4)} {op} {rhs_label}"
                                    + (f" ({round(rhs, 4)})" if "compare_field" in spec else ""),
                        "status": "PASS" if ok else "FAIL"})
    return results


# ==============================================================================
# RISK / ATTENTION ITEMS
# ==============================================================================
# Derived from the data by the rules in input_config.yaml. The model may describe
# these; it may not invent one and it may not suppress one.

def derive_risk_items(config, feeds, kpi_rows, attribution, reconciliations,
                      validations, derived, periods):
    sym = config.get("reporting", {}).get("currency_symbol", "₹")
    seg = feeds.get("segment_breakdown")
    kpi_by_key = {r["metric_key"]: r for r in kpi_rows}
    items = []
    seq = 0

    def add(rule, message, **kw):
        nonlocal seq
        seq += 1
        items.append({
            "id": f"RISK-{seq:03d}",
            "rule_id": rule.get("id"),
            "severity": rule.get("severity", "Medium"),
            "category": rule.get("category", ""),
            "message": message,
            "reviewed": False,
            **kw,
        })

    for rule in config.get("risk_rules", []):
        basis, cond = rule.get("basis"), rule.get("condition")
        thr = float(rule.get("threshold", 0)) if rule.get("threshold") is not None else None

        # ---- segment-level rules ------------------------------------------
        if basis == "segment" and seg is not None:
            mcol, pcol = rule.get("metric"), rule.get("prior_metric")
            if mcol not in seg.columns or pcol not in seg.columns:
                continue
            total_change = float(seg[mcol].sum() - seg[pcol].sum())
            for _, r in seg.iterrows():
                cv, pv = float(r[mcol]), float(r[pcol])
                delta = cv - pv
                name = str(r["segment"])
                if cond == "absolute_increase_above" and delta > thr:
                    add(rule, rule["message_template"].format(
                            segment=name, symbol=sym, delta=delta),
                        segment=name, metric_key=mcol,
                        detail=f"{name} {mcol} moved from {sym}{pv:,.1f} Cr to "
                               f"{sym}{cv:,.1f} Cr, an increase of {sym}{delta:,.1f} Cr.",
                        evidence={"current": round(cv, 2), "prior": round(pv, 2),
                                  "change": round(delta, 2), "threshold": thr})
                elif cond == "pct_increase_above" and pv > 0 and (delta / pv * 100) > thr:
                    pct = delta / pv * 100
                    add(rule, rule["message_template"].format(
                            segment=name, pct=pct, symbol=sym, delta=delta),
                        segment=name, metric_key=mcol,
                        detail=f"{name} {mcol} rose from {sym}{pv:,.2f} Cr to "
                               f"{sym}{cv:,.2f} Cr, an increase of {pct:.1f}%.",
                        evidence={"current": round(cv, 2), "prior": round(pv, 2),
                                  "pct_increase": round(pct, 1), "threshold": thr})
                elif (cond == "share_of_movement_above" and abs(total_change) > 1e-9
                        and (delta / total_change * 100) > thr):
                    pct = delta / total_change * 100
                    add(rule, rule["message_template"].format(segment=name, pct=pct),
                        segment=name, metric_key=mcol,
                        detail=f"{name} contributed {sym}{delta:,.1f} Cr of the "
                               f"{sym}{total_change:,.1f} Cr total movement.",
                        evidence={"change": round(delta, 2),
                                  "total_change": round(total_change, 2),
                                  "share_pct": round(pct, 1), "threshold": thr})

        # ---- KPI-level rules ----------------------------------------------
        elif basis == "kpi":
            key = rule.get("metric")
            row = kpi_by_key.get(key)
            if row is not None:
                if cond == "pp_increase_above" and row["qoq_change"] is not None \
                        and row["qoq_change"] > thr:
                    bps = row["qoq_change"] * 100
                    add(rule, rule["message_template"].format(bps=bps, symbol=sym),
                        metric_key=key,
                        detail=f"{row['label']} moved from {row['prior_actual_display']} "
                               f"to {row['current_actual_display']}, an increase of "
                               f"{bps:.0f} basis points.",
                        evidence={"current": row["current_actual"],
                                  "prior": row["prior_actual"],
                                  "bps": round(bps, 1), "threshold": thr})
            else:
                # Not a comparison-table KPI - look it up in the derived figures.
                cv = derived.get(key) or _val(periods["current"], key)
                pv = derived.get(f"{key.replace('_cr', '')}_prior_cr") \
                    or derived.get(f"{key}_prior") \
                    or _val(periods["qoq"], key)
                if cv is not None and pv:
                    if cond == "pct_increase_above" and (cv - pv) / pv * 100 > thr:
                        add(rule, rule["message_template"].format(
                                symbol=sym, value=cv, prior=pv,
                                pct=(cv - pv) / pv * 100),
                            metric_key=key,
                            detail=f"{key} rose from {sym}{pv:,.1f} Cr to {sym}{cv:,.1f} Cr.",
                            evidence={"current": cv, "prior": pv,
                                      "pct_increase": round((cv - pv) / pv * 100, 1),
                                      "threshold": thr})

        # ---- reconciliation, attribution and validation rules --------------
        elif basis == "reconciliation" and cond == "breached":
            for rc in reconciliations:
                if rc.get("breached"):
                    add(rule, rule["message_template"].format(
                            field=rc["label"], bottom_up=rc["bottom_up_value"],
                            reported=rc["reported_value"], variance_pct=rc["variance_pct"]),
                        metric_key=rc["field"],
                        detail=f"The segment ledger gives {rc['bottom_up_value']} against "
                               f"{rc['reported_value']} from the reporting system, a variance "
                               f"of {rc['variance_pct']}% against a tolerance of "
                               f"{rc['tolerance_pct']}%.",
                        evidence=rc)

        elif basis == "attribution" and cond == "explained_share_below":
            for key, att in attribution.items():
                if not att["sufficient"]:
                    row = kpi_by_key.get(key, {})
                    add(rule, rule["message_template"].format(
                            metric_label=att["label"],
                            change=row.get("qoq_change_display", att["total_change"]),
                            explained_pct=att["explained_share_pct"]),
                        metric_key=key,
                        detail=att["description"],
                        evidence={"explained_share_pct": att["explained_share_pct"],
                                  "threshold": thr})

        elif basis == "validation" and cond == "failed":
            for v in validations:
                if v["status"] == "FAIL":
                    add({**rule, "severity": v.get("severity", rule.get("severity"))},
                        rule["message_template"].format(name=v["name"], observed=v["observed"]),
                        metric_key=None,
                        detail=f"Validation rule {v['id']} failed: {v['rule']}",
                        evidence=v)

    order = {s: i for i, s in enumerate(
        config.get("risk_display", {}).get("severity_order", ["High", "Medium", "Low"]))}
    items.sort(key=lambda i: order.get(i["severity"], 9))

    max_visible = int(config.get("risk_display", {}).get("max_visible", 3))
    for i, item in enumerate(items):
        item["visible_on_card"] = i < max_visible
    return items


# ==============================================================================
# SOURCE TRACEABILITY & READINESS
# ==============================================================================

def compute_source_traceability(config, feeds, kpi_rows, reconciliations, periods):
    computed = {r["metric_key"]: r["computed"] for r in kpi_rows}
    breached_fields = {rc["field"] for rc in reconciliations if rc.get("breached")}
    cur = periods["current"]
    out = []

    for t in config.get("source_traceability", []):
        metrics = t.get("metrics", [])
        # A metric outside the comparison table (a stage balance, say) counts as
        # computed when the column is present in the current row.
        done = sum(1 for m in metrics
                   if computed.get(m, _val(cur, m) is not None))
        issues = []
        if done < len(metrics):
            issues.append(f"{len(metrics) - done} of {len(metrics)} metrics not computed")
        conflicts = breached_fields.intersection(metrics)
        if conflicts:
            issues.append("cross-source variance on " + ", ".join(sorted(conflicts)))

        feed_df = feeds.get(t.get("feed"))
        status = ("conflict" if conflicts else
                  "verified" if done == len(metrics) else
                  "partial" if done else "unverified")
        out.append({
            "id": t["id"], "label": t["label"], "system": t["system"],
            "metrics": metrics, "feed": t.get("feed"),
            "status": status,
            "records_ingested": int(len(feed_df)) if feed_df is not None else 0,
            "metrics_computed": done, "metrics_expected": len(metrics),
            "issues": issues,
        })
    return out


def compute_report_readiness(config, kpi_rows, commentary_sections, risk_items):
    cfg = config.get("report_readiness", {})
    totals = {
        "metrics_completed": (sum(1 for r in kpi_rows if r["computed"]), len(kpi_rows)),
        "commentary_accepted": (
            sum(1 for s in commentary_sections if s.get("status") == "accepted"),
            len(commentary_sections)),
        "risk_items_reviewed": (
            sum(1 for r in risk_items if r.get("reviewed")), len(risk_items)),
        "slide_previewed": (0, len(commentary_sections)),
    }

    counters, score = [], 0.0
    for c in cfg.get("counters", []):
        done, total = totals.get(c["key"], (0, 0))
        weight = float(c.get("weight", 0))
        score += (done / total * weight) if total else weight
        counters.append({
            "key": c["key"], "label": c["label"],
            "done": done, "total": total, "display": f"{done} / {total}",
            "weight": weight, "owner": c.get("owner", "human"),
        })

    grand = float(cfg.get("total", 100))
    score = round(score, 1)
    return {"score": score, "total": grand, "completed": score,
            "pending": round(grand - score, 1), "counters": counters}


def compute_time_saved(config, commentary_sections, risk_items):
    b = config.get("operational_baseline", {})
    n = len(commentary_sections)
    manual = (n * float(b.get("manual_minutes_per_commentary_section", 45))
              + float(b.get("manual_minutes_metric_pack", 90))
              + float(b.get("manual_minutes_risk_review", 40)))
    assisted = n * float(b.get("review_minutes_per_section", 12))
    saved = manual - assisted
    return {
        "manual_minutes": round(manual, 1),
        "assisted_minutes": round(assisted, 1),
        "minutes_saved": round(saved, 1),
        "hours_saved": round(saved / 60, 1),
        "basis": f"{n} commentary sections drafted, metric pack assembled and "
                 f"{len(risk_items)} risk items derived automatically; the finance "
                 f"team's remaining effort is refinement only.",
    }


# ==============================================================================
# INDEPENDENT ARITHMETIC AUDIT
# ==============================================================================
# Recomputes every displayed variance straight from the raw feed, so a bug in the
# table builder cannot put a wrong number in front of a board.

def audit_arithmetic(config, feeds, kpi_rows, periods, derived):
    disc = []
    cur, prior, yoy = periods["current"], periods["qoq"], periods["yoy"]
    catalog = config.get("kpi_catalog", {})

    def close(a, b, tol=0.02):
        return a is not None and b is not None and abs(a - b) <= tol

    for row in kpi_rows:
        kpi = catalog.get(row["metric_key"], {})
        col = kpi.get("column", row["metric_key"])
        c, p_, y = _val(cur, col), _val(prior, col), _val(yoy, col)
        if c is None:
            continue
        if not close(c, row["current_actual"], 1e-9):
            disc.append({"metric": row["metric_key"], "expected": c,
                         "reported": row["current_actual"],
                         "note": "current value differs from the feed"})
        for base, key in ((p_, "qoq_change"), (y, "yoy_change")):
            if base is None or row[key] is None:
                continue
            exp = (c - base) if row["change_type"] == "pp_change" else \
                  ((c - base) / base * 100 if base else None)
            if exp is not None and not close(round(exp, 4), row[key], 0.005):
                disc.append({"metric": f"{row['metric_key']}.{key}",
                             "expected": round(exp, 4), "reported": row[key],
                             "note": "variance differs from independent recompute"})

    seg = feeds.get("segment_breakdown")
    if seg is not None:
        exp_aum = float(seg["aum_cr"].sum())
        rep_aum = _val(cur, "aum_cr")
        if rep_aum and abs(exp_aum - rep_aum) / rep_aum * 100 > 1.0:
            disc.append({"metric": "aum_cr (segment vs reported)",
                         "expected": round(exp_aum, 2), "reported": rep_aum,
                         "note": "segment ledger does not sum to reported AUM"})

    s = derived.get("stage_sum_cr")
    a = _val(cur, "aum_cr")
    if s is not None and a is not None and abs(s - a) > 0.05:
        disc.append({"metric": "stage_sum_cr", "expected": a, "reported": s,
                     "note": "Stage 1+2+3 does not reconcile to AUM"})
    return disc


# ==============================================================================
# ORCHESTRATION - BUILD THE COMPLETE FACT BASE
# ==============================================================================

def build_facts(config, feeds, verbose=True):
    """
    Runs the whole deterministic pipeline and returns the `facts` dict: the
    complete numeric picture of the quarter, and the single source of truth the
    drafted prose is later checked against.
    """
    dfq = feeds.get("quarterly_metrics")
    if dfq is None or dfq.empty:
        raise RuntimeError("quarterly_metrics feed is empty - nothing to report")

    periods = resolve_periods(config, dfq)
    cur, prior, yoy = periods["current"], periods["qoq"], periods["yoy"]

    profile = feeds.get("company_profile", {}) or {}
    rep = config.get("reporting", {})

    meta = {
        "company_name": profile.get("company_name", "Company"),
        "platform": profile.get("platform", "Atlas Hub"),
        "module": profile.get("module", "Investor Reporting"),
        "reporting_quarter": str(cur["reporting_quarter"]) if cur is not None else None,
        "prior_quarter": str(prior["reporting_quarter"]) if prior is not None else None,
        "yoy_quarter": str(yoy["reporting_quarter"]) if yoy is not None else None,
        "forecast_quarter": rep.get("forecast_quarter"),
        "accounting_standard": profile.get("accounting_standard", "Ind AS"),
        "ecl_standard": profile.get("ecl_standard", "Ind AS 109"),
        "currency_symbol": rep.get("currency_symbol", "₹"),
        "reporting_unit": profile.get("reporting_unit", "Crore"),
        "confidentiality": profile.get("confidentiality", ""),
        "audience": "board",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "degraded_run": False,
    }

    kpi_rows = compute_kpi_comparison(config, feeds, periods)
    derived, one_offs = compute_derived(config, feeds, periods)
    budget = compute_budget_variance(config, kpi_rows)
    stages = compute_stage_movement(config, feeds, periods)
    attribution = compute_attribution(config, feeds, kpi_rows)
    recons = compute_reconciliations(config, feeds, periods)
    validations = run_validations(config, periods, derived)
    risks = derive_risk_items(config, feeds, kpi_rows, attribution, recons,
                              validations, derived, periods)
    traceability = compute_source_traceability(config, feeds, kpi_rows, recons, periods)
    audit = audit_arithmetic(config, feeds, kpi_rows, periods, derived)

    if verbose:
        print(f"    [METRICS] {sum(1 for r in kpi_rows if r['computed'])}/{len(kpi_rows)} "
              f"KPIs computed for {meta['reporting_quarter']} "
              f"(QoQ vs {meta['prior_quarter']}, YoY vs {meta['yoy_quarter']})")
        for r in kpi_rows:
            print(f"       - {r['short_label']:<16} {r['current_actual_display']:>10}  "
                  f"QoQ {r['qoq_change_display']:>8} [{r['qoq_sentiment']}]  "
                  f"YoY {r['yoy_change_display']:>8}"
                  + ("" if r["material"] else "  (immaterial)"))
        for key, att in attribution.items():
            flag = "sufficient" if att["sufficient"] else "INSUFFICIENT"
            print(f"    [ATTRIBUTION] {key}: total {att['total_change']} {att['unit']}, "
                  f"{att['explained_share_pct']}% explained by "
                  f"{', '.join(att['named_contributors'][:3])} [{flag}]")
        for rc in recons:
            print(f"    [RECONCILE] {rc['label']}: bottom-up {rc['bottom_up_value']} vs "
                  f"reported {rc['reported_value']} ({rc['variance_pct']}%) "
                  f"[{'BREACH' if rc['breached'] else 'agreed'}]")
        p = sum(1 for v in validations if v["status"] == "PASS")
        f = sum(1 for v in validations if v["status"] == "FAIL")
        print(f"    [VALIDATION] {p} passed, {f} failed, "
              f"{len(validations) - p - f} not evaluable of {len(validations)} rules")
        print(f"    [RISK] {len(risks)} attention item(s) derived: "
              + "; ".join(r["message"] for r in risks[:3]))
        print(f"    [AUDIT] {len(audit)} arithmetic discrepancy(ies)"
              if audit else "    [AUDIT] Clean - every figure reproduced from the raw feed")

    return {
        "meta": meta,
        "kpi_comparison": kpi_rows,
        "budget_variance": budget,
        "stage_movement": stages,
        "risk_attention_items": risks,
        "source_traceability": traceability,
        "attribution": attribution,
        "reconciliations": recons,
        "validation_results": validations,
        "one_off_items": one_offs,
        "derived_metrics": derived,
        "arithmetic_audit": audit,
        "_periods": {"current": meta["reporting_quarter"],
                     "qoq": meta["prior_quarter"],
                     "yoy": meta["yoy_quarter"]},
    }
