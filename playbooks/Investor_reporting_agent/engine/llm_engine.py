"""
==============================================================================
LLM ENGINE - Investor / Board Reporting Agent
==============================================================================
The model writes. It does not calculate, and it does not decide what is true.

By the time this module is called, metrics_engine.py has already computed every
figure, every variance, every driver decomposition and every verdict. The model
receives those as fixed inputs and produces one thing: the prose for the five
editable commentary cards on the Atlas Hub Investor Reporting screen.

THREE LAYERS SIT BETWEEN THE MODEL AND THE FINANCE TEAM

  1. NUMERIC GROUNDING   Every number in the drafted prose is extracted and
                         matched against the computed fact base. A figure that
                         does not reconcile is an invented figure, and the
                         section carrying it is returned to the model once and
                         then flagged.

  2. LANGUAGE GUARDRAILS Forward-looking terms, unsupported causal certainty and
                         promotional phrasing are detected by rule. This
                         commentary is investor- and board-facing: language that
                         reads as guidance can create a disclosure obligation the
                         company never intended, and no amount of prompt
                         instruction removes that risk reliably enough on its own.

  3. ATTRIBUTION LIMITS  A causal claim is only permitted where the deterministic
                         segment decomposition evidences it. Where the evidence
                         is insufficient, the agent is required to flag the
                         movement rather than explain it - flagging is a correct
                         outcome, and a confident invented driver is the specific
                         failure this system exists to prevent.

A section that trips any layer is returned to the model ONCE with the exact
violations quoted. If it still trips, the section is surfaced with status
`needs_review` and its flags attached, so the finance team sees precisely what
the agent got wrong rather than a silently corrected draft.

FALLBACK
If Groq is unreachable, a deterministic template drafter produces the same five
sections from the same fact base. The figures are identical; only the prose is
mechanical, and the run is clearly labelled degraded so nobody mistakes it for a
full one.
==============================================================================
"""

import os
import re
import json
import time

from .paths import ENV_FILE

DEFAULT_MODEL = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o",
    "gemini": "gemini-1.5-pro",
}

# Per-minute token ceilings differ sharply by model on Groq's free tier, and the
# request is admitted against prompt + max_tokens, so a fixed max_tokens 413s on
# the smaller models. Size the completion budget from what is actually left.
# There is ONE model, and it comes from .env (LLM_MODEL). Its rate limits come
# from .env too - LLM_TPM_LIMIT and LLM_MIN_COMPLETION - so switching model is a
# config change and never a code change.
DEFAULT_TPM = 8000
TPM_SAFETY_MARGIN = 400
# Floor on the completion room a request must leave. A batch sized to leave only
# a few hundred tokens comes back with an EMPTY completion, which surfaces as a
# json_validate_failed and looks like a prompt problem when it is not.
DEFAULT_MIN_COMPLETION = 1600


class LLMUnavailable(RuntimeError):
    pass


# ==============================================================================
# ENV / PROVIDER
# ==============================================================================

def load_env(env_path=None, override=False):
    """
    Reads .env into os.environ, stripping inline `# comment` trailers - without
    that, DEFAULT_LLM_PROVIDER reads as "groq  # Options: groq, openai, gemini".

    A variable already present in the environment WINS by default, which is the
    conventional dotenv contract: .env supplies defaults, and the caller's
    environment overrides them. Clobbering the process environment instead makes
    a per-run override (a different output path, a different model for one
    invocation) silently impossible.
    """
    env_path = env_path or ENV_FILE
    if not os.path.exists(env_path):
        return False
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            for sep in (" #", "\t#"):
                if sep in val:
                    val = val.split(sep, 1)[0]
            if override or key not in os.environ:
                os.environ[key] = val.strip()
    return True


def resolve_provider():
    provider = (os.getenv("DEFAULT_LLM_PROVIDER") or "groq").strip().lower()
    model = (os.getenv("LLM_MODEL") or "").strip() or DEFAULT_MODEL.get(provider, "")
    return provider, model


def _placeholder(key):
    return (not key) or ("your_" in key.lower()) or key.lower().endswith("_here")


def _estimate_tokens(*texts):
    return sum(len(t or "") for t in texts) // 4


def model_tpm():
    return int(os.getenv("LLM_TPM_LIMIT") or DEFAULT_TPM)


def min_completion():
    return int(os.getenv("LLM_MIN_COMPLETION") or DEFAULT_MIN_COMPLETION)


def _budget_max_tokens(system_prompt, user_prompt):
    """
    Groq admits a request against prompt + max_tokens, so an oversized
    max_tokens 413s even when the real completion is small. The completion
    budget is therefore whatever is genuinely left under the per-minute ceiling.
    """
    override = os.getenv("LLM_MAX_TOKENS")
    if override:
        return int(override)
    room = model_tpm() - _estimate_tokens(system_prompt, user_prompt) - TPM_SAFETY_MARGIN
    return max(256, min(room, 4000))


# ------------------------------------------------------------------------------
# TOKEN PACING
# ------------------------------------------------------------------------------
# Groq's per-minute ceiling is a rolling window. When the drafting run has to be
# split across several requests, they must be spaced or the second one 429s.

_SPEND = []          # [(timestamp, tokens)]


def _pace(estimated_tokens, verbose=True):
    tpm = model_tpm()
    now = time.time()
    _SPEND[:] = [(t, n) for t, n in _SPEND if now - t < 60]
    used = sum(n for _, n in _SPEND)
    if used + estimated_tokens <= tpm:
        return
    oldest = min((t for t, _ in _SPEND), default=now)
    wait = max(0.0, 61 - (now - oldest))
    if wait > 0:
        if verbose:
            print(f"    [PACING] {used:,} of {tpm:,} tokens used in the last minute; "
                  f"waiting {wait:.0f}s before the next request")
        time.sleep(wait)
    _SPEND.clear()


def _record_spend(tokens):
    _SPEND.append((time.time(), tokens))


def call_llm(system_prompt, user_prompt, provider=None, model=None,
             temperature=0.2, max_tokens=None):
    """
    Single chat completion returning raw text, JSON mode where supported.

    Temperature is low but not zero: this is a drafting task where a little
    variation between regenerations is useful to the finance team (the screen
    offers a per-section Regenerate), while the figures themselves come from the
    payload and cannot drift.

    NOTE: Groq counts prompt + max_tokens against the per-minute token limit when
    admitting a request, so an oversized max_tokens triggers a 413 even when the
    real completion is small. Keep headroom.
    """
    if max_tokens is None:
        max_tokens = _budget_max_tokens(system_prompt, user_prompt)

    p, m = resolve_provider()
    provider, model = provider or p, model or m
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]

    if provider == "groq":
        key = os.getenv("GROQ_API_KEY")
        if _placeholder(key):
            raise LLMUnavailable("GROQ_API_KEY missing or placeholder in .env")
        from groq import Groq
        resp = Groq(api_key=key).chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, response_format={"type": "json_object"})
        return resp.choices[0].message.content

    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if _placeholder(key):
            raise LLMUnavailable("OPENAI_API_KEY missing or placeholder in .env")
        from openai import OpenAI
        resp = OpenAI(api_key=key).chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, response_format={"type": "json_object"})
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
                               "response_mime_type": "application/json"})
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
        est = _estimate_tokens(system_prompt, user_prompt) + \
            _budget_max_tokens(system_prompt, user_prompt)
        _pace(est, verbose=verbose)
        try:
            raw = call_llm(system_prompt, user_prompt, provider=provider, model=model)
            _record_spend(est)
            parsed = _extract_json(raw)
            if verbose:
                print(f"    [LLM:{label}] OK on attempt {attempt} "
                      f"({round(time.time() - t0, 2)}s, {len(raw):,} chars)")
            return parsed
        except LLMUnavailable:
            raise
        except Exception as e:
            last_err, msg = e, str(e)
            if verbose:
                print(f"    [LLM:{label}] attempt {attempt} failed: "
                      f"{type(e).__name__}: {msg[:150]}")
            if attempt > max_retries:
                break
            if any(t in msg for t in ("rate_limit", "429", "413")):
                # Never append corrective text on a size error - that is what
                # turns a marginal 413 into a permanent one.
                wait = 20 * attempt
                if verbose:
                    print(f"    [LLM:{label}] rate limited - waiting {wait}s")
                time.sleep(wait)
            else:
                user_prompt += (f"\n\nYour previous reply was rejected ({msg[:200]}). "
                                f"Reply with raw valid JSON only, matching the contract exactly.")
    raise RuntimeError(f"{label} failed after {max_retries + 1} attempts: {last_err}")


# ==============================================================================
# NUMERIC GROUNDING
# ==============================================================================
# Every figure the model writes must reconcile to something the engine computed.

_NUM_RE = re.compile(r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?")


def _add(values, v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return
    if f != f:                                        # NaN
        return
    values.add(round(f, 4))
    values.add(round(abs(f), 4))


def build_grounded_values(facts):
    """
    The complete set of figures the model is permitted to quote. Percentage-point
    movements are also admitted in basis points, because "improved 20 basis
    points" is the correct way to describe a 0.20pp move and must not be flagged
    as ungrounded.
    """
    v = set()

    for row in facts.get("kpi_comparison", []):
        for k in ("current_actual", "prior_actual", "yoy_actual", "forecast",
                  "budget", "qoq_change", "yoy_change", "qoq_change_abs",
                  "yoy_change_abs", "budget_variance", "budget_variance_pct"):
            _add(v, row.get(k))
        if row.get("change_type") == "pp_change":
            for k in ("qoq_change", "yoy_change"):
                if row.get(k) is not None:
                    _add(v, row[k] * 100)             # basis points

    for key, val in (facts.get("derived_metrics") or {}).items():
        _add(v, val)

    sm = facts.get("stage_movement", {})
    for row in sm.get("rows", []):
        for k in ("current", "prior", "forecast", "qoq_change",
                  "current_share_pct", "forecast_share_pct"):
            _add(v, row.get(k))
    for k in ("value", "prior"):
        _add(v, sm.get("net_flow", {}).get(k))

    for att in (facts.get("attribution") or {}).values():
        _add(v, att.get("total_change"))
        _add(v, att.get("explained_share_pct"))
        for c in att.get("contributors", []):
            for k in ("current", "prior", "change", "share_of_change_pct",
                      "stock_effect", "denominator_effect"):
                _add(v, c.get(k))
            if c.get("change") is not None:
                _add(v, c["change"] * 100)            # bps form for ratio effects

    for rc in facts.get("reconciliations", []):
        for k in ("bottom_up_value", "reported_value", "variance", "variance_pct"):
            _add(v, rc.get(k))

    for oo in facts.get("one_off_items", []):
        _add(v, oo.get("impact_value"))

    bv = facts.get("budget_variance", {})
    for k in ("actual", "budget", "variance", "variance_pct"):
        _add(v, bv.get(k))
    for e in bv.get("by_metric", []):
        for k in ("actual", "budget", "variance", "variance_pct"):
            _add(v, e.get(k))

    for item in facts.get("risk_attention_items", []):
        ev = item.get("evidence") or {}
        if isinstance(ev, dict):
            for val in ev.values():
                _add(v, val)

    return v


def extract_figures(text, ignore_patterns):
    """Pulls every numeric token from the prose, minus tokens that are labels."""
    scrubbed = text or ""
    for pat in ignore_patterns or []:
        try:
            scrubbed = re.sub(pat, " ", scrubbed)
        except re.error:
            continue
    out = []
    for m in _NUM_RE.finditer(scrubbed):
        tok = m.group(0)
        try:
            out.append((tok, float(tok.replace(",", ""))))
        except ValueError:
            continue
    return out


def check_grounding(text, grounded, guard_cfg):
    ng = guard_cfg.get("numeric_grounding", {})
    abs_tol = float(ng.get("abs_tolerance", 0.05))
    rel_tol = float(ng.get("rel_tolerance", 0.01))
    figures, ungrounded = [], []

    for tok, val in extract_figures(text, ng.get("ignore_patterns", [])):
        match, best = None, None
        for g in grounded:
            d = abs(g - val)
            if d <= abs_tol or (max(abs(g), abs(val)) > 0
                                and d / max(abs(g), abs(val)) <= rel_tol):
                if best is None or d < best:
                    match, best = g, d
        figures.append({"quoted": tok, "value": val,
                        "matched_to": (str(match) if match is not None else None),
                        "grounded": match is not None})
        if match is None:
            ungrounded.append(tok)
    return figures, ungrounded


# ==============================================================================
# LANGUAGE GUARDRAILS
# ==============================================================================

def _find_terms(text, terms):
    """Whole-word / phrase matches, case-insensitive, returning the sentence."""
    hits = []
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    for term in terms or []:
        pat = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        for s in sentences:
            if pat.search(s):
                hits.append((term, s.strip()))
    return hits


def check_section(section, facts, config, section_cfg):
    """
    Runs every guardrail over one drafted section. Returns the flag list; an
    empty list means the section is clean.
    """
    guard = config.get("guardrails", {})
    body = section.get("body", "") or ""
    flags = []

    figures, ungrounded = check_grounding(body, facts["_grounded"], guard)
    section["figures_used"] = figures
    for tok in ungrounded:
        flags.append({
            "type": "ungrounded_figure", "severity": "High",
            "message": f"The figure {tok} does not reconcile to any computed value.",
            "excerpt": tok})

    for term, sentence in _find_terms(body, guard.get("forward_looking_terms")):
        flags.append({
            "type": "forward_looking", "severity": "High",
            "message": f"Forward-looking language ('{term}') - investor and board "
                       f"commentary must not read as guidance.",
            "excerpt": sentence})

    for term, sentence in _find_terms(body, guard.get("causal_overreach_terms")):
        flags.append({
            "type": "causal_overreach", "severity": "Medium",
            "message": f"Asserts causation ('{term}') beyond what the attribution "
                       f"evidence establishes.",
            "excerpt": sentence})

    for term, sentence in _find_terms(body, guard.get("banned_phrases")):
        flags.append({
            "type": "banned_phrase", "severity": "Medium",
            "message": f"Promotional language ('{term}') has no place in a board pack.",
            "excerpt": sentence})

    words = len(body.split())
    section["word_count"] = words
    max_words = int(guard.get("max_words_per_section", 200))
    if words > max_words:
        flags.append({
            "type": "length", "severity": "Low",
            "message": f"{words} words against a maximum of {max_words}.",
            "excerpt": ""})

    # A one-off affecting a metric in this section must be separated out. The
    # executive summary is exempt by configuration - it draws on the sections
    # below it, and forcing every adjusted figure into it would crowd out the
    # summary's job.
    section_kpis = set(section_cfg.get("kpis", []))
    ng = guard.get("numeric_grounding", {})
    adj_abs = float(ng.get("abs_tolerance", 0.05))
    adj_rel = float(ng.get("rel_tolerance", 0.01))
    for oo in (facts.get("one_off_items", [])
               if section_cfg.get("enforce_one_off_separation", True) else []):
        if oo.get("metric_affected") not in section_kpis:
            continue
        adj_key = ("pat_ex_oneoff_cr" if oo["metric_affected"] == "pat_cr"
                   else "aum_organic_growth_cr")
        adj = (facts.get("derived_metrics") or {}).get(adj_key)
        if adj is None:
            continue
        # Matched on the same tolerance as numeric grounding, so a figure the
        # grounding check accepts is not rejected here for the same rounding.
        quoted = any(abs(f["value"] - adj) <= adj_abs
                     or (max(abs(f["value"]), abs(adj)) > 0
                         and abs(f["value"] - adj) / max(abs(f["value"]), abs(adj)) <= adj_rel)
                     for f in figures)
        if not quoted:
            flags.append({
                "type": "one_off_not_separated", "severity": "High",
                "message": f"{oo['item_id']} ({oo['description'][:60]}...) affects "
                           f"{oo['metric_affected']} but the ex-one-off figure "
                           f"({adj}) is not stated. The reported movement must not "
                           f"stand alone.",
                "excerpt": ""})

    # A material movement whose drivers the engine could not establish must be
    # acknowledged, not explained away.
    declared = {u.lower() for u in (section.get("unexplained_movements") or [])}
    for key in section_cfg.get("kpis", []):
        att = (facts.get("attribution") or {}).get(key)
        if att and not att["sufficient"]:
            mentioned = any(key.split("_")[0] in d for d in declared)
            if not mentioned:
                flags.append({
                    "type": "unexplained_movement", "severity": "Medium",
                    "message": f"Named drivers explain only {att['explained_share_pct']}% "
                               f"of the movement in {att['label']}, but the section does "
                               f"not flag it as unexplained.",
                    "excerpt": ""})
    return flags


# ==============================================================================
# PROMPT ASSEMBLY
# ==============================================================================

DRAFT_CONTRACT = """
Return ONE JSON object and nothing else:

{
  "sections": [
    {
      "id": "<the section id exactly as given>",
      "body": "<the drafted commentary prose for this section>",
      "kpis_referenced": ["<metric_key>", ...],
      "unexplained_movements": ["<a material movement you could not attribute, and the data that would have let you>"]
    }
  ]
}

RULES
- Return one entry for EVERY section id listed, in the order given.
- `body` is plain prose. No markdown, no bullet characters, no headings.
- Every number in `body` must appear in the FACT BASE below, used verbatim at the
  precision given. Introducing any other number is a failure.
- Ratios and percentages move in percentage points or basis points, never in
  percent. Amounts move in currency and in percent.
- `sentiment` on each figure tells you whether the movement is favourable. Do not
  reason about it, and do not soften an adverse movement.
- Where the attribution evidence marks a movement insufficient, say so in the
  prose AND list it in `unexplained_movements`. Do not invent a driver.
- Raw JSON only. No markdown fences, no prose outside the JSON.
"""


def _compact_kpis(facts, keys=None):
    out = []
    for r in facts["kpi_comparison"]:
        if keys is not None and r["metric_key"] not in keys:
            continue
        out.append({
            "metric_key": r["metric_key"], "label": r["label"],
            "definition": r["definition"],
            "current": r["current_actual"], "current_display": r["current_actual_display"],
            "prior": r["prior_actual"], "prior_display": r["prior_actual_display"],
            "yoy": r["yoy_actual"],
            "forecast": r["forecast"], "forecast_basis": r["forecast_basis"],
            "forecast_approved_by": r["forecast_approved_by"],
            "change_type": r["change_type"],
            "qoq": r["qoq_change_display"], "qoq_abs": r["qoq_change_abs"],
            "yoy_move": r["yoy_change_display"], "yoy_abs": r["yoy_change_abs"],
            "qoq_sentiment": r["qoq_sentiment"], "yoy_sentiment": r["yoy_sentiment"],
            "material": r["material"],
            "budget": r["budget"], "budget_variance": r["budget_variance"],
        })
    return out


def _compact_stages(facts):
    """Stage card, minus the display strings and descriptions the model does not need."""
    sm = facts.get("stage_movement", {})
    return {
        "stages": [{k: r[k] for k in ("label", "current", "prior", "forecast",
                                      "qoq_change", "current_share_pct", "sentiment")}
                   for r in sm.get("rows", [])],
        "net_flow": {k: sm.get("net_flow", {}).get(k)
                     for k in ("label", "value", "prior", "sentiment")},
    }


def _compact_attribution(facts, keys=None):
    out = {}
    for key, att in (facts.get("attribution") or {}).items():
        if keys is not None and key not in keys:
            continue
        out[key] = {
            "what_it_decomposes": att["description"],
            "total_change": att["total_change"], "unit": att["unit"],
            "explained_share_pct": att["explained_share_pct"],
            "sufficient_to_state_a_driver": att["sufficient"],
            "contributors": [
                {k: c[k] for k in ("label", "change", "share_of_change_pct",
                                   "stock_effect", "denominator_effect")
                 if c.get(k) is not None}
                for c in att["contributors"][:5]],
        }
    return out


def _style_exemplars(config, feeds, section_ids):
    """
    Two approved exemplars per section from the most recent decks, plus the
    edit-intensity signal that tells the model where its previous drafts were
    weakest.
    """
    cfg = config.get("style_reference", {})
    lookback = int(cfg.get("lookback_quarters", 3))
    per_section = int(cfg.get("max_exemplars_per_section", 2))
    high_edit = float(cfg.get("high_edit_threshold_pct", 35.0))

    decks = (feeds.get("past_commentary") or {}).get("decks", [])[:lookback]
    by_section = {sid: [] for sid in section_ids}
    for deck in decks:
        for s in deck.get("sections", []):
            sid = s.get("section")
            if sid in by_section and len(by_section[sid]) < per_section:
                by_section[sid].append({
                    "deck_quarter": deck.get("deck_quarter"),
                    "audience": deck.get("audience"),
                    "approved_text": s.get("commentary_text"),
                    "finance_team_edited_pct": s.get("final_vs_draft_diff"),
                    "note": ("The finance team rewrote most of the previous draft for "
                             "this section - study the approved text closely."
                             if (s.get("final_vs_draft_diff") or 0) >= high_edit else ""),
                })
    return by_section


def build_draft_prompt(config, facts, feeds, drafting_prompt, style_guide,
                       tone_id, audience, only_sections=None):
    sections = [s for s in config.get("commentary_sections", [])
                if audience in s.get("audiences", ["board"])]
    sections.sort(key=lambda s: s.get("order", 99))
    if only_sections:
        sections = [s for s in sections if s["id"] in only_sections]

    tones = config.get("tone_presets", {})
    tone = next((t for t in tones.get("options", []) if t["id"] == tone_id),
                {"id": tone_id, "label": tone_id, "guidance": ""})

    exemplars = _style_exemplars(config, feeds, [s["id"] for s in sections])
    m = facts["meta"]

    # The fact base is scoped to the sections actually being drafted. On a
    # token-metered model this is the difference between fitting and not, and it
    # also sharpens the draft - a section is not tempted to reach for a figure
    # that belongs to a different card.
    wanted = set()
    for s in sections:
        wanted.update(s.get("kpis", []))
    section_ids = {s["id"] for s in sections}

    spec = []
    for s in sections:
        spec.append({
            "id": s["id"], "title": s["title"],
            "kpis": s.get("kpis", []),
            "target_words": s.get("target_words"),
            "must_land_these_points": s.get("beats", []),
            "style_exemplars": exemplars.get(s["id"], []),
        })

    parts = [
        "# TASK",
        f"Draft the first-pass commentary for {m['company_name']}'s {m['audience']} "
        f"deck covering {m['reporting_quarter']}. Comparators: "
        f"{m['prior_quarter']} (QoQ) and {m['yoy_quarter']} (YoY). "
        f"Every figure has already been computed - you are writing, not calculating.\n",

        f"# SELECTED TONE: {tone['label']}",
        tone.get("guidance", "") + "\n",

        "# DRAFTING PLAYBOOK",
        (drafting_prompt or "").strip() + "\n",

        "# HOUSE STYLE",
        (style_guide or "").strip() + "\n",

        "# FACT BASE - HEADLINE KPIs (use these figures verbatim)",
        "```json\n" + json.dumps(_compact_kpis(facts, wanted), separators=(",", ":")) + "\n```\n",

        "# FACT BASE - DRIVER ATTRIBUTION",
        "This decomposition is your ONLY permitted evidence for a causal claim. "
        "Where `sufficient_to_state_a_driver` is false you must flag the movement "
        "instead of explaining it.",
        "```json\n" + json.dumps(_compact_attribution(facts, wanted),
                                 separators=(",", ":")) + "\n```\n",
    ]

    if "asset_quality" in section_ids:
        parts += [
            "# FACT BASE - STAGE MOVEMENT",
            "```json\n" + json.dumps(_compact_stages(facts), separators=(",", ":")) + "\n```\n",
        ]

    one_offs = [o for o in facts.get("one_off_items", [])
                if o.get("metric_affected") in wanted]
    if one_offs:
        parts += [
            "# FACT BASE - EXCEPTIONAL ITEMS",
            "Any metric these affect must be presented BOTH ways - reported, and "
            "excluding the item, with the item named. The adjusted figures are in "
            "the derived figures below.",
            "```json\n" + json.dumps(
                [{k: o[k] for k in ("item_id", "description", "metric_affected",
                                    "impact_value", "nature")} for o in one_offs],
                separators=(",", ":")) + "\n```\n",
        ]

    derived = _relevant_derived(facts, wanted)
    if derived:
        parts += [
            "# FACT BASE - DERIVED FIGURES",
            "```json\n" + json.dumps(derived, separators=(",", ":")) + "\n```\n",
        ]

    bv = facts.get("budget_variance", {})
    if bv.get("metric_key") in wanted:
        parts += [
            "# FACT BASE - BUDGET VARIANCE",
            "```json\n" + json.dumps(
                {k: bv.get(k) for k in ("metric_key", "actual", "budget", "variance",
                                        "variance_pct", "display", "sentiment")},
                separators=(",", ":")) + "\n```\n",
        ]

    # High severity items always travel: the Executive Summary must carry them,
    # and any section they touch should acknowledge them.
    risks = [r for r in facts.get("risk_attention_items", [])
             if r["severity"] == "High" or r.get("metric_key") in wanted]
    if risks:
        parts += [
            "# FACT BASE - RISK / ATTENTION ITEMS ALREADY RAISED BY RULE",
            "You may reference these. You may not invent one, and a High severity "
            "item must appear in the Executive Summary.",
            "```json\n" + json.dumps(
                [{"severity": r["severity"], "message": r["message"]} for r in risks],
                separators=(",", ":")) + "\n```\n",
        ]

    parts += [
        "# SECTIONS TO DRAFT",
        "```json\n" + json.dumps(spec, separators=(",", ":")) + "\n```\n",
        "# OUTPUT CONTRACT",
        DRAFT_CONTRACT,
    ]
    return "\n".join(parts)


_DERIVED_FOR = {
    "pat_cr": ("pat_ex_oneoff_cr", "pat_ex_oneoff_qoq_pct", "one_off_pbt_impact_cr",
               "one_off_pat_impact_cr", "pat_margin_pct", "pat_margin_prior_pct"),
    "revenue_cr": ("ebitda_margin_pct", "ebitda_margin_prior_pct"),
    "ebitda_cr": ("ebitda_margin_pct", "ebitda_margin_prior_pct"),
    "aum_cr": ("aum_growth_total_cr", "aum_inorganic_cr", "aum_organic_growth_cr",
               "aum_organic_growth_pct", "active_accounts"),
    "gnpa_pct": ("gnpa_cr", "provision_cr", "provision_coverage_pct",
                 "provision_coverage_prior_pct", "net_flow_to_stage3_cr",
                 "net_flow_to_stage3_prior_cr"),
    "nnpa_pct": ("provision_coverage_pct", "provision_coverage_prior_pct"),
    "cost_of_funds_pct": ("borrowings_cr", "net_worth_cr", "debt_to_equity"),
}


def _relevant_derived(facts, wanted):
    derived = facts.get("derived_metrics") or {}
    keys = set()
    for kpi in wanted:
        keys.update(_DERIVED_FOR.get(kpi, ()))
    return {k: v for k, v in derived.items() if k in keys and v is not None}


def build_repair_prompt(section_cfg, section, flags, facts):
    lines = [
        f"# SECTION TO FIX: {section_cfg['id']} ({section_cfg['title']})\n",
        "# YOUR PREVIOUS DRAFT\n" + (section.get("body") or "") + "\n",
        "# WHAT IS WRONG WITH IT",
    ]
    for f in flags:
        lines.append(f"- [{f['type']}] {f['message']}"
                     + (f"\n    excerpt: \"{f['excerpt']}\"" if f.get("excerpt") else ""))
    lines += [
        "\n# HOW TO FIX IT",
        "- Fix EXACTLY what is listed above. Change nothing else - the rest of the "
        "draft was accepted.",
        "- An ungrounded figure means you wrote a number that is not in the fact base. "
        "Replace it with the correct figure from the fact base, or remove the claim.",
        "- Forward-looking language must be removed, not softened. Rewrite the sentence "
        "to describe the closed quarter.",
        "- If a movement cannot be attributed from the evidence, say so plainly in the "
        "prose and list it in `unexplained_movements`.",
        "",
        "# FACT BASE - HEADLINE KPIs",
        "```json\n" + json.dumps(_compact_kpis(facts), separators=(",", ":")) + "\n```",
        "# FACT BASE - DERIVED FIGURES",
        "```json\n" + json.dumps(facts.get("derived_metrics", {}), separators=(",", ":")) + "\n```",
        "# FACT BASE - DRIVER ATTRIBUTION",
        "```json\n" + json.dumps(_compact_attribution(facts), separators=(",", ":")) + "\n```",
        "",
        "Return ONE JSON object: "
        '{"sections":[{"id":"...","body":"...","kpis_referenced":[...],'
        '"unexplained_movements":[...]}]}',
        "Raw JSON only.",
    ]
    return "\n".join(lines)


# ==============================================================================
# DELTA BADGE
# ==============================================================================

def build_delta_badge(config, facts, section_cfg):
    """The pill next to each commentary card title, e.g. '+₹137 Cr QoQ'."""
    spec = section_cfg.get("delta_badge")
    if not spec:
        return None
    row = next((r for r in facts["kpi_comparison"]
                if r["metric_key"] == spec.get("metric")), None)
    if row is None:
        return None

    sym = facts["meta"].get("currency_symbol", "₹")
    basis = spec.get("basis", "qoq_pct")
    value = {"qoq_absolute": row["qoq_change_abs"],
             "qoq_pct": row["qoq_change"],
             "qoq_pp": row["qoq_change"],
             "qoq_bps": (row["qoq_change"] * 100
                         if row["qoq_change"] is not None else None)}.get(basis)
    if value is None:
        return None

    sign = "+" if value > 0 else ("-" if value < 0 else "")
    try:
        text = spec.get("format", "{sign}{value}").format(
            sign=sign, symbol=sym, value=abs(value))
    except (KeyError, IndexError, ValueError):
        text = f"{sign}{abs(value)}"

    return {"text": text,
            "sentiment": row["qoq_sentiment"],
            "metric_key": row["metric_key"],
            "value": round(value, 4)}


# ==============================================================================
# ORCHESTRATION
# ==============================================================================

def _plan_batches(config, facts, feeds, system_prompt, drafting_prompt, style_guide,
                  tone_id, audience, section_cfgs, tpm):
    """
    Greedily packs sections into the largest requests that fit under the model's
    per-minute ceiling. Always returns at least one batch per section, so an
    unusually tight ceiling degrades to one-section-per-call rather than failing.
    """
    ids = [s["id"] for s in section_cfgs]

    reserve = min_completion()

    def fits(subset):
        prompt = build_draft_prompt(config, facts, feeds, drafting_prompt, style_guide,
                                    tone_id, audience, only_sections=subset)
        need = (_estimate_tokens(system_prompt, prompt)
                + max(reserve, 320 * len(subset))
                + TPM_SAFETY_MARGIN)
        return need <= tpm

    if fits(ids):
        return [ids]

    batches, current = [], []
    for sid in ids:
        trial = current + [sid]
        if current and not fits(trial):
            batches.append(current)
            current = [sid]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def run_commentary_drafting(config, facts, feeds, system_prompt, drafting_prompt,
                            style_guide, tone_id=None, audience="board", verbose=True):
    """
    Drafts every commentary section, checks each one, repairs what fails, and
    returns (ai_commentary, guardrail_report, meta).
    """
    provider, model = resolve_provider()
    guard = config.get("guardrails", {})
    tones = config.get("tone_presets", {})
    tone_id = tone_id or tones.get("default", "board_formal")
    tone = next((t for t in tones.get("options", []) if t["id"] == tone_id),
                {"id": tone_id, "label": tone_id})

    section_cfgs = [s for s in config.get("commentary_sections", [])
                    if audience in s.get("audiences", ["board"])]
    section_cfgs.sort(key=lambda s: s.get("order", 99))
    by_id = {s["id"]: s for s in section_cfgs}

    facts["_grounded"] = build_grounded_values(facts)
    if verbose:
        print(f"    [LLM] Provider: {provider} | Model: {model} | Tone: {tone['label']}")
        print(f"    [GUARDRAIL] {len(facts['_grounded'])} computed figures admitted "
              f"as grounded values")

    t0 = time.time()

    # ---- adaptive batching -------------------------------------------------
    # The instruction payload (persona, playbook, house style) travels with every
    # request, so on a model with a tight per-minute ceiling the whole set of
    # sections will not fit in one call. Rather than truncate the instructions -
    # which is what produces a bad draft - the run is split into the largest
    # batches that fit, and paced between them.
    tpm = model_tpm()
    batches = _plan_batches(config, facts, feeds, system_prompt, drafting_prompt,
                            style_guide, tone_id, audience, section_cfgs, tpm)
    if verbose:
        if len(batches) == 1:
            print(f"    [LLM:DRAFT] Drafting {len(section_cfgs)} section(s) in one "
                  f"request (model ceiling {tpm:,} tokens/min)")
        else:
            print(f"    [LLM:DRAFT] Model ceiling is {tpm:,} tokens/min - splitting "
                  f"{len(section_cfgs)} section(s) across {len(batches)} paced requests")

    drafted = {}
    for i, batch in enumerate(batches, start=1):
        prompt = build_draft_prompt(config, facts, feeds, drafting_prompt, style_guide,
                                    tone_id, audience, only_sections=batch)
        if verbose:
            print(f"    [LLM:DRAFT] batch {i}/{len(batches)}: {', '.join(batch)} "
                  f"(~{(len(prompt) + len(system_prompt)) // 4:,} tokens)")
        reply = _call_with_retries(system_prompt, prompt, f"DRAFT{i}", verbose=verbose)
        for s in reply.get("sections", []):
            if s.get("id"):
                drafted[s["id"]] = s

    max_repairs = int(guard.get("repair_rounds", 1))
    sections, repairs_used = [], 0

    for cfg_s in section_cfgs:
        sid = cfg_s["id"]
        raw = drafted.get(sid) or {"id": sid, "body": "", "kpis_referenced": [],
                                   "unexplained_movements": []}
        section = {
            "id": sid, "title": cfg_s["title"], "order": cfg_s.get("order", 99),
            "body": (raw.get("body") or "").strip(),
            "kpis_referenced": raw.get("kpis_referenced") or [],
            "unexplained_movements": raw.get("unexplained_movements") or [],
            "regenerate_count": 0,
        }

        flags = check_section(section, facts, config, cfg_s)
        rounds = 0
        while flags and rounds < max_repairs:
            rounds += 1
            repairs_used += 1
            if verbose:
                print(f"    [GUARDRAIL] {sid}: {len(flags)} violation(s) "
                      f"({', '.join(sorted({f['type'] for f in flags}))}) "
                      f"- returning to the model (round {rounds}/{max_repairs})")
            try:
                fixed = _call_with_retries(
                    system_prompt, build_repair_prompt(cfg_s, section, flags, facts),
                    f"REPAIR:{sid}", max_retries=1, verbose=verbose)
                cand = next((s for s in fixed.get("sections", [])
                             if s.get("id") == sid), None)
                if not cand or not (cand.get("body") or "").strip():
                    break
                section["body"] = cand["body"].strip()
                section["kpis_referenced"] = cand.get("kpis_referenced") or section["kpis_referenced"]
                section["unexplained_movements"] = (cand.get("unexplained_movements")
                                                    or section["unexplained_movements"])
                section["regenerate_count"] += 1
                flags = check_section(section, facts, config, cfg_s)
            except Exception as e:
                if verbose:
                    print(f"    [GUARDRAIL] {sid}: repair call failed ({e}) "
                          f"- surfacing the draft with its flags")
                break

        section["flags"] = flags
        section["status"] = "needs_review" if flags else "draft"
        section["delta_badge"] = build_delta_badge(config, facts, cfg_s)
        section["style_exemplars_used"] = [
            e["deck_quarter"] for e in
            _style_exemplars(config, feeds, [sid]).get(sid, [])]
        sections.append(section)

        if verbose:
            state = "CLEAN" if not flags else f"{len(flags)} FLAG(S)"
            print(f"    [SECTION] {sid:<20} {section['word_count']:>3}w  {state}")

    ai_commentary = {
        "tone": tone_id, "tone_label": tone.get("label", tone_id),
        "tone_options": [{"id": t["id"], "label": t["label"]}
                         for t in tones.get("options", [])],
        "sections": sections,
    }
    return ai_commentary, _guardrail_report(sections, repairs_used), {
        "provider": provider, "model": model, "passes": 1 + repairs_used,
        "repair_rounds": repairs_used, "latency_s": round(time.time() - t0, 2),
        "degraded": False,
    }


def _guardrail_report(sections, repairs_used):
    by_type = {}
    for s in sections:
        for f in s.get("flags", []):
            by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    figures = sum(len(s.get("figures_used", [])) for s in sections)
    ungrounded = sum(1 for s in sections
                     for f in s.get("figures_used", []) if not f["grounded"])
    clean = sum(1 for s in sections if not s.get("flags"))
    return {
        "sections_drafted": len(sections),
        "sections_clean": clean,
        "sections_flagged": len(sections) - clean,
        "total_flags": sum(by_type.values()),
        "flags_by_type": by_type,
        "repair_rounds_used": repairs_used,
        "figures_checked": figures,
        "ungrounded_figures": ungrounded,
    }


# ==============================================================================
# FALLBACK - DETERMINISTIC DRAFTING WHEN THE LLM IS UNREACHABLE
# ==============================================================================
# A missing key, an exhausted quota or a network failure must not take the
# reporting pipeline down. The figures are identical to a full run because they
# never came from the model in the first place; only the prose is mechanical.

def _q(row, field):
    return row.get(field)


def fallback_commentary(config, facts, audience="board", verbose=True):
    if verbose:
        print("    [FALLBACK] Drafting commentary from templates - "
              "figures are identical to a full run, only the prose is mechanical.")

    sym = facts["meta"].get("currency_symbol", "₹")
    kpi = {r["metric_key"]: r for r in facts["kpi_comparison"]}
    d = facts.get("derived_metrics", {})
    att = facts.get("attribution", {})
    sm = facts.get("stage_movement", {})
    m = facts["meta"]

    def line(key, noun=None):
        r = kpi.get(key)
        if not r:
            return ""
        noun = noun or r["short_label"]
        if r["change_type"] == "pp_change":
            return (f"{noun} was {r['current_actual_display']} against "
                    f"{r['prior_actual_display']} in {m['prior_quarter']} and "
                    f"{r['yoy_actual']:.2f}% in {m['yoy_quarter']}.")
        return (f"{noun} was {sym}{r['current_actual_display']} Cr against "
                f"{sym}{r['prior_actual_display']} Cr in {m['prior_quarter']}, "
                f"a movement of {r['qoq_change_display']} QoQ and "
                f"{r['yoy_change_display']} YoY.")

    def top_driver(key, n=2):
        a = att.get(key)
        if not a or not a.get("sufficient"):
            return ""
        cs = a["contributors"][:n]
        # 1dp, not 0dp: the house style forbids adding or removing precision the
        # payload did not give, and the grounding check enforces it.
        bits = [f"{c['label']} ({c['share_of_change_pct']:.1f}% of the movement)"
                for c in cs if c.get("share_of_change_pct")]
        return (" The movement was concentrated in " + " and ".join(bits) + ".") if bits else ""

    bodies = {}

    b = [line("aum_cr", "AUM"), line("disbursement_cr", "Disbursements")]
    b.append(top_driver("aum_cr").strip())
    if d.get("aum_inorganic_cr"):
        b.append(f"Of the {sym}{d['aum_growth_total_cr']:.1f} Cr increase, "
                 f"{sym}{d['aum_inorganic_cr']:.1f} Cr was an acquired portfolio; "
                 f"organic growth was {sym}{d['aum_organic_growth_cr']:.1f} Cr "
                 f"({d['aum_organic_growth_pct']:.1f}%).")
    bodies["aum_growth"] = " ".join(x for x in b if x)

    b = [line("gnpa_pct", "Gross NPA"), line("nnpa_pct", "Net NPA")]
    rows = {r["stage"]: r for r in sm.get("rows", [])}
    if "stage2_cr" in rows:
        r2 = rows["stage2_cr"]
        b.append(f"Stage 2 exposure was {sym}{r2['current']:,.1f} Cr against "
                 f"{sym}{r2['prior']:,.1f} Cr.")
    nf = sm.get("net_flow", {})
    if nf.get("value") is not None:
        b.append(f"Net new flow to Stage 3 was {nf['display']}.")
    if d.get("provision_coverage_pct"):
        b.append(f"Provision coverage was {d['provision_coverage_pct']:.1f}%.")
    b.append(top_driver("stage2_cr").strip())
    bodies["asset_quality"] = " ".join(x for x in b if x)

    b = [line("revenue_cr", "Revenue"), line("ebitda_cr", "EBITDA"),
         line("pat_cr", "Profit after tax")]
    if d.get("pat_ex_oneoff_cr") is not None:
        names = "; ".join(f"a {sym}{abs(o['impact_value'])} Cr "
                          f"{'gain' if o['impact_value'] > 0 else 'charge'} "
                          f"({o['description'][:50]})"
                          for o in facts.get("one_off_items", [])
                          if o.get("metric_affected") == "pat_cr")
        b.append(f"Excluding exceptional items — {names} — profit after tax was "
                 f"{sym}{d['pat_ex_oneoff_cr']:.1f} Cr.")
    if d.get("ebitda_margin_pct"):
        b.append(f"The EBITDA margin was {d['ebitda_margin_pct']:.1f}%.")
    bodies["profitability"] = " ".join(x for x in b if x)

    r = kpi.get("cost_of_funds_pct")
    b = []
    if r and r["qoq_change"] is not None:
        b.append(f"The cost of funds was {r['current_actual_display']}, "
                 f"{abs(r['qoq_change'] * 100):.0f} basis points "
                 f"{'above' if r['qoq_change'] > 0 else 'below'} "
                 f"{r['prior_actual_display']} in {m['prior_quarter']}.")
    if d.get("borrowings_cr"):
        b.append(f"Borrowings were {sym}{d['borrowings_cr']:,.1f} Cr against net worth "
                 f"of {sym}{d['net_worth_cr']:,.1f} Cr, a leverage of "
                 f"{d['debt_to_equity']:.2f} times.")
    bodies["funding_cost"] = " ".join(x for x in b if x)

    b = [line("aum_cr", "AUM"), line("pat_cr", "Profit after tax"),
         line("gnpa_pct", "Gross NPA")]
    highs = [x["message"] for x in facts.get("risk_attention_items", [])
             if x["severity"] == "High"]
    if highs:
        b.append("Items placed before the board: " + "; ".join(highs[:3]) + ".")
    bodies["executive_summary"] = " ".join(x for x in b if x)

    section_cfgs = [s for s in config.get("commentary_sections", [])
                    if audience in s.get("audiences", ["board"])]
    section_cfgs.sort(key=lambda s: s.get("order", 99))

    facts["_grounded"] = build_grounded_values(facts)
    sections = []
    for cfg_s in section_cfgs:
        section = {
            "id": cfg_s["id"], "title": cfg_s["title"], "order": cfg_s.get("order", 99),
            "body": bodies.get(cfg_s["id"], ""),
            "kpis_referenced": cfg_s.get("kpis", []),
            "unexplained_movements": [
                f"{a['label']}: named drivers explain only {a['explained_share_pct']}% "
                f"of the movement"
                for k, a in att.items()
                if k in cfg_s.get("kpis", []) and not a["sufficient"]],
            "regenerate_count": 0,
        }
        flags = check_section(section, facts, config, cfg_s)
        section["flags"] = flags
        section["status"] = "needs_review"
        section["delta_badge"] = build_delta_badge(config, facts, cfg_s)
        section["style_exemplars_used"] = []
        sections.append(section)

    tones = config.get("tone_presets", {})
    ai_commentary = {
        "tone": tones.get("default", "board_formal"),
        "tone_label": "Deterministic fallback",
        "tone_options": [{"id": t["id"], "label": t["label"]}
                         for t in tones.get("options", [])],
        "sections": sections,
    }
    return ai_commentary, _guardrail_report(sections, 0)
