"""
Project layout, resolved in one place.

Every other module imports from here rather than deriving its own BASE_DIR, so
moving a file does not silently break a path, and the folder structure is
documented by the code that depends on it.

    <root>/
      agent_runner.py        CLI entrypoint
      .env                   credentials and model rate limits (gitignored)
      config/                ALL technical aspects
        input_config.yaml      KPI catalog, comparators, attribution, risk
                               rules, guardrails, validation, frontend contract
        output_schema.json     the payload contract, validated every run
      prompts/               ALL narrative instruction
        system_prompt.md       persona and the rules that matter most
        drafting_prompt.md     the fixed drafting playbook
        style_guide.md         house style
      engine/                the code
        paths.py               this file
        metrics_engine.py      every figure - no LLM
        llm_engine.py          Groq drafting plus the guardrail layers
        report_renderer.py     JSON / Markdown / PDF / HTML
        pipeline.py            orchestration
      sample_data/           the five quarterly data feeds
      output/                generated artefacts (gitignored)
"""

import os

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE_DIR)

CONFIG_DIR = os.path.join(ROOT, "config")
PROMPTS_DIR = os.path.join(ROOT, "prompts")
SAMPLE_DIR = os.path.join(ROOT, "sample_data")
OUTPUT_DIR = os.path.join(ROOT, "output")

ENV_FILE = os.path.join(ROOT, ".env")
CONFIG_FILE = os.path.join(CONFIG_DIR, "input_config.yaml")
SCHEMA_FILE = os.path.join(CONFIG_DIR, "output_schema.json")


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def resolve(path, base=None):
    """
    Resolves a path from the config against the project root, so `inputs:` in
    input_config.yaml stays written the way a human would write it
    (`sample_data/quarterly_metrics.csv`) regardless of where it is read from.
    """
    if not path:
        return None
    return path if os.path.isabs(path) else os.path.join(base or ROOT, path)
