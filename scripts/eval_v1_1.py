"""End-to-end eval run for the v1.1 prompt against tests/eval/cases/v1.yaml.

Loads `prompts/system_v1_1.md` explicitly. v1.1 is currently the production
default in `src/wiki_qa/agent.py`, but this script pins the load explicitly
so future runs reproduce regardless of default-pointer drift.

Usage (from repo root):
    uv run python scripts/eval_v1_1.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from wiki_qa.agent import answer as agent_answer  # noqa: E402
from wiki_qa.agent_contract import AgentResult  # noqa: E402
from wiki_qa.eval.dataset import load_cases  # noqa: E402
from wiki_qa.eval.runner import run  # noqa: E402

PROMPT_PATH = ROOT / "prompts" / "system_v1_1.md"
CASES_PATH = ROOT / "tests" / "eval" / "cases" / "v1.yaml"


def _load_prompt() -> str:
    text = PROMPT_PATH.read_text()
    parts = text.split("\n---\n", 1)
    return (parts[1] if len(parts) == 2 else text).strip()


def main() -> None:
    cases = load_cases(str(CASES_PATH))
    prompt = _load_prompt()
    print(f"Loaded {len(cases)} cases. v1.1 prompt: {len(prompt)} chars.")

    def agent_with_v1_1(question: str) -> AgentResult:
        return agent_answer(question, system_prompt=prompt)

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_dir = ROOT / "eval_runs" / f"v1_1_{ts}"
    print(f"Output: {out_dir}")
    print("Firing eval — concurrency=3, real agent + judge (Opus 4.7).")
    sys.stdout.flush()

    report = run(
        cases=cases,
        agent_fn=agent_with_v1_1,
        concurrency=3,
        out_dir=out_dir,
    )

    print()
    print("=" * 70)
    print("RUN COMPLETE")
    print("=" * 70)
    print(f"Total: {report.total}  OK: {report.ok}  Errors: {report.errors}")
    print(f"Output: {report.out_dir}")


if __name__ == "__main__":
    main()
