# Investor / Board Reporting Agent

**Atlas Hub → Finance & Treasury → Investor Reporting**

Auto-drafts the quarterly investor and board deck commentary from the finance
team's own data, so their time shifts from writing on a blank page to refining
and adding strategic framing.

Runs once per reporting cycle, triggered by quarter-close.

---

## The problem this solves

Every quarter someone in finance reviews the numbers, forms a narrative, and
writes it up in polished, board-ready language — from scratch, re-explaining the
same categories of movement in fresh prose each time. It consumes a large share
of the deck-preparation cycle.

The hard part is not the arithmetic. It is explaining *why* AUM grew, *why* GNPA
moved, *what* is driving profitability — for an audience of institutional
investors and directors who carry personal accountability for what the company
says about itself.

That is also exactly where an AI draft does the most damage. AI-drafted text can
sound polished and confident while being subtly wrong: a fabricated figure, a
driver attributed to the wrong segment, a sentence that reads as forward-looking
guidance and creates a disclosure obligation nobody intended.

So this agent is built around a single principle:

> **The model writes. It does not calculate, and it does not decide what is true.**

---

## Architecture

```
   .env                    config/                      prompts/
   ────                    ───────                      ────────
   Groq credentials        EVERY technical aspect       EVERY narrative
   model + rate limits     KPI catalog, comparators     instruction
                           attribution, risk rules      persona, playbook,
                           guardrails, validation       house style
                           frontend panel contract
        │                          │                         │
        └──────────────┬───────────┘                         │
                       ▼                                     │
          engine/metrics_engine.py                           │
          ────────────────────────                           │
          All arithmetic. Driver decomposition.              │
          Risk items. Reconciliation. Validation.            │
          Independent recompute audit.                       │
                       │                                     │
                       ▼          facts (the fact base)      ▼
              engine/llm_engine.py ◄─────────────────────────┘
              ────────────────────
              Groq drafts the five commentary cards.
              Three guardrail layers check the PROSE
              back against the FIGURES.
                       │
                       ▼
              engine/pipeline.py
                       │
       ┌───────────────┼───────────────┬──────────────┐
       ▼               ▼               ▼              ▼
    JSON            Markdown          PDF        HTML assistant
  (frontend        (review pack)   (board pack)     screen
   contract)              ───── all written to output/ ─────
```

### Folder layout

The directories are the separation of concerns: technical configuration,
narrative instruction, code, data and generated output never mix.

```
Investor_reporting_agent/
├── agent_runner.py            CLI entrypoint
├── .env                       credentials + model rate limits  (gitignored)
├── .env.example               template - placeholders only
│
├── config/                    ALL TECHNICAL ASPECTS
│   ├── input_config.yaml        KPI catalog, comparators, attribution, risk
│   │                            rules, guardrails, validation, frontend contract
│   └── output_schema.json       payload contract, validated every run
│
├── prompts/                   ALL NARRATIVE INSTRUCTION
│   ├── system_prompt.md         persona and the rules that matter most
│   ├── drafting_prompt.md       the fixed drafting playbook
│   └── style_guide.md           house style
│
├── engine/                    THE CODE
│   ├── paths.py                 project layout, resolved in one place
│   ├── metrics_engine.py        every figure — no LLM
│   ├── llm_engine.py            Groq drafting + the three guardrail layers
│   ├── report_renderer.py       JSON / Markdown / PDF / HTML
│   └── pipeline.py              orchestration and payload assembly
│
├── sample_data/               THE FIVE QUARTERLY FEEDS
└── output/                    GENERATED ARTEFACTS  (gitignored)
```

Nothing in `config/` tells the model how to write. Nothing in `prompts/` tells it
how to calculate. That boundary is the point — a finance user can retune tone,
house style or the drafting playbook without touching a KPI definition, and can
change a materiality threshold or a covenant limit without touching prose.

| File | Role |
| :--- | :--- |
| `config/input_config.yaml` | **All technical aspects.** KPI catalog and definitions, period resolution, forecast policy, budget variance, stage movement, risk rules, source traceability, readiness weights, commentary sections, tone presets, attribution rules, guardrails, validation catalog, reconciliations, derived figures. Also the **contract with the frontend** — each block maps to a named panel. |
| `config/output_schema.json` | The frontend contract, validated on every run. |
| `prompts/system_prompt.md` | **Instructions.** Persona, the four rules that matter most, how to describe a movement, one-off handling, tone. |
| `prompts/drafting_prompt.md` | **Instructions.** The fixed four-step drafting playbook and per-section guidance. |
| `prompts/style_guide.md` | **Instructions.** House style — number formats, periods, voice, banned words, how to write an adverse movement. |
| `engine/metrics_engine.py` | Every figure, variance, driver decomposition, risk item and verdict. No LLM. |
| `engine/llm_engine.py` | Groq calls, prompt assembly, the three guardrail layers, deterministic fallback. |
| `engine/report_renderer.py` | JSON / Markdown / PDF / HTML rendering. |
| `engine/pipeline.py` | Pipeline orchestration and payload assembly. |
| `engine/paths.py` | Project layout resolved in one place, so no module derives its own base directory. |
| `agent_runner.py` | CLI. |

---

## Why the model never calculates

The sibling Fund-Raising agent in this suite established the failure empirically:
a 70B model transcribes source data perfectly, states the correct method
perfectly, and then mis-adds the column — returning 22.1 for a set of addends
summing to 6.8, which propagated into every downstream ratio.

But this use case does not need the model to calculate. Unlike a fund-raising
data pack — where the model must decide which of several sources is authoritative
for each metric — the Investor Reporting screen has a **fixed metric contract**:
eight KPIs, named columns, named comparators, all declared in
`input_config.yaml`. There is no methodological judgement left to make.

So the split is clean: Python owns every number, the model owns every sentence.

**This is not theoretical.** On the live run, the model wrote:

> "Growth was led by Retail Loans and Commercial Vehicle, which together
> contributed **51.1%** of the increase in book size."

Retail contributed 28.6% and Commercial Vehicle 22.5%. The model added them —
arithmetic it was explicitly told not to perform — and produced a figure that
exists nowhere in the fact base. The grounding check caught it and flagged the
section `needs_review`. Every other figure in that draft reconciled exactly.

---

## The three guardrail layers

Between the model and the finance team:

**1. Numeric grounding.** Every number in the prose is extracted and matched
against the computed fact base (284 admitted values on the sample data,
including basis-point forms of percentage-point movements). A figure that does
not reconcile within tolerance is an invented figure.

**2. Language guardrails.** Forward-looking terms, unsupported causal certainty
and promotional phrasing, detected by rule from `input_config.yaml`. This
commentary is investor- and board-facing: language that reads as guidance can
create a disclosure obligation, and no amount of prompt instruction removes that
risk reliably enough on its own.

**3. Attribution limits.** A causal claim is permitted only where the
deterministic segment decomposition evidences it. Where the named drivers explain
less than the configured share of a movement, the agent is **required to flag it
rather than explain it**. Flagging is a correct outcome; a confident invented
driver is the specific failure this system exists to prevent.

A section that trips any layer is returned to the model **once** with the exact
violations quoted. If it still trips, it is surfaced with status `needs_review`
and its flags attached — so the finance team sees precisely what the agent got
wrong rather than a silently corrected draft.

There is also a fourth check that runs regardless of the model: an **independent
recompute** of every displayed figure straight from the raw feed, so a bug in the
table builder cannot put a wrong number in front of a board.

---

## Human-in-the-loop

This content is investor- and board-facing, which makes review non-negotiable.

| Role | Responsibility |
| :--- | :--- |
| **Agent** | First draft only. Computes metrics, derives risk items, drafts commentary, flags what it cannot support. |
| **Finance team** | First pass of review. Edits for accuracy, adds context the model could not have known, shapes the strategic framing. |
| **CFO** | Final reviewer and approver. Owns the company's external financial communications and signs off before anything is finalised into the deck. |

Nothing the agent produces is board-ready. Every output is labelled as a first
draft, the review queue lists everything unresolved, and `agent_runner.py`
returns **exit code 2** when an unreconciled source, a failed validation rule or
an arithmetic discrepancy means the pack must not go into a deck as-is.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then add your Groq key
python agent_runner.py
```

### `.env`

One model, configured in one place. The engine has no per-model table, so
switching model is a change to these lines and never a code change.

```ini
GROQ_API_KEY=gsk_...
DEFAULT_LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile

LLM_TPM_LIMIT=12000        # the model's tokens-per-minute ceiling
LLM_MIN_COMPLETION=1600    # completion room every request must leave

COMMENTARY_TONE=board_formal
DECK_AUDIENCE=board
```

`LLM_MIN_COMPLETION` matters more than it looks. Groq admits a request against
*prompt + max_tokens*, so the engine sizes the completion budget from what is
left under `LLM_TPM_LIMIT` and splits the drafting run into paced batches if the
sections do not fit in one request. Raise this floor for a **reasoning** model
(`gpt-oss`, `qwen3`, `deepseek-r1`): those spend tokens thinking before emitting
anything, and a request left with too little room returns an *empty* completion
that surfaces as `json_validate_failed` — which looks like a prompt problem and
is not one.

Environment variables already set in the shell override `.env`, so a single run
can be redirected without editing the file.

### CLI

```bash
python agent_runner.py                                          # board deck, configured tone
python agent_runner.py --audience investor                      # investor deck
python agent_runner.py --tone concise_summary                   # different tone preset
python agent_runner.py --quiet                                  # suppress the engine trace
```

Tone presets — `board_formal`, `investor_narrative`, `analyst_detailed`,
`concise_summary` — are defined in `input_config.yaml` and drive the "Tone:"
dropdown on the screen.

---

## Data feeds

Five feeds in `sample_data/`, matching Section 5 of the system design note:

| Feed | Contents |
| :--- | :--- |
| `quarterly_metrics.csv` | Historical quarterly time series, oldest first. The last row is the current quarter. Comparators are resolved by negative offset, so the pipeline works unchanged however many quarters are supplied. |
| `segment_breakdown.csv` | Current-quarter segment detail with prior-quarter and prior-year comparatives on the same row, so movement is attributable. This is the granular evidence the reported figures are reconciled against. |
| `one_off_items.csv` | Exceptional and non-recurring items finance wants explicitly called out. |
| `forecast_plan.csv` | Board-approved plan figures feeding the FORECAST column. |
| `budget_targets.csv` | Quarterly budget phasing feeding the variance strip. |
| `past_deck_commentary.json` | Approved commentary from prior decks — the tone and structure reference. Carries `final_vs_draft_diff`, how heavily the finance team rewrote the agent's previous draft, which tells the agent where its starting quality was weakest. |

Metric definitions live in `input_config.yaml` under `kpi_catalog`, so a metric is
described consistently deck to deck.

---

## What the agent does *not* do

**It does not forecast.** The FORECAST column carries approved plan figures
supplied by the business — the board's operating plan, the risk committee's
credit outlook, the ALCO funding plan. `forecast.allow_model_generated` is
`false`. The commentary may state a plan figure only as a plan, attributed to its
approver, never as an expectation. A model-invented projection in an investor
deck is a disclosure risk, not a convenience.

**It does not invent risk items.** The "Risk / attention items" card is derived
from the data by the rules in `input_config.yaml`. The model may describe them.
It may not invent one, and it may not suppress one.

**It does not decide whether a movement is good.** Direction is a property of the
metric, declared in the catalog. A rising GNPA is adverse; a rising AUM is
favourable. The model reads this rather than reasoning about it, and it is
forbidden from softening an adverse movement by burying it in a subordinate
clause or pairing it with a favourable one.

**It does not present a one-off as underlying performance.** Where exceptional
items exist, every affected metric must be stated both ways. On the sample data
the reported PAT of ₹48 Cr is up 9.4% QoQ; excluding a ₹6.4 Cr property gain and
a ₹2.4 Cr accelerated provision it is ₹45.0 Cr. Likewise AUM: of the ₹137 Cr
increase, ₹62 Cr was an acquired portfolio, leaving ₹74.9 Cr organic. Presenting
either headline alone is the most common way a well-intentioned draft misleads a
board, and the guardrail enforces the split.

**It does not run without the LLM failing safe.** If Groq is unreachable or out
of quota, a deterministic template drafter produces the same five sections from
the same fact base. The figures are identical — they never came from the model.
Only the prose is mechanical, and the run is labelled `DEGRADED RUN` everywhere
it surfaces.

---

## Outputs

Written to `output/`, which is gitignored — the figures come from `sample_data/`,
so a stale file committed to the repo would be mistaken for the current quarter.

| File | Purpose |
| :--- | :--- |
| `output/generated_investor_report.json` | The frontend contract. Validated against `config/output_schema.json` on every run. |
| `output/reporting_assistant.html` | Standalone reporting-assistant screen mirroring the Atlas Hub layout — key metrics alongside editable draft commentary, per-section accept, tone selector, and an export that emits the reviewed payload. Self-contained, no external assets. |
| `output/generated_commentary.md` | The full review pack: metrics, commentary with flags, risk items, attribution tables, reconciliation, validation, review queue. |
| `output/generated_commentary.pdf` | Printable board-ready version, watermarked as a first draft. |

An `OUTPUT_*_PATH` environment variable overrides a filename; an absolute value
redirects the file entirely.

Every top-level key in the JSON maps to a named panel on the screen:
`kpi_comparison`, `budget_variance`, `stage_movement`, `risk_attention_items`,
`source_traceability`, `report_readiness`, `ai_commentary`.

---

## Measuring the benefit

The use case names three measures, and each is emitted on every run:

| Measure | Where |
| :--- | :--- |
| Time saved in deck preparation | `time_saved` — manual minutes vs remaining review minutes, from the baselines in `operational_baseline` |
| Consistency of metric reporting quarter-over-quarter | `kpi_catalog` definitions travel with every figure; the same sections, beats and house style apply every quarter; `final_vs_draft_diff` tracks how much the finance team had to rewrite |
| Finance team hours freed for analysis | `report_readiness` separates agent-owned from human-owned work, so what the team still has to do is explicit rather than assumed |
