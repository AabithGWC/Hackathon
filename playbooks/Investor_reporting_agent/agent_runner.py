"""
CLI entrypoint for the Investor / Board Reporting Agent.

    python agent_runner.py
    python agent_runner.py --tone investor_narrative --audience investor
    python agent_runner.py --quiet

Thin wrapper over the single pipeline in investor_reporting_agent.py. There is
one pipeline and one set of numbers - nothing here recomputes a figure.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from investor_engine import run_agent


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Draft the quarterly investor / board commentary pack.")
    ap.add_argument("--tone", default=None,
                    help="Tone preset id from input_config.yaml "
                         "(board_formal, investor_narrative, analyst_detailed, "
                         "concise_summary). Defaults to the configured default.")
    ap.add_argument("--audience", default=None, choices=["investor", "board"],
                    help="Which deck to draft. Board-only sections are omitted "
                         "from the investor deck.")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress the per-step engine trace.")
    args = ap.parse_args(argv)

    payload = run_agent(tone=args.tone, audience=args.audience,
                        verbose=not args.quiet)

    # A non-zero exit tells a scheduler the pack needs attention before it can
    # go into a deck: an unreconciled source, a failed rule, or an arithmetic
    # discrepancy is a blocking condition, not a warning.
    blocking = (
        any(r.get("breached") for r in payload["reconciliations"])
        or any(v["status"] == "FAIL" for v in payload["validation_results"])
        or bool(payload["arithmetic_audit"])
    )
    return 2 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
