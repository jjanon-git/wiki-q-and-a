"""End-to-end smoke: dev cases + agent stub + behavior checks all green.

Validates that the harness pieces compose correctly before the runner is
written. If this regresses, one of: dev.yaml, the agent_outputs fixture,
or behavior_checks has drifted out of alignment with the others.
"""

from __future__ import annotations

from pathlib import Path

from wiki_qa import agent_stub
from wiki_qa.eval.behavior_checks import run_behavior_checks
from wiki_qa.eval.dataset import load_cases

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEV_CASES_GLOB = REPO_ROOT / "tests" / "eval" / "cases" / "dev.yaml"


def test_dev_cases_run_through_stub_and_pass_all_applicable_checks() -> None:
    cases = load_cases(DEV_CASES_GLOB)
    assert len(cases) == 3, "expected 3 placeholder cases in dev.yaml"

    expected_categories = {"simple_factual", "negative_capability", "false_premise"}
    assert {c.category for c in cases} == expected_categories

    for case in cases:
        result = agent_stub.answer(case.question)
        checks = run_behavior_checks(case, result)
        # Every applicable check should pass for the canned dev outputs.
        # NA is fine; failures indicate drift between fixture, case YAML, or check logic.
        assert checks.failed == 0, (
            f"case {case.id}: {checks.failed} failed check(s); "
            f"{[c for c in checks.checks if c.status == 'fail']}"
        )
