"""
==============================================================================
LLM CALCULATION ENGINE - Fund-Raising Document Agent
==============================================================================
The LLM performs the analysis; Python performs the addition.

WHY THIS SPLIT
Direct testing against llama-3.3-70b showed the model transcribes source data
perfectly and describes the correct method perfectly, then mis-adds the columns:
given the exact addends [1.5,0.9,1.2,1.1,0.8,0.5,0.3,0.3,0.1,0.1] it returned
22.1 instead of 6.8 - a 3.2x error that propagated into GNPA%, NNPA% and the
covenant verdicts. 3 of 5 column sums were wrong while every addend and every
stated formula was right.

So the engine runs in two passes and never asks the model to add:

  PASS 1 - PLAN      The LLM decides everything methodological: which row is the
                     current period, which comparator is YoY vs QoQ, which
                     source is authoritative for each metric, which aggregation
                     applies, and the arithmetic expression for every KPI. It
                     emits a machine-executable calculation plan containing no
                     computed numbers at all.

  EXECUTE            Python resolves the plan against the pandas DataFrames -
                     exact aggregation, exact expression evaluation through a
                     restricted AST evaluator.

  PASS 2 - ADJUDICATE The LLM receives the exact computed figures and does the
                     judgement work: covenant PASS/FAIL, validation rule
                     outcomes, cross-source reconciliation commentary, and the
                     CFO insight bullets.

  AUDIT              A deterministic recompute cross-checks the executed figures
                     and re-derives every covenant verdict, so a mislabelled
                     PASS cannot reach the document.

Nothing about the methodology is hardcoded - if the LLM decides GNPA should come
from the segment file rather than the summary column, that is what executes.
==============================================================================
"""

import os
import io
import re
import json
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MODEL = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o",
    "gemini": "gemini-1.5-pro",
}

# Tolerance when cross-checking executed figures against the audit recompute.
ABS_TOL = 0.05      # absolute, for ratios / percentages
REL_TOL = 0.005     # 0.5% relative, for crore amounts

# Rounding is a presentation concern, not a modelling one, and the model gets it
# wrong (it asked for 1dp on DSCR, turning 1.42x into 1.4x). Fixed in Python by
# target type; the plan's own `round` is only honoured if it is more precise.
ROUNDING_POLICY = [
    ("active_loans", 0),
    ("_pct", 2),
    ("dscr", 2), ("debt_to_equity", 2), ("interest_coverage", 2),
    ("_cr", 2),
]
DEFAULT_ROUND = 2

# Sanity envelopes - a plan that produces a figure outside these is wired wrong,
# not merely imprecise. Used to trigger a plan-repair round.
SANITY_BOUNDS = {
    "_yoy_pct": (-100.0, 200.0),
    "_qoq_pct": (-100.0, 200.0),
    "gnpa_pct": (0.0, 100.0),
    "nnpa_pct": (0.0, 100.0),
    "collection_efficiency_pct": (0.0, 100.0),
    "capital_adequacy_pct": (0.0, 100.0),
    "debt_to_equity": (0.0, 20.0),
    "dscr": (0.0, 20.0),
    "interest_coverage": (0.0, 50.0),
}

SOURCE_ALIASES = {
    "financial": "financial_df", "financials": "financial_df",
    "source1": "financial_df", "source_1": "financial_df",
    "historical": "financial_df",
    "borrowings": "borrowings_df", "borrowing": "borrowings_df",
    "facilities": "borrowings_df", "source2": "borrowings_df",
    "source_2": "borrowings_df",
    "portfolio": "portfolio_df", "segments": "portfolio_df",
    "source3": "portfolio_df", "source_3": "portfolio_df",
}


# ==============================================================================
# ENV / PROVIDER
# ==============================================================================

def load_env(env_path=None):
    """
    Reads .env into os.environ, stripping inline `# comment` trailers - the
    original loader did not, so DEFAULT_LLM_PROVIDER was being read as
    "groq  # Options: groq, openai, gemini".
    """
    env_path = env_path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return False
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            val = val.strip()
            if " #" in val:
                val = val.split(" #", 1)[0]
            if "\t#" in val:
                val = val.split("\t#", 1)[0]
            os.environ[key.strip()] = val.strip()
    return True


def resolve_provider():
    provider = (os.getenv("DEFAULT_LLM_PROVIDER") or "groq").strip().lower()
    model = (os.getenv("LLM_MODEL") or "").strip() or DEFAULT_MODEL.get(provider, "")
    return provider, model


class LLMUnavailable(RuntimeError):
    pass


# Per-minute token ceilings differ sharply by model on Groq's free tier
# (llama-3.3-70b: 12k, llama-3.1-8b: 6k) and the request is admitted against
# prompt + max_tokens, so a fixed max_tokens 413s on the smaller model. Size the
# completion budget from what is actually left after the prompt.
MODEL_TPM = {
    "llama-3.3-70b-versatile": 12000,
    "llama-3.1-8b-instant": 6000,
    "openai/gpt-oss-120b": 8000,
}
DEFAULT_TPM = 12000
TPM_SAFETY_MARGIN = 400
MIN_COMPLETION_TOKENS = 1200


def _estimate_tokens(*texts):
    return sum(len(t or "") for t in texts) // 4


def _budget_max_tokens(system_prompt, user_prompt):
    env_override = os.getenv("LLM_MAX_TOKENS")
    if env_override:
        return int(env_override)
    _, model = resolve_provider()
    tpm = int(os.getenv("LLM_TPM_LIMIT") or MODEL_TPM.get(model, DEFAULT_TPM))
    room = tpm - _estimate_tokens(system_prompt, user_prompt) - TPM_SAFETY_MARGIN
    return max(MIN_COMPLETION_TOKENS, min(room, 5000))


def _placeholder(key):
    return (not key) or ("your_" in key.lower()) or key.lower().endswith("_here")


def call_llm(system_prompt, user_prompt, provider=None, model=None,
             temperature=0.0, max_tokens=None):
    """
    Single chat completion returning raw text, JSON mode where supported.

    NOTE: Groq counts prompt + max_tokens against the per-minute token limit when
    admitting a request, so an oversized max_tokens triggers a 413 even when the
    real completion is small. Keep headroom.
    """
    if max_tokens is None:
        max_tokens = _budget_max_tokens(system_prompt, user_prompt)

    p, m = resolve_provider()
    provider = provider or p
    model = model or m

    if provider == "groq":
        key = os.getenv("GROQ_API_KEY")
        if _placeholder(key):
            raise LLMUnavailable("GROQ_API_KEY missing or placeholder in .env")
        from groq import Groq
        resp = Groq(api_key=key).chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=temperature, max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content

    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if _placeholder(key):
            raise LLMUnavailable("OPENAI_API_KEY missing or placeholder in .env")
        from openai import OpenAI
        resp = OpenAI(api_key=key).chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=temperature, max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content

    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if _placeholder(key):
            raise LLMUnavailable("GEMINI_API_KEY missing or placeholder in .env")
        import google.generativeai as genai
        genai.configure(api_key=key)
        gm = genai.GenerativeModel(
            model_name=model, system_instruction=system_prompt,
            generation_config={"temperature": temperature,
                               "max_output_tokens": max_tokens,
                               "response_mime_type": "application/json"},
        )
        return gm.generate_content(user_prompt).text

    raise LLMUnavailable(f"Unknown provider: {provider!r}")


def _extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model response")
    return json.loads(text[start:end + 1])


def _call_with_retries(system_prompt, user_prompt, label, max_retries=2, verbose=True):
    last_err = None
    provider, model = resolve_provider()
    for attempt in range(1, max_retries + 2):
        t0 = time.time()
        try:
            raw = call_llm(system_prompt, user_prompt, provider=provider, model=model)
            parsed = _extract_json(raw)
            if verbose:
                print(f"    [LLM:{label}] OK on attempt {attempt} "
                      f"({round(time.time() - t0, 2)}s, {len(raw):,} chars)")
            return parsed
        except LLMUnavailable:
            raise
        except Exception as e:
            last_err = e
            msg = str(e)
            if verbose:
                print(f"    [LLM:{label}] attempt {attempt} failed: "
                      f"{type(e).__name__}: {msg[:150]}")
            if attempt > max_retries:
                break
            if "rate_limit" in msg or "429" in msg or "413" in msg:
                wait = 20 * attempt
                if verbose:
                    print(f"    [LLM:{label}] rate limited - waiting {wait}s")
                time.sleep(wait)
            else:
                user_prompt += (f"\n\nYour previous reply was rejected ({msg[:200]}). "
                                f"Reply with raw valid JSON only, matching the contract exactly.")
    raise RuntimeError(f"{label} failed after {max_retries + 1} attempts: {last_err}")


# ==============================================================================
# ANCHOR STRIPPING
# ==============================================================================
# system_prompt.md and input_config.yaml are saturated with figures from a
# previous run ("Current: 1.42x", value: 425.0, "debt_to_equity (2.10) <= 4.00").
# Left in place the model copies them verbatim instead of deriving anything -
# observed directly in testing, where it returned the stale Q1 FY26 figures and
# ignored the CSVs entirely. Removed before they reach the model; the persona,
# the formulas and the thresholds all survive.

_STALE_VALUE_KEYS = ("value", "previous_value")


def strip_spec_anchors(spec):
    cleaned = {}
    for group, kpis in (spec or {}).items():
        cleaned[group] = {}
        for name, meta in (kpis or {}).items():
            if isinstance(meta, dict):
                cleaned[group][name] = {k: v for k, v in meta.items()
                                        if k not in _STALE_VALUE_KEYS}
    return cleaned


def strip_rule_anchors(rules):
    out = []
    for r in rules or []:
        r = dict(r)
        if isinstance(r.get("rule"), str):
            r["rule"] = re.sub(r"\s*\([^)]*\)", "", r["rule"]).strip()
        out.append(r)
    return out


def strip_prompt_anchors(text):
    text = re.sub(r"\(\s*Current:[^)]*\)", "", text)
    text = re.sub(r"\(\s*[₹$]\s*[\d,]+(?:\.\d+)?\s*(?:Cr|Crore)[^)]*\)", "", text)
    text = re.sub(r"\(\s*[+-]?\d+(?:\.\d+)?\s*%[^)]*\)", "", text)
    text = re.sub(r"\(\s*\d+(?:\.\d+)?\s*(?:x|hrs)[^)]*\)", "", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _df_to_csv_text(df, columns=None):
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().strip()


# ==============================================================================
# PASS 1 - THE LLM AUTHORS A CALCULATION PLAN
# ==============================================================================

PLAN_CONTRACT = """
You are producing a CALCULATION PLAN, not results. Emit NO computed numbers.
For each metric you choose the METHOD, the SOURCE table and the COLUMN(s).
Python then builds the operands and does the arithmetic exactly.

Return ONE JSON object:

{
  "reporting_period": "<period label of the row you designate as current>",
  "comparison_period_yoy": "<period label of your YoY comparator>",
  "comparison_period_qoq": "<period label of your QoQ comparator>",
  "row_offsets": { "current": -1, "yoy": -5, "qoq": -2 },

  "metrics": [
    { "target": "financial_metrics.revenue_cr",
      "method": "current_value", "source": "financial", "column": "revenue_cr" },
    { "target": "financial_metrics.revenue_yoy_pct",
      "method": "yoy_growth_pct", "source": "financial", "column": "revenue_cr" },
    { "target": "portfolio_metrics.aum_cr",
      "method": "sum", "source": "portfolio", "column": "aum_cr" }
  ],

  "reconciliations": [
    { "field": "aum_cr", "bottom_up_target": "portfolio_metrics.aum_cr",
      "reported_source": "financial", "reported_column": "aum_cr",
      "reported_row": "current", "tolerance_pct": 1.0 }
  ],

  "method_notes": [ "<one line per non-obvious methodological choice>" ]
}

`source` is one of: "financial", "borrowings", "portfolio".
`row` is one of: "current", "yoy", "qoq" - resolved via row_offsets, which are
negative indices into the table (-1 is the last row).

METHOD VOCABULARY - use these names exactly; no other method is executable.

  current_value          source, column
                         -> the column's value on the current row

  prior_value            source, column, row ("yoy" | "qoq")
                         -> the column's value on that comparator row

  yoy_growth_pct         source, column
                         -> (current - yoy) / yoy * 100

  qoq_growth_pct         source, column
                         -> (current - qoq) / qoq * 100

  sum                    source, column
                         -> sum of the column over every row

  mean                   source, column
  weighted_mean          source, column, weight_column
                         -> sum(column * weight) / sum(weight)

  ratio_of_sums_pct      source, numerator_column, denominator_column
                         -> sum(numerator) / sum(denominator) * 100

  net_ratio_of_sums_pct  source, numerator_column, deduction_column,
                         denominator_column
                         -> (sum(numerator) - sum(deduction))
                            / (sum(denominator) - sum(deduction)) * 100

  sum_over_scalar        source, column,
                         denominator_source, denominator_column,
                         denominator_row ("current" | "yoy" | "qoq")
                         -> sum(column) / that single value

Choose the method that expresses your intent; do not try to combine methods or
write arithmetic yourself. Note that the portfolio segment table is a snapshot
with no period column - any quarter-on-quarter movement must be read from the
financial table.

REQUIRED TARGETS - emit exactly one metric entry for each of these paths:
  financial_metrics.revenue_cr, .revenue_yoy_pct, .ebitda_cr, .ebitda_yoy_pct,
  financial_metrics.pat_cr, .pat_yoy_pct, .total_debt_cr, .total_debt_yoy_pct,
  financial_metrics.total_debt_qoq_pct, .net_worth_cr, .net_worth_yoy_pct
  portfolio_metrics.aum_cr, .aum_qoq_pct, .disbursement_cr, .collection_cr,
  portfolio_metrics.gnpa_pct, .gnpa_previous_pct, .nnpa_pct,
  portfolio_metrics.collection_efficiency_pct, .active_loans
  key_ratios.dscr, .debt_to_equity, .debt_to_equity_previous, .interest_coverage,
  key_ratios.capital_adequacy_pct, .roa_pct, .roe_pct

PERIOD SELECTION
- current = the LAST row of the financial table (sorted oldest first).
- yoy     = the same quarter one fiscal year earlier (4 rows back).
- qoq     = the immediately preceding row.

SOURCE AUTHORITY - this is a reconciliation exercise, so choose deliberately
- P&L / balance sheet lines and the reported ratios DSCR, ICR, CRAR, ROA, ROE
  come from the financial table.
- AUM, GNPA %, NNPA %, collection efficiency and active loan accounts must be
  built BOTTOM-UP from the portfolio segment table, because the granular file is
  the evidence and the summary column is the claim being verified:
      gnpa_pct = sum(gnpa_90_plus_dpd_cr) / sum(aum_cr) * 100
      nnpa_pct = (sum(gnpa_90_plus_dpd_cr) - sum(provision_amount_cr))
                 / (sum(aum_cr) - sum(provision_amount_cr)) * 100
      collection_efficiency_pct = AUM-weighted mean
- Total borrowings, and therefore debt_to_equity, come BOTTOM-UP from the
  borrowing facility table: sum(outstanding_balance_cr) / net worth (current).
- The financial table ALSO carries summary columns for aum_cr, gnpa_pct,
  nnpa_pct and total_debt_cr. Do not use them as the computed value - instead
  register each as a `reconciliations` entry so the variance against your
  bottom-up figure is measured and reported.

Output raw JSON only. No markdown fences, no prose outside the JSON.
"""


def _describe_source(df, name, description):
    """
    Schema-only description. A calculation PLAN depends on the shape of the data,
    never on its values, so sending the rows would only waste the token budget and
    give the model figures to copy instead of methods to choose.
    """
    lines = [f"SOURCE `{name}` - {description}",
             f"  rows: {len(df)}",
             f"  columns: {', '.join(df.columns)}"]
    if "period" in df.columns:
        lines.append(f"  period sequence (oldest to newest): "
                     f"{', '.join(str(p) for p in df['period'])}")
    return "\n".join(lines)


def build_plan_prompt(config, dataframes, insights_prompt=None):
    parts = ["# TASK\n"
             "Author the calculation plan for this NBFC's Fund-Raising Data Pack. "
             "Decide the reporting period, the authoritative source for each metric, "
             "and the method for every KPI. Emit the plan only - Python will "
             "execute it against the data.\n",
             "# AVAILABLE SOURCES (schema only - values are deliberately withheld, "
             "since a plan depends on structure, not figures)\n"]

    for key, name, desc in (
        ("financial_df", "financial", "historical quarterly financials, oldest first"),
        ("borrowings_df", "borrowings", "lender facility schedule, one row per tranche"),
        ("portfolio_df", "portfolio", "loan portfolio quality, one row per product segment "
                                      "(a point-in-time snapshot with no period column)"),
    ):
        df = dataframes.get(key)
        if df is not None:
            parts.append(_describe_source(df, name, desc) + "\n")

    parts.append("# OUTPUT CONTRACT\n" + PLAN_CONTRACT)
    return "\n".join(parts)


# ==============================================================================
# PLAN EXECUTION - EXACT ARITHMETIC IN PYTHON
# ==============================================================================

def _rounding_for(field):
    for suffix, nd in ROUNDING_POLICY:
        if field == suffix or field.endswith(suffix):
            return nd
    return DEFAULT_ROUND


def _bounds_for(field):
    for suffix, bounds in SANITY_BOUNDS.items():
        if field == suffix or field.endswith(suffix):
            return bounds
    return None


def _resolve_source(name, dataframes):
    key = SOURCE_ALIASES.get(str(name).strip().lower().replace(" ", "_"))
    if key is None:
        raise KeyError(f"unknown source {name!r}")
    df = dataframes.get(key)
    if df is None:
        raise KeyError(f"source {name!r} not loaded")
    return df


def execute_plan(plan, dataframes, verbose=True):
    """
    Runs the LLM's plan against the DataFrames. Returns (values, symbols, errors)
    where `values` is the nested metric dict keyed by the plan's target paths.
    """
    errors = []
    symbols = {}
    trace = {}

    offsets = plan.get("row_offsets") or {}
    row_idx = {}
    for name, default in (("current", -1), ("yoy", -5), ("qoq", -2)):
        try:
            v = int(offsets.get(name, default))
        except (TypeError, ValueError):
            v = default
        if v >= 0:
            errors.append(f"row_offset {name}={v} is not a negative index; using {default}")
            v = default
        row_idx[name] = v

    def cell(source, column, row="current"):
        df = _resolve_source(source, dataframes)
        if column not in df.columns:
            raise KeyError(f"column {column!r} not in source {source!r}")
        idx = row_idx.get(str(row).lower(), -1)
        if abs(idx) > len(df):
            raise IndexError(f"row offset {idx} out of range for {len(df)} rows")
        return float(df.iloc[idx][column])

    def col(source, column):
        df = _resolve_source(source, dataframes)
        if column not in df.columns:
            raise KeyError(f"column {column!r} not in source {source!r}")
        return df, df[column]

    def apply_method(m):
        """Executes one plan entry. All operands are built here, never by the model."""
        method = str(m.get("method", "")).strip().lower()
        src = m.get("source")

        if method == "current_value":
            return cell(src, m["column"], "current")

        if method == "prior_value":
            return cell(src, m["column"], m.get("row", "qoq"))

        if method in ("yoy_growth_pct", "qoq_growth_pct"):
            comparator = "yoy" if method.startswith("yoy") else "qoq"
            cur = cell(src, m["column"], "current")
            prior = cell(src, m["column"], comparator)
            if prior == 0:
                raise ZeroDivisionError(f"{comparator} base for {m['column']!r} is zero")
            return (cur - prior) / prior * 100

        if method == "sum":
            return float(col(src, m["column"])[1].sum())

        if method == "mean":
            return float(col(src, m["column"])[1].mean())

        if method == "weighted_mean":
            df, series = col(src, m["column"])
            w = m.get("weight_column")
            if w not in df.columns:
                raise KeyError(f"weight_column {w!r} not in source {src!r}")
            return float((series * df[w]).sum() / df[w].sum())

        if method == "ratio_of_sums_pct":
            _, num = col(src, m["numerator_column"])
            _, den = col(src, m["denominator_column"])
            return float(num.sum()) / float(den.sum()) * 100

        if method == "net_ratio_of_sums_pct":
            _, num = col(src, m["numerator_column"])
            _, ded = col(src, m["deduction_column"])
            _, den = col(src, m["denominator_column"])
            return ((float(num.sum()) - float(ded.sum()))
                    / (float(den.sum()) - float(ded.sum())) * 100)

        if method == "sum_over_scalar":
            _, series = col(src, m["column"])
            denom = cell(m["denominator_source"], m["denominator_column"],
                         m.get("denominator_row", "current"))
            if denom == 0:
                raise ZeroDivisionError("denominator is zero")
            return float(series.sum()) / denom

        raise ValueError(f"unknown method {method!r}")

    # --- metrics ---
    values = {}
    insane = []
    for m in plan.get("metrics", []) or []:
        target = m.get("target", "")
        try:
            section, _, field = target.partition(".")
            if not section or not field:
                raise ValueError(f"malformed target {target!r}")
            raw = apply_method(m)

            nd = _rounding_for(field)
            val = int(round(raw)) if field == "active_loans" else round(raw, nd)

            bounds = _bounds_for(field)
            if bounds and not (bounds[0] <= raw <= bounds[1]):
                insane.append(
                    f"{target} = {round(raw, 4)} is outside the plausible range "
                    f"{bounds}; method {m.get('method')!r} on "
                    f"{m.get('source')}.{m.get('column') or m.get('numerator_column')} "
                    f"is the wrong choice for this metric")

            values.setdefault(section, {})[field] = val
            symbols[target] = raw
            trace[target] = {k: v for k, v in m.items() if k != "target"}
        except Exception as e:
            errors.append(f"metric {target!r}: {e}")

    # --- reconciliations ---
    recons = []
    for r in plan.get("reconciliations", []) or []:
        try:
            bt = r.get("bottom_up_target")
            if bt not in symbols:
                raise KeyError(f"bottom_up_target {bt!r} was not computed")
            bottom = symbols[bt]
            reported = cell(r["reported_source"], r["reported_column"],
                            r.get("reported_row", "current"))
            tol = float(r.get("tolerance_pct", 1.0))
            denom = abs(reported) if reported else 1.0
            variance_pct = abs(bottom - reported) / denom * 100
            recons.append({
                "field": r.get("field"),
                "bottom_up_value": round(bottom, 4),
                "bottom_up_basis": bt,
                "reported_value": round(reported, 4),
                "reported_basis": f"{r['reported_source']}.{r['reported_column']}",
                "variance_pct": round(variance_pct, 2),
                "tolerance_pct": tol,
                "breached": variance_pct > tol,
            })
        except Exception as e:
            errors.append(f"reconciliation {r.get('field')!r}: {e}")

    errors.extend(insane)

    if verbose:
        print(f"    [PLAN EXEC] {len(symbols)} symbols resolved, "
              f"{sum(len(v) for v in values.values())} metrics computed, "
              f"{len(recons)} reconciliations")
        for rc in recons:
            flag = "BREACH" if rc["breached"] else "ok"
            print(f"       - {rc['field']}: bottom-up {rc['bottom_up_value']} vs "
                  f"reported {rc['reported_value']} ({rc['variance_pct']}%) [{flag}]")
        if errors:
            print(f"    [PLAN EXEC] {len(errors)} plan error(s):")
            for e in errors:
                print(f"       ! {e}")

    return values, symbols, recons, errors


# ==============================================================================
# PASS 2 - THE LLM ADJUDICATES THE EXACT FIGURES
# ==============================================================================

ADJUDICATE_CONTRACT = """
Return ONE JSON object:

{
  "covenant_audit": [
    { "name": "<ratio name>", "computed": <the exact figure given to you>,
      "threshold": <number>, "rule": "<e.g. >= 1.25>", "unit": "x" | "%",
      "status": "PASS" | "FAIL",
      "headroom": "<distance to the limit, in the ratio's own units>" }
  ],
  "validation_results": [
    { "id": "<V01..>", "name": "<rule name>", "rule": "<rule expression>",
      "observed": "<the figures you compared>",
      "status": "PASS" | "FAIL" | "NOT_EVALUABLE" }
  ],
  "data_conflicts": [
    { "field": "<metric>", "source_a": "<name and value>", "source_b": "<name and value>",
      "severity": "High" | "Medium" | "Low", "note": "<one line>" }
  ],
  "review_items": [
    { "field_name": "<field>", "issue_type": "Missing" | "Outdated" | "Conflict",
      "recommended_action": "<what the finance team should do>" }
  ],
  "ai_insights": [ "<4 to 5 executive bullets>" ]
}

RULES
- Use ONLY the exact figures supplied below. Do not recompute or adjust them,
  and do not introduce any number that is not derivable from them.
- Covenant status must follow arithmetically from the computed value vs the
  threshold. Never mark a breach as PASS.
- Evaluate EVERY rule in the catalog. If a rule cannot be evaluated from the
  figures available, use "NOT_EVALUABLE" - never guess PASS.
- Every reconciliation marked breached MUST appear in data_conflicts AND as a
  review_item with issue_type "Conflict", quoting both values.
- Insight bullets must quote the exact figures.
- Raw JSON only, no markdown fences.
"""


def build_adjudication_prompt(config, values, recons, insights_prompt, plan):
    rules = strip_rule_anchors(config.get("validation_rules", []))
    ratio_spec = strip_spec_anchors({"key_ratios_kpi": config.get("key_ratios_kpi", {})})

    parts = [
        "# TASK\n"
        "The figures below were computed by executing your calculation plan against "
        "the source data. Adjudicate them: audit the covenants, evaluate the "
        "validation catalog, report the cross-source variances, and write the CFO "
        "commentary.\n",
        f"# REPORTING PERIOD\n{plan.get('reporting_period')} "
        f"(YoY vs {plan.get('comparison_period_yoy')}, "
        f"QoQ vs {plan.get('comparison_period_qoq')})\n",
        "# COMPUTED FIGURES (exact - use verbatim)\n```json\n"
        + json.dumps(values, indent=2) + "\n```\n",
        "# COVENANT THRESHOLDS\n```json\n"
        + json.dumps(ratio_spec, separators=(",", ":")) + "\n```\n",
        f"# VALIDATION RULE CATALOG ({len(rules)} rules - evaluate every one)\n"
        "```json\n" + json.dumps(rules, separators=(",", ":")) + "\n```\n",
        "# CROSS-SOURCE RECONCILIATION RESULTS\n"
        "Bottom-up figures were aggregated from the granular files; reported "
        "figures are the summary columns in the financial statements. Any entry "
        "with breached=true is an unreconciled variance.\n```json\n"
        + json.dumps(recons, indent=2) + "\n```\n",
        "# CFO INSIGHT RULES\n" + (insights_prompt or "").strip() + "\n",
        "# OUTPUT CONTRACT\n" + ADJUDICATE_CONTRACT,
    ]
    return "\n".join(parts)


REPAIR_CONTRACT = """
Your calculation plan executed, but some metrics came out wrong. Diagnose each
problem and return a CORRECTED plan.

Return the SAME JSON structure as before (reporting_period, comparison_period_yoy,
comparison_period_qoq, row_offsets, metrics, reconciliations, method_notes),
using the SAME method vocabulary. Return the COMPLETE plan, not a patch.

Fixing guidance
- Change ONLY the entries named below. Copy every other entry through unchanged -
  they produced correct figures and rewriting them introduces new faults.
- A growth % in the thousands means the method was applied to the wrong source:
  a quarter-on-quarter move cannot be read from the portfolio segment snapshot,
  which has no period column. Read it from the financial table instead.
- A total that equals a single row's value means you used `current_value` where
  you need `sum` over the whole table.
- A percentage that should be a share of the portfolio needs
  `ratio_of_sums_pct`, not a growth method.
- Do not change your source-authority decisions unless they caused the error.

Raw JSON only.
"""


def build_repair_prompt(plan, values, problems, symbols):
    return "\n".join([
        "# YOUR PREVIOUS PLAN\n```json\n"
        + json.dumps({k: plan.get(k) for k in
                      ("reporting_period", "comparison_period_yoy",
                       "comparison_period_qoq", "row_offsets", "metrics",
                       "reconciliations")}, separators=(",", ":")) + "\n```\n",
        "# WHAT IT PRODUCED\n```json\n" + json.dumps(values, indent=2) + "\n```\n",
        "# PROBLEMS TO FIX (leave every other entry exactly as it was)\n- "
        + "\n- ".join(problems) + "\n",
        "# OUTPUT CONTRACT\n" + REPAIR_CONTRACT,
    ])


def complete_missing_metrics(values, dataframes, verbose=True):
    """
    Backstop after the repair rounds: any required metric the model never managed
    to compute is filled from the fixed fallback plan, so the payload is always
    complete and schema-valid regardless of model capability. Returns the list of
    targets that had to be filled.
    """
    filled = []
    missing = [m for m in FALLBACK_PLAN["metrics"]
               if _get_path(values, m["target"]) is None]
    if not missing:
        return filled
    patch_plan = {"row_offsets": FALLBACK_PLAN["row_offsets"], "metrics": missing}
    patch_values, _, _, errs = execute_plan(patch_plan, dataframes, verbose=False)
    for section, fields in patch_values.items():
        for field, val in fields.items():
            values.setdefault(section, {})[field] = val
            filled.append(f"{section}.{field}")
    if verbose and filled:
        print(f"    [BACKSTOP] {len(filled)} metric(s) the model could not compute "
              f"were filled deterministically: {', '.join(filled)}")
        for e in errs:
            print(f"       ! backstop {e}")
    return filled


def enforce_covenant_verdicts(covenants, config, values, verbose=True):
    """
    The LLM chooses which covenants matter and narrates the headroom; the PASS/FAIL
    bit is arithmetic and is settled here. Thresholds missing from the model's
    reply are backfilled from input_config.yaml. Returns the list of corrections.
    """
    ratio_kpis = config.get("key_ratios_kpi", {}) or {}
    by_label = {}
    for key, meta in ratio_kpis.items():
        thr = (meta or {}).get("covenant_threshold")
        rule = (meta or {}).get("covenant_rule") or ""
        if thr is not None:
            by_label[key] = (thr, "<=" if rule.strip().startswith("<=") else ">=")

    corrections = []
    for cov in covenants or []:
        name = str(cov.get("name", "")).lower()
        match = next((k for k in by_label
                      if k.replace("_", " ") in name
                      or k in name
                      or (k == "capital_adequacy_pct" and "crar" in name)
                      or (k == "debt_to_equity" and "equity" in name)
                      or (k == "interest_coverage" and "interest" in name)), None)
        if match:
            thr, op = by_label[match]
            if cov.get("threshold") in (None, "", 0):
                cov["threshold"] = thr
            if not str(cov.get("rule", "")).strip().rstrip(">=<"):
                cov["rule"] = f"{op} {cov['threshold']}"
        try:
            val, thr = float(cov.get("computed")), float(cov.get("threshold"))
        except (TypeError, ValueError):
            continue
        op = "<=" if str(cov.get("rule", "")).strip().startswith("<=") else ">="
        cov["rule"] = f"{op} {thr}"
        correct = "PASS" if (val >= thr if op == ">=" else val <= thr) else "FAIL"
        if cov.get("status") != correct:
            corrections.append(
                f"{cov.get('name')}: model said {cov.get('status')}, arithmetic says "
                f"{correct} ({val} {op} {thr})")
            cov["status"] = correct
            cov["status_corrected"] = True
    if verbose and corrections:
        print(f"    [COVENANT ENFORCEMENT] {len(corrections)} verdict(s) corrected:")
        for c in corrections:
            print(f"       ! {c}")
    return corrections


def _collect_problems(plan_errors, audit_discrepancies):
    problems = list(plan_errors)
    for d in audit_discrepancies:
        problems.append(
            f"{d['metric']}: plan produced {d['llm_value']}, independent recompute "
            f"gives {d['expected']} - the expression or its operands are wrong")
    return problems


# ==============================================================================
# ORCHESTRATION
# ==============================================================================

def run_llm_analysis(config, dataframes, system_prompt, insights_prompt,
                     max_retries=2, verbose=True):
    """
    Two-pass LLM analysis. Returns (result, meta).
    """
    provider, model = resolve_provider()
    system_prompt = strip_prompt_anchors(system_prompt or "")

    if verbose:
        print(f"    [LLM] Provider: {provider} | Model: {model}")
        print("    [LLM] Stale-value anchors stripped from persona, KPI spec and rules")

    t0 = time.time()

    # ---- PASS 1: plan ----
    plan_prompt = build_plan_prompt(config, dataframes, insights_prompt)
    if verbose:
        print(f"    [LLM:PLAN] Requesting calculation plan "
              f"(~{(len(plan_prompt) + len(system_prompt)) // 4:,} tokens)")
    plan = _call_with_retries(system_prompt, plan_prompt, "PLAN",
                              max_retries=max_retries, verbose=verbose)
    if verbose:
        print(f"    [LLM:PLAN] Period: {plan.get('reporting_period')} | "
              f"offsets {plan.get('row_offsets')} | "
              f"{len(plan.get('scalars', []))} scalars, "
              f"{len(plan.get('aggregations', []))} aggregations, "
              f"{len(plan.get('metrics', []))} metrics")

    # ---- EXECUTE, then let the LLM repair its own plan if it mis-wired one ----
    values, symbols, recons, plan_errors = execute_plan(plan, dataframes, verbose=verbose)
    discrepancies = audit_llm_arithmetic({**values, "covenant_audit": []},
                                         dataframes, verbose=False)
    repairs = 0
    max_repairs = int(os.getenv("LLM_MAX_PLAN_REPAIRS", "2"))
    while (plan_errors or discrepancies) and repairs < max_repairs:
        problems = _collect_problems(plan_errors, discrepancies)
        repairs += 1
        if verbose:
            print(f"    [PLAN REPAIR {repairs}/{max_repairs}] {len(problems)} problem(s) "
                  f"fed back to the model for plan revision")
        try:
            revised = _call_with_retries(
                system_prompt, build_repair_prompt(plan, values, problems, symbols),
                f"REPAIR{repairs}", max_retries=1, verbose=verbose)
            # A repair reply sometimes drops the descriptive fields; keep the
            # originals so the period labels survive the round trip.
            plan = {**plan, **{k: v for k, v in revised.items() if v}}
        except Exception as e:
            if verbose:
                print(f"    [PLAN REPAIR {repairs}] revision failed, keeping prior plan: {e}")
            break
        values, symbols, recons, plan_errors = execute_plan(plan, dataframes, verbose=verbose)
        discrepancies = audit_llm_arithmetic({**values, "covenant_audit": []},
                                             dataframes, verbose=False)

    # Backstop: any required metric the model never managed to express is filled
    # deterministically, so an imperfect plan degrades one figure rather than
    # invalidating the whole payload.
    backstopped = complete_missing_metrics(values, dataframes, verbose=verbose)

    if verbose:
        print(f"    [PLAN] Settled after {repairs} repair round(s): "
              f"{len(plan_errors)} plan error(s), {len(discrepancies)} discrepancy(ies), "
              f"{len(backstopped)} backstopped")

    # ---- PASS 2: adjudicate ----
    adj_prompt = build_adjudication_prompt(config, values, recons, insights_prompt, plan)
    if verbose:
        print(f"    [LLM:JUDGE] Requesting adjudication "
              f"(~{(len(adj_prompt) + len(system_prompt)) // 4:,} tokens)")
    adjudication = _call_with_retries(system_prompt, adj_prompt, "JUDGE",
                                      max_retries=max_retries, verbose=verbose)

    covenants = adjudication.get("covenant_audit", [])
    corrections = enforce_covenant_verdicts(covenants, config, values, verbose=verbose)

    result = {
        "backstopped_metrics": backstopped,
        "covenant_corrections": corrections,
        "reporting_period": plan.get("reporting_period"),
        "comparison_period_yoy": plan.get("comparison_period_yoy"),
        "comparison_period_qoq": plan.get("comparison_period_qoq"),
        "financial_metrics": values.get("financial_metrics", {}),
        "portfolio_metrics": values.get("portfolio_metrics", {}),
        "key_ratios": values.get("key_ratios", {}),
        "covenant_audit": covenants,
        "validation_results": adjudication.get("validation_results", []),
        "data_conflicts": adjudication.get("data_conflicts", []),
        "review_items": adjudication.get("review_items", []),
        "ai_insights": adjudication.get("ai_insights", []),
        "reconciliations": recons,
        "method_notes": plan.get("method_notes", []),
        "plan_errors": plan_errors,
    }
    meta = {
        "provider": provider, "model": model,
        "latency_s": round(time.time() - t0, 2),
        "passes": 2 + repairs,
        "repair_rounds": repairs,
        "symbols_resolved": len(symbols),
        "plan_errors": len(plan_errors),
    }
    return result, meta


# ==============================================================================
# FALLBACK - RUNS WHEN THE LLM IS UNREACHABLE
# ==============================================================================
# The agent is LLM-driven by design, but a missing key, an exhausted quota or a
# network failure must not take the document pipeline down with it. The fallback
# runs the SAME executor over a fixed plan, so the arithmetic is identical; only
# the judgement and the narrative are mechanical. Output is clearly labelled so
# nobody mistakes a degraded run for a full one.

FALLBACK_PLAN = {
    "reporting_period": None,          # filled from the data at run time
    "row_offsets": {"current": -1, "yoy": -5, "qoq": -2},
    "metrics": [
        {"target": "financial_metrics.revenue_cr", "method": "current_value", "source": "financial", "column": "revenue_cr"},
        {"target": "financial_metrics.revenue_yoy_pct", "method": "yoy_growth_pct", "source": "financial", "column": "revenue_cr"},
        {"target": "financial_metrics.ebitda_cr", "method": "current_value", "source": "financial", "column": "ebitda_cr"},
        {"target": "financial_metrics.ebitda_yoy_pct", "method": "yoy_growth_pct", "source": "financial", "column": "ebitda_cr"},
        {"target": "financial_metrics.pat_cr", "method": "current_value", "source": "financial", "column": "pat_cr"},
        {"target": "financial_metrics.pat_yoy_pct", "method": "yoy_growth_pct", "source": "financial", "column": "pat_cr"},
        {"target": "financial_metrics.total_debt_cr", "method": "sum", "source": "borrowings", "column": "outstanding_balance_cr"},
        {"target": "financial_metrics.total_debt_yoy_pct", "method": "yoy_growth_pct", "source": "financial", "column": "total_debt_cr"},
        {"target": "financial_metrics.total_debt_qoq_pct", "method": "qoq_growth_pct", "source": "financial", "column": "total_debt_cr"},
        {"target": "financial_metrics.net_worth_cr", "method": "current_value", "source": "financial", "column": "net_worth_cr"},
        {"target": "financial_metrics.net_worth_yoy_pct", "method": "yoy_growth_pct", "source": "financial", "column": "net_worth_cr"},
        {"target": "portfolio_metrics.aum_cr", "method": "sum", "source": "portfolio", "column": "aum_cr"},
        {"target": "portfolio_metrics.aum_qoq_pct", "method": "qoq_growth_pct", "source": "financial", "column": "aum_cr"},
        {"target": "portfolio_metrics.disbursement_cr", "method": "current_value", "source": "financial", "column": "disbursement_cr"},
        {"target": "portfolio_metrics.collection_cr", "method": "current_value", "source": "financial", "column": "collection_cr"},
        {"target": "portfolio_metrics.gnpa_pct", "method": "ratio_of_sums_pct", "source": "portfolio",
         "numerator_column": "gnpa_90_plus_dpd_cr", "denominator_column": "aum_cr"},
        {"target": "portfolio_metrics.gnpa_previous_pct", "method": "prior_value", "source": "financial", "column": "gnpa_pct", "row": "qoq"},
        {"target": "portfolio_metrics.nnpa_pct", "method": "net_ratio_of_sums_pct", "source": "portfolio",
         "numerator_column": "gnpa_90_plus_dpd_cr", "deduction_column": "provision_amount_cr", "denominator_column": "aum_cr"},
        {"target": "portfolio_metrics.collection_efficiency_pct", "method": "weighted_mean", "source": "portfolio",
         "column": "collection_efficiency_pct", "weight_column": "aum_cr"},
        {"target": "portfolio_metrics.active_loans", "method": "sum", "source": "portfolio", "column": "active_accounts"},
        {"target": "key_ratios.dscr", "method": "current_value", "source": "financial", "column": "dscr"},
        {"target": "key_ratios.debt_to_equity", "method": "sum_over_scalar", "source": "borrowings",
         "column": "outstanding_balance_cr", "denominator_source": "financial",
         "denominator_column": "net_worth_cr", "denominator_row": "current"},
        {"target": "key_ratios.debt_to_equity_previous", "method": "prior_value", "source": "financial", "column": "debt_to_equity", "row": "qoq"},
        {"target": "key_ratios.interest_coverage", "method": "current_value", "source": "financial", "column": "interest_coverage"},
        {"target": "key_ratios.capital_adequacy_pct", "method": "current_value", "source": "financial", "column": "capital_adequacy_pct"},
        {"target": "key_ratios.roa_pct", "method": "current_value", "source": "financial", "column": "roa_pct"},
        {"target": "key_ratios.roe_pct", "method": "current_value", "source": "financial", "column": "roe_pct"},
    ],
    "reconciliations": [
        {"field": "aum_cr", "bottom_up_target": "portfolio_metrics.aum_cr",
         "reported_source": "financial", "reported_column": "aum_cr", "tolerance_pct": 1.0},
        {"field": "total_debt_cr", "bottom_up_target": "financial_metrics.total_debt_cr",
         "reported_source": "financial", "reported_column": "total_debt_cr", "tolerance_pct": 1.0},
        {"field": "gnpa_pct", "bottom_up_target": "portfolio_metrics.gnpa_pct",
         "reported_source": "financial", "reported_column": "gnpa_pct", "tolerance_pct": 1.0},
        {"field": "nnpa_pct", "bottom_up_target": "portfolio_metrics.nnpa_pct",
         "reported_source": "financial", "reported_column": "nnpa_pct", "tolerance_pct": 1.0},
    ],
    "method_notes": [
        "DEGRADED RUN - the LLM was unreachable, so this fixed plan was executed instead.",
        "Portfolio and leverage figures aggregated bottom-up from the granular files.",
    ],
}

_COVENANT_FIELDS = [
    ("dscr", "Debt Service Coverage Ratio (DSCR)", "key_ratios.dscr", ">=", "x"),
    ("debt_to_equity", "Debt-to-Equity Ratio", "key_ratios.debt_to_equity", "<=", "x"),
    ("interest_coverage", "Interest Coverage Ratio (ICR)", "key_ratios.interest_coverage", ">=", "x"),
    ("capital_adequacy_pct", "Capital Adequacy Ratio (CRAR)", "key_ratios.capital_adequacy_pct", ">=", "%"),
]


def _get_path(values, path):
    section, _, field = path.partition(".")
    return (values.get(section) or {}).get(field)


def fallback_adjudication(config, values, recons):
    """Mechanical covenant/validation verdicts for a degraded run."""
    ratio_kpis = config.get("key_ratios_kpi", {}) or {}
    covenants = []
    for key, label, path, op, unit in _COVENANT_FIELDS:
        val = _get_path(values, path)
        thr = (ratio_kpis.get(key) or {}).get("covenant_threshold")
        if val is None or thr is None:
            continue
        ok = val >= thr if op == ">=" else val <= thr
        gap = abs(val - thr)
        covenants.append({
            "name": label, "computed": val, "threshold": thr,
            "rule": f"{op} {thr}", "unit": unit,
            "status": "PASS" if ok else "FAIL",
            "headroom": (f"{round(gap, 2)}{unit} "
                         f"{'above the floor' if op == '>=' else 'below the ceiling'}"
                         if ok else f"{round(gap, 2)}{unit} BEYOND the limit"),
        })

    port = values.get("portfolio_metrics", {})
    ratios = values.get("key_ratios", {})
    observed = {
        "debt_to_equity": ratios.get("debt_to_equity"),
        "capital_adequacy_pct": ratios.get("capital_adequacy_pct"),
        "dscr": ratios.get("dscr"),
        "interest_coverage": ratios.get("interest_coverage"),
        "gnpa_pct": port.get("gnpa_pct"),
        "nnpa_pct": port.get("nnpa_pct"),
        "collection_efficiency_pct": port.get("collection_efficiency_pct"),
    }
    limits = {"debt_to_equity": ("<=", 4.0), "capital_adequacy_pct": (">=", 15.0),
              "dscr": (">=", 1.25), "interest_coverage": (">=", 2.0),
              "gnpa_pct": ("<=", 3.0), "collection_efficiency_pct": (">=", 90.0)}

    results = []
    for rule in config.get("validation_rules", []) or []:
        rid, name = rule.get("id"), rule.get("name", "")
        matched = next((f for f in limits if f in (rule.get("rule") or "")), None)
        if matched and observed.get(matched) is not None:
            op, thr = limits[matched]
            val = observed[matched]
            ok = val >= thr if op == ">=" else val <= thr
            results.append({"id": rid, "name": name, "rule": rule.get("rule"),
                            "observed": f"{matched} = {val} ({op} {thr})",
                            "status": "PASS" if ok else "FAIL"})
        elif "nnpa_pct" in (rule.get("rule") or "") and observed["nnpa_pct"] is not None:
            ok = observed["nnpa_pct"] < observed["gnpa_pct"]
            results.append({"id": rid, "name": name, "rule": rule.get("rule"),
                            "observed": f"nnpa {observed['nnpa_pct']} vs gnpa {observed['gnpa_pct']}",
                            "status": "PASS" if ok else "FAIL"})
        else:
            results.append({"id": rid, "name": name, "rule": rule.get("rule"),
                            "observed": "required inputs not present in the source data",
                            "status": "NOT_EVALUABLE"})

    fin = values.get("financial_metrics", {})
    insights = [
        f"AUM stands at Rs.{port.get('aum_cr')} Cr, {port.get('aum_qoq_pct')}% QoQ.",
        f"Total borrowings of Rs.{fin.get('total_debt_cr')} Cr give a debt/equity of "
        f"{ratios.get('debt_to_equity')}x.",
        f"GNPA at {port.get('gnpa_pct')}% and NNPA at {port.get('nnpa_pct')}% on a "
        f"bottom-up basis.",
        f"DSCR of {ratios.get('dscr')}x against the 1.25x covenant floor.",
        "Generated without LLM commentary - the analysis engine was unreachable.",
    ]

    conflicts = [{
        "field": rc["field"],
        "source_a": f"{rc['reported_basis']}: {rc['reported_value']}",
        "source_b": f"bottom-up: {rc['bottom_up_value']}",
        "severity": "High" if rc["variance_pct"] > 5 else "Medium",
        "note": f"{rc['variance_pct']}% variance between sources",
    } for rc in recons if rc.get("breached")]

    return {"covenant_audit": covenants, "validation_results": results,
            "data_conflicts": conflicts, "review_items": [], "ai_insights": insights}


def run_fallback_analysis(config, dataframes, reason, verbose=True):
    if verbose:
        print(f"    [FALLBACK] LLM unavailable ({reason}).")
        print("    [FALLBACK] Executing the fixed deterministic plan instead - "
              "arithmetic is identical, judgement and narrative are mechanical.")
    plan = dict(FALLBACK_PLAN)
    values, symbols, recons, errors = execute_plan(plan, dataframes, verbose=verbose)
    adj = fallback_adjudication(config, values, recons)

    period = None
    fin = dataframes.get("financial_df")
    if fin is not None and "period" in fin.columns:
        period = str(fin.iloc[-1]["period"])

    return {
        "reporting_period": period,
        "financial_metrics": values.get("financial_metrics", {}),
        "portfolio_metrics": values.get("portfolio_metrics", {}),
        "key_ratios": values.get("key_ratios", {}),
        "reconciliations": recons,
        "method_notes": plan["method_notes"],
        "plan_errors": errors,
        "degraded": True,
        **adj,
    }, {"provider": "none (fallback)", "model": "deterministic plan",
        "passes": 0, "repair_rounds": 0, "degraded": True, "reason": str(reason)}


# ==============================================================================
# DETERMINISTIC AUDIT
# ==============================================================================

def _close(a, b):
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if abs(a - b) <= ABS_TOL:
        return True
    denom = max(abs(a), abs(b))
    return denom > 0 and abs(a - b) / denom <= REL_TOL


def audit_llm_arithmetic(llm_out, dataframes, verbose=True):
    """
    Independently recomputes the headline figures with pandas and re-derives every
    covenant verdict. Returns a list of discrepancies (empty = clean).
    """
    discrepancies = []
    fin = dataframes.get("financial_df")
    borr = dataframes.get("borrowings_df")
    port = dataframes.get("portfolio_df")

    llm_fin = llm_out.get("financial_metrics", {}) or {}
    llm_port = llm_out.get("portfolio_metrics", {}) or {}
    llm_ratios = llm_out.get("key_ratios", {}) or {}

    def check(label, expected, actual):
        if actual is None:
            discrepancies.append({"metric": label, "expected": round(float(expected), 4),
                                  "llm_value": None, "note": "missing from output"})
        elif not _close(expected, actual):
            discrepancies.append({"metric": label, "expected": round(float(expected), 4),
                                  "llm_value": actual,
                                  "note": "differs from deterministic recompute"})

    if fin is not None and len(fin) >= 5:
        cur, prior_yoy, prior_qoq = fin.iloc[-1], fin.iloc[-5], fin.iloc[-2]
        for col in ("revenue_cr", "ebitda_cr", "pat_cr", "net_worth_cr"):
            check(col, float(cur[col]), llm_fin.get(col))
        # Flow items for the quarter, not cumulative totals - the model has been
        # seen summing these across all ten quarters instead of reading the row.
        for col in ("disbursement_cr", "collection_cr"):
            if col in fin.columns:
                check(col, float(cur[col]), llm_port.get(col))
        for col, key in (("revenue_cr", "revenue_yoy_pct"), ("ebitda_cr", "ebitda_yoy_pct"),
                         ("pat_cr", "pat_yoy_pct"), ("total_debt_cr", "total_debt_yoy_pct"),
                         ("net_worth_cr", "net_worth_yoy_pct")):
            check(key, ((float(cur[col]) - float(prior_yoy[col])) / float(prior_yoy[col])) * 100,
                  llm_fin.get(key))
        check("total_debt_qoq_pct",
              ((float(cur["total_debt_cr"]) - float(prior_qoq["total_debt_cr"]))
               / float(prior_qoq["total_debt_cr"])) * 100, llm_fin.get("total_debt_qoq_pct"))
        for col in ("dscr", "interest_coverage", "capital_adequacy_pct", "roa_pct", "roe_pct"):
            check(col, float(cur[col]), llm_ratios.get(col))

    if port is not None:
        aum = float(port["aum_cr"].sum())
        gnpa = float(port["gnpa_90_plus_dpd_cr"].sum())
        prov = float(port["provision_amount_cr"].sum())
        check("aum_cr", aum, llm_port.get("aum_cr"))
        check("gnpa_pct", gnpa / aum * 100, llm_port.get("gnpa_pct"))
        check("nnpa_pct", (gnpa - prov) / (aum - prov) * 100, llm_port.get("nnpa_pct"))
        check("collection_efficiency_pct",
              float((port["aum_cr"] * port["collection_efficiency_pct"]).sum() / aum),
              llm_port.get("collection_efficiency_pct"))
        check("active_loans", float(port["active_accounts"].sum()), llm_port.get("active_loans"))

    if borr is not None and fin is not None:
        check("debt_to_equity",
              float(borr["outstanding_balance_cr"].sum()) / float(fin.iloc[-1]["net_worth_cr"]),
              llm_ratios.get("debt_to_equity"))

    # Re-derive every covenant verdict so a mislabelled PASS cannot survive.
    for cov in llm_out.get("covenant_audit", []) or []:
        try:
            val, thr = float(cov.get("computed")), float(cov.get("threshold"))
        except (TypeError, ValueError):
            continue
        rule = (cov.get("rule") or "").strip()
        expected = None
        if rule.startswith(">="):
            expected = "PASS" if val >= thr else "FAIL"
        elif rule.startswith("<="):
            expected = "PASS" if val <= thr else "FAIL"
        if expected and cov.get("status") != expected:
            discrepancies.append({
                "metric": f"covenant:{cov.get('name')}",
                "expected": expected, "llm_value": cov.get("status"),
                "note": f"verdict contradicts {val} {rule} {thr}",
            })

    if verbose:
        if discrepancies:
            print(f"    [ARITHMETIC AUDIT] {len(discrepancies)} discrepancy(ies):")
            for d in discrepancies:
                print(f"       ! {d['metric']}: got={d['llm_value']} expected={d['expected']}")
        else:
            print("    [ARITHMETIC AUDIT] Clean - all figures match deterministic recompute.")

    return discrepancies
