# Reconciliation Agent (LLM Calculation Engine)

Agent 1 of a 7-agent NBFC finance AI suite. Performs three-way reconciliation across
LMS transactions, bank statements, and GL entries: rule-based matching finds clean
matches, an LLM (Groq / Llama 3.3 70B) adjudicates each unmatched break with a
reasoning + confidence call, and results are persisted to SQLite (`agent_runs.db`).

## Architecture

```
/engine                     - generic LLM calculation runner, reusable across all future agents
  run_agent.py               generic runner: python engine/run_agent.py --playbook <name>
  llm_client.py               Groq API wrapper (JSON mode)
  validator.py                JSON-schema validation with single retry
  store.py                    SQLite run history (agent_runs table)

/playbooks/reconciliation    - playbook specific logic
  system_prompt.md            LLM instructions for adjudicating one break
  insights_prompt.md          LLM instructions for run-over-run insights
  output_schema.json          schema the break adjudication output must match
  input_config.yaml           input CSV paths + matching/LLM config
  preprocess.py                rule-based matching + aggregate computation (no LLM calls)

/sample_data                 - generated demo data (~150 rows, 3 systems)
```

Adding agent 2 means adding a new `/playbooks/<name>/` folder with the same five
files — `run_agent.py`, `llm_client.py`, `validator.py`, and `store.py` don't change.

## Setup

1. Install Python 3.11+ and dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and add your Groq API key:

   ```bash
   cp .env.example .env
   ```

   Get a key at https://console.groq.com/keys.

Sample data in `/sample_data` is already generated — no manual data prep needed.

## Run the LLM Calculation Agent

Run standard summary + top break adjudication reasoning output:
```bash
python engine/run_agent.py --playbook reconciliation
```

Output raw JSON calculation results:
```bash
python engine/run_agent.py --playbook reconciliation --json
```

Print detailed LLM break adjudication & reasoning breakdown:
```bash
python engine/run_agent.py --playbook reconciliation --verbose
```

This loads the 3 sample CSVs, rule-matches transactions, calls the LLM to adjudicate
each unmatched break plus a run-over-run insights call, validates every LLM response
against `output_schema.json`, and writes the full result to `agent_runs.db` (SQLite).

## Notes

- The LLM call layer retries once on a schema-validation failure (via `validator.py`)
  and retries with exponential backoff on Groq rate-limit (429) errors (via
  `llm_client.py`) — both raise a clear error if retries are exhausted.
- Break adjudication calls run concurrently (bounded pool) for efficiency, one LLM
  call per break, each validated independently against `output_schema.json`.

