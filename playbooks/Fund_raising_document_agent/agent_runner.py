"""
CLI Execution Script for Fund-Raising Document Agent.

Thin wrapper over the single agent pipeline in fund_raising_agent.py.

It used to run a second, divergent calculation path (preprocess.compute_agent_output)
and write its results to the same generated_datapack.json. The two paths disagreed -
different revenue growth, GNPA, NNPA and leverage - so whichever ran last silently
won, and the JSON could contradict the PDF sitting beside it. There is now one
pipeline and one set of numbers.
"""
import os
from fund_raising_agent import run_agent

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    print("[INFO] Initializing Fund-Raising Document Agent...")
    output_data = run_agent()

    fin = output_data["financial_metrics"]
    port = output_data["portfolio_metrics"]
    ratios = output_data["key_ratios"]
    ops = output_data["operational_kpis"]

    print("--- KPI Summary ---")
    print(f"Period:  {output_data.get('reporting_period')}")
    print(f"Revenue: Rs. {fin['revenue_cr']} Cr ({fin['revenue_yoy_pct']:+}% YoY)")
    print(f"AUM:     Rs. {port['aum_cr']} Cr ({port['aum_qoq_pct']:+}% QoQ)")
    print(f"GNPA:    {port['gnpa_pct']}% | NNPA: {port['nnpa_pct']}%")
    print(f"DSCR:    {ratios['dscr']}x | Debt/Equity: {ratios['debt_to_equity']}x "
          f"| CRAR: {ratios['capital_adequacy_pct']}%")

    breaches = [c for c in output_data.get("covenant_audit", [])
                if str(c.get("status", "")).upper() == "FAIL"]
    print(f"Covenants: {len(output_data.get('covenant_audit', []))} audited, "
          f"{len(breaches)} breached")
    print(f"Validation: {ops['validation_checks_passed']}/{ops['validation_checks_total']} passed")
    print(f"Est. Time Saved: {ops['est_time_saved_hours']} hrs vs manual process")

    return output_data


if __name__ == "__main__":
    main()
