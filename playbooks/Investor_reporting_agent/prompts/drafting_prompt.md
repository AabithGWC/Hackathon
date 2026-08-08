# DRAFTING PLAYBOOK

This is the fixed procedure for drafting each commentary card. It exists so the
output follows a consistent, reviewable structure rather than free-form
generation — the finance team should be able to open any two quarters side by
side and find the same information in the same order.

---

## THE FOUR-STEP PATTERN, PER METRIC

For every headline metric in a section, in this order:

1. **State the current value.** With its unit and its full precision as supplied.
2. **State the comparators.** Prior quarter first, then the same quarter a year
   earlier. Name the quarters — "against ₹1,113 Cr in the preceding quarter",
   not "against last quarter".
3. **State the variance.** In the correct unit of change:
   - Amounts: currency *and* percent — "an increase of ₹137 Cr, or 12.3%".
   - Ratios and percentages: percentage points or basis points — "improved by
     20 basis points to 1.80%". Never "improved 20 percent".
4. **State the driver, or state that you cannot.** From the attribution evidence
   only. Name the segment and quantify its contribution.

If the movement is below the metric's materiality threshold, do steps 1 and 2,
skip steps 3 and 4, and describe the metric as broadly unchanged.

---

## SECTION-BY-SECTION GUIDANCE

### AUM & Growth Overview
Open with the AUM level and the QoQ movement, since that is what the card's badge
shows. Give the YoY movement in the same sentence or the next. Then
disbursements. Then the segment drivers, in descending order of contribution,
naming at most the top two or three.

If an inorganic addition is disclosed in the one-off items, you must split the
movement: state total growth, state the acquired amount, and state the organic
residual. Both figures are in the payload. Presenting a portfolio purchase as
origination momentum is a material misrepresentation.

If growth this quarter is markedly different in pace from the preceding
quarters, note that plainly — an acceleration or deceleration is itself the
story, and the board will ask about it.

### Asset Quality & Provisioning
Lead with GNPA and NNPA levels and their movement in basis points against both
comparators. Then the staging picture: Stage 2 and Stage 3 balances and the net
new flow into Stage 3.

A headline ratio can improve while the underlying picture deteriorates — GNPA can
fall simply because the denominator grew. The attribution evidence separates the
**stock effect** (change in Stage 3 balances) from the **denominator effect**
(change in book size). If the denominator effect is doing most of the work, say
so explicitly. This is precisely the kind of thing a board expects its finance
function to surface unprompted.

Then concentration: which segments carry the stress. Then provision coverage,
including any variance between the segment ledger and the reporting system.

### Profitability & Margins
Revenue, EBITDA and PAT, each with QoQ and YoY movement. Then margins. Then
performance against budget where a budget figure is supplied.

If exceptional items exist, the reported PAT movement and the ex-one-off PAT
movement must both appear, with the item named. State the reported figure first,
then the adjusted one — "profit after tax was ₹48 Cr, 9.4% above the preceding
quarter; excluding the ₹6.4 Cr gain on the sale of the Coimbatore property and a
₹2.4 Cr accelerated provision, profit after tax was ₹45.0 Cr".

### Funding & Cost of Funds
Cost of funds level and movement **in basis points**. Borrowings and leverage at
quarter end. What the movement is attributable to, from the data supplied — and
if the data supplied does not identify a cause, say that rather than reaching for
the usual explanation about policy rates.

### Executive Summary
Written last, drawing only on what the other four sections already established.
Introduce no figure and no claim that does not appear in them.

One opening sentence on the scale and direction of the quarter. Then growth,
asset quality and profitability, each in a clause, each with a figure. Then the
single most important movement of the quarter and its driver. Then, named without
elaboration, anything requiring board attention.

---

## WHAT TO DO WITH THE RISK ITEMS

The risk / attention items on the screen are derived from the data by rule, not
by you. You may reference them in the commentary and you should, where they are
material to a section. You may not invent one, and you may not omit a High
severity item from the Executive Summary.

---

## LENGTH

Each section has a target word count in the payload. Treat it as a target, not a
floor: a section that says everything required in fewer words is better, not
worse. Never exceed the configured maximum — the card is a fixed-height textarea
and an overlong draft is one the finance team has to cut before it can edit.

---

## THE FLAGS YOU MUST RAISE YOURSELF

Alongside the prose, return for each section:

- `unexplained_movements` — every material movement you could not attribute to a
  driver from the evidence supplied. Be specific about what data would have let
  you explain it.
- `kpis_referenced` — the metric keys you actually quoted.

The engine runs its own checks on top of these — numeric grounding, forward-
looking language, banned phrases — and will return a section to you once with
the specific violations quoted if it finds any. When that happens, fix exactly
what was quoted and change nothing else.
