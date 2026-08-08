# HOUSE STYLE FOR INVESTOR & BOARD COMMENTARY

Mechanical conventions. These are not suggestions — the finance team edits
against them every quarter, and getting them right is most of the difference
between a draft that is refined and a draft that is rewritten.

---

## NUMBERS

| Convention | Write | Not |
| :--- | :--- | :--- |
| Currency | `₹1,250 Cr` | `Rs 1250 crore`, `INR 1,250` |
| Thousands separator | `₹1,250 Cr` | `₹1250 Cr` |
| Percentages | `1.80%` | `1.8 %`, `1.8 percent` |
| Ratio movement | `20 basis points`, `0.20 percentage points` | `0.2 percent`, `20%` |
| Amount movement | `₹137 Cr, or 12.3%` | `12.3 percent` |
| Multiples | `3.8 times` | `3.8x` in prose |
| Precision | exactly as supplied in the payload | rounded to taste |

Never round a figure the payload gave you at higher precision, and never add
precision it did not give you.

A percentage and a percentage point are different quantities. A GNPA ratio moving
from 2.00% to 1.80% has improved by **20 basis points** or **0.20 percentage
points**. It has not improved by 20%, and it has not improved by 10%.

---

## PERIODS

Name them: `Q1 FY27`, `the preceding quarter`, `the same quarter a year earlier`.

Not: `last quarter`, `LQ`, `this time last year`, `Q1`.

---

## VOICE

Third person about the company. `The company`, `the portfolio`, `AUM`.

Never `we`, `our`, `us`, `I`, or `the Company` with a capital C.

Past tense for the quarter's events. Present tense for positions at quarter end.

> AUM **closed** the quarter at ₹1,250 Cr. Provision coverage **is** 49.3%.

---

## SENTENCES

Short and declarative. One idea each. If a sentence has two clauses joined by
"while" or "although", check whether it is being used to soften something adverse
— if so, split it into two sentences.

Lead with the figure, not with a build-up.

> **Write:** Gross NPA improved 20 basis points to 1.80%.
> **Not:** The company saw a continued improvement in asset quality metrics
> during the quarter, with gross NPA declining to 1.80%.

---

## WORDS TO AVOID

**Promotional:** robust, stellar, strong momentum, record, unprecedented,
best-ever, world-class, healthy, impressive, encouraging, pleasing.

**Vague intensifiers:** significantly, substantially, materially (unless used in
its accounting sense), considerably, sharply.

Quantify instead. "Grew significantly" says nothing; "grew 12.3%" says
everything.

**Forward-looking:** will, expects, anticipates, is poised, on track, going
forward, outlook, guidance, likely to, should. See the guardrail list in
`input_config.yaml` for the full set — anything on it is stripped out and flagged
before the draft reaches the finance team.

**False causation:** caused by, due to the fact that, proves, clearly shows.

Prefer attributive phrasing that reflects what the data can actually support:

> **Write:** The increase was concentrated in the Commercial Vehicle book, which
> accounted for 22% of the quarter's AUM growth.
> **Not:** The increase was caused by strong demand in commercial vehicles.

---

## STRUCTURE OF A SECTION

Level, then movement, then driver. Every time.

Do not open a section with context, background, or a scene-setting sentence. The
reader knows what quarter it is and what business the company is in.

---

## ADVERSE MOVEMENTS

Give them their own sentence, in the same register as everything else. Do not
apologise for them, do not explain them away, and do not immediately follow them
with a favourable figure to rebalance the paragraph.

> **Write:** Stage 2 exposure rose to ₹96.0 Cr from ₹71.2 Cr. The increase was
> concentrated in the SME Construction book, which accounted for ₹48.0 Cr of
> the movement.
> **Not:** While stage 2 exposure saw a modest uptick, overall asset quality
> continued to improve with GNPA declining to 1.80%.

---

## WHEN YOU CANNOT EXPLAIN SOMETHING

Say so, plainly, in the prose. Name the data that would have let you explain it.

> The increase in Stage 2 exposure is concentrated in the SME Construction
> segment. The origination-vintage detail needed to identify which cohort
> migrated was not available in the data supplied for this quarter.

This is a good sentence. It tells the CFO exactly what to ask for. A confident
invented explanation in its place is the failure mode this whole system exists to
prevent.
