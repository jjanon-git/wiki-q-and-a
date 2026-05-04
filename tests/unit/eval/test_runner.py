"""Tests for the eval runner.

Verifies the four properties the runner promises:

1. Per-case behavior_checks computed for every successful case.
2. Per-case error isolation: an exception in agent_fn for one case must
   not affect the others; the failing case shows up as status="error".
3. Deterministic on-disk ordering: results.jsonl is sorted by case_id
   regardless of completion order, at concurrency=1 and concurrency=3.
4. No real API calls: runner takes agent_fn via dependency injection;
   tests pass fakes; the default (`agent_stub.answer`) is never called
   in this file.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wiki_qa.agent_contract import AgentResult, TokenUsage
from wiki_qa.eval.judge import DimensionScore, JudgeOutput
from wiki_qa.eval.results import EvalResult
from wiki_qa.eval.runner import run
from wiki_qa.eval.schema import EvalCase, ExpectedBehavior


def _case(
    *,
    id: str,
    category: str = "simple_factual",
    difficulty: str = "easy",
    question: str | None = None,
    must_search: bool = False,
    must_not_search: bool = False,
) -> EvalCase:
    return EvalCase(
        id=id,
        category=category,
        difficulty=difficulty,
        question=question or f"Q for {id}?",
        expected_answer="A",
        expected_behavior=ExpectedBehavior(
            must_search=must_search, must_not_search=must_not_search
        ),
    )


def _agent_result(
    question: str, *, answer: str = "Answer per [Foo].\n\nSources:\nFoo - https://x\n"
) -> AgentResult:
    return AgentResult(
        question=question,
        evidence="",
        answer=answer,
        raw_output=f"<evidence></evidence>\n<answer>{answer}</answer>",
        tool_calls=[],
        n_searches=0,
        queries=[],
        stop_reason="end_turn",
        usage=TokenUsage(
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0
        ),
        raw_messages=[],
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------- happy path ----------


def test_runner_writes_results_jsonl_one_line_per_case(tmp_path: Path) -> None:
    cases = [_case(id="a_001"), _case(id="a_002"), _case(id="a_003")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    report = run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)

    results_path = tmp_path / "results.jsonl"
    assert results_path.exists()
    rows = _read_jsonl(results_path)
    assert len(rows) == 3
    assert report.total == 3
    assert report.errors == 0


def test_runner_runs_behavior_checks_for_each_ok_case(tmp_path: Path) -> None:
    cases = [_case(id="bc_001"), _case(id="bc_002")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)

    rows = _read_jsonl(tmp_path / "results.jsonl")
    for row in rows:
        assert row["status"] == "ok"
        assert row["behavior_checks"] is not None
        assert isinstance(row["behavior_checks"]["checks"], list)
        assert len(row["behavior_checks"]["checks"]) == 11


# ---------- per-case error isolation ----------


def test_runner_continues_after_per_case_failure(tmp_path: Path) -> None:
    cases = [_case(id="ok_001"), _case(id="boom_002"), _case(id="ok_003")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        if "boom_002" in q:
            raise RuntimeError("simulated agent failure")
        return _agent_result(q)

    report = run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)

    rows = _read_jsonl(tmp_path / "results.jsonl")
    by_id = {row["case_id"]: row for row in rows}
    assert by_id["ok_001"]["status"] == "ok"
    assert by_id["boom_002"]["status"] == "error"
    assert "simulated agent failure" in by_id["boom_002"]["error"]
    assert by_id["boom_002"]["agent_result"] is None
    assert by_id["boom_002"]["behavior_checks"] is None
    assert by_id["ok_003"]["status"] == "ok"
    assert report.total == 3
    assert report.errors == 1


def test_runner_records_duration_for_errored_cases_too(tmp_path: Path) -> None:
    cases = [_case(id="boom_only")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        time.sleep(0.01)
        raise RuntimeError("boom")

    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)
    rows = _read_jsonl(tmp_path / "results.jsonl")
    assert rows[0]["duration_ms"] >= 1


# ---------- deterministic ordering ----------


def test_runner_sorts_results_by_case_id_at_concurrency_1(tmp_path: Path) -> None:
    # input deliberately out of id-order
    cases = [_case(id="zeta_999"), _case(id="alpha_001"), _case(id="middle_500")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)

    rows = _read_jsonl(tmp_path / "results.jsonl")
    assert [r["case_id"] for r in rows] == ["alpha_001", "middle_500", "zeta_999"]


def test_runner_sorts_results_by_case_id_at_concurrency_3_under_jitter(
    tmp_path: Path,
) -> None:
    """Force out-of-order completion via per-case sleeps; assert disk order
    is still by case_id."""
    cases = [_case(id="alpha_001"), _case(id="beta_002"), _case(id="gamma_003")]

    delays = {"alpha_001": 0.06, "beta_002": 0.02, "gamma_003": 0.04}

    completion_order: list[str] = []
    completion_lock = threading.Lock()

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        case_id = q.split()[-1].rstrip("?")
        time.sleep(delays[case_id])
        with completion_lock:
            completion_order.append(case_id)
        return _agent_result(q)

    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=3, out_dir=tmp_path)

    # completion order must NOT be id-sorted (otherwise the test isn't proving anything)
    assert completion_order != sorted(completion_order)
    # but on-disk order MUST be id-sorted
    rows = _read_jsonl(tmp_path / "results.jsonl")
    assert [r["case_id"] for r in rows] == sorted(r["case_id"] for r in rows)


def test_runner_concurrency_1_and_3_produce_identical_output(tmp_path: Path) -> None:
    cases = [_case(id=f"case_{i:03d}") for i in (3, 1, 2, 5, 4)]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    out1 = tmp_path / "serial"
    out3 = tmp_path / "parallel"
    out1.mkdir()
    out3.mkdir()

    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=out1)
    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=3, out_dir=out3)

    text1 = (out1 / "results.jsonl").read_text()
    text3 = (out3 / "results.jsonl").read_text()
    assert text1 == text3, "concurrency=1 and concurrency=3 should produce byte-identical output"


# ---------- summary report ----------


def test_runner_summary_counts_ok_vs_error(tmp_path: Path) -> None:
    cases = [_case(id="o1"), _case(id="o2"), _case(id="e1"), _case(id="e2")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        if "e" in q.split()[-1]:
            raise RuntimeError("nope")
        return _agent_result(q)

    report = run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)

    assert report.total == 4
    assert report.ok == 2
    assert report.errors == 2


# ---------- empty input ----------


def test_runner_handles_empty_case_list(tmp_path: Path) -> None:
    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        raise AssertionError("agent_fn should not be called for empty case list")

    report = run([], agent_fn=agent_fn, concurrency=3, out_dir=tmp_path)
    assert report.total == 0
    assert (tmp_path / "results.jsonl").read_text() == ""


# ---------- DI guard ----------


def test_runner_does_not_call_default_agent_when_agent_fn_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: if a future change accidentally falls back to the default
    agent (the stub) when agent_fn is provided, this test catches it."""
    from wiki_qa import agent_stub

    sentinel = []

    def boom(*a: object, **k: object) -> AgentResult:
        sentinel.append("called")
        raise AssertionError("default agent must not be invoked when agent_fn is passed")

    monkeypatch.setattr(agent_stub, "answer", boom)

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    cases = [_case(id="c1")]
    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)
    assert sentinel == []


# ---------- contract sanity: EvalResult round-trips through JSON ----------


def test_eval_result_round_trip_via_jsonl(tmp_path: Path) -> None:
    cases = [_case(id="rt_001")]

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    run(cases, agent_fn=agent_fn, judge_enabled=False, concurrency=1, out_dir=tmp_path)

    line = (tmp_path / "results.jsonl").read_text().splitlines()[0]
    parsed = EvalResult.model_validate_json(line)
    assert parsed.case_id == "rt_001"
    assert parsed.status == "ok"
    assert parsed.agent_result is not None
    assert parsed.behavior_checks is not None
    # judge_enabled=False → judge_output is None
    assert parsed.judge_output is None


# ---------- judge integration ----------


def _fake_judge_output_factory() -> Callable[[], JudgeOutput]:
    def make() -> JudgeOutput:
        return JudgeOutput(
            dimensions=[
                DimensionScore(name=n, score=2, reasoning="reason for " + n, flags=[])
                for n in (
                    "factual_accuracy",
                    "groundedness",
                    "citation_quality",
                    "search_efficiency",
                    "calibration",
                )
            ],
            raw_response="<evaluation>...</evaluation>",
            judge_failure=False,
            retries=0,
        )

    return make


def test_runner_calls_judge_when_enabled_and_writes_judge_output(tmp_path: Path) -> None:
    cases = [_case(id="j_001")]
    judge_calls: list[tuple[str, str]] = []
    make_output = _fake_judge_output_factory()

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    def judge_fn(case: EvalCase, agent_result: AgentResult) -> Any:
        judge_calls.append((case.id, agent_result.question))
        return make_output()

    run(
        cases,
        agent_fn=agent_fn,
        judge_fn=judge_fn,
        judge_enabled=True,
        concurrency=1,
        out_dir=tmp_path,
    )

    rows = _read_jsonl(tmp_path / "results.jsonl")
    assert rows[0]["judge_output"] is not None
    assert rows[0]["judge_output"]["judge_failure"] is False
    assert len(rows[0]["judge_output"]["dimensions"]) == 5
    assert len(judge_calls) == 1
    assert judge_calls[0][0] == "j_001"


def test_runner_skips_judge_for_errored_cases(tmp_path: Path) -> None:
    cases = [_case(id="boom_001")]
    judge_calls: list[str] = []
    make_output = _fake_judge_output_factory()

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        raise RuntimeError("agent down")

    def judge_fn(case: EvalCase, agent_result: AgentResult) -> Any:
        judge_calls.append(case.id)
        return make_output()

    run(
        cases,
        agent_fn=agent_fn,
        judge_fn=judge_fn,
        judge_enabled=True,
        concurrency=1,
        out_dir=tmp_path,
    )

    rows = _read_jsonl(tmp_path / "results.jsonl")
    assert rows[0]["status"] == "error"
    assert rows[0]["judge_output"] is None
    assert judge_calls == [], "judge must not be called on errored cases"


def test_runner_judge_failure_is_per_case_not_run_failure(tmp_path: Path) -> None:
    """A judge_fn that raises for one case errors that case but doesn't
    abort the run."""
    cases = [_case(id="ok_001"), _case(id="judge_boom_002")]
    make_output = _fake_judge_output_factory()

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    def judge_fn(case: EvalCase, agent_result: AgentResult) -> Any:
        if "boom" in case.id:
            raise RuntimeError("judge down")
        return make_output()

    report = run(
        cases,
        agent_fn=agent_fn,
        judge_fn=judge_fn,
        judge_enabled=True,
        concurrency=1,
        out_dir=tmp_path,
    )

    rows = _read_jsonl(tmp_path / "results.jsonl")
    by_id = {r["case_id"]: r for r in rows}
    assert by_id["ok_001"]["status"] == "ok"
    assert by_id["judge_boom_002"]["status"] == "error"
    assert "judge down" in by_id["judge_boom_002"]["error"]
    assert report.total == 2
    assert report.errors == 1


def test_runner_does_not_call_default_judge_when_judge_fn_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: if a future change accidentally falls back to the
    default judge when judge_fn is passed, this test catches it."""
    from wiki_qa.eval import judge as judge_module

    sentinel = []
    make_output = _fake_judge_output_factory()

    def boom(*a: object, **k: object) -> Any:
        sentinel.append("called")
        raise AssertionError("default judge must not be invoked when judge_fn is passed")

    monkeypatch.setattr(judge_module, "evaluate", boom)

    def agent_fn(q: str, *, max_iterations: int = 5) -> AgentResult:
        return _agent_result(q)

    def judge_fn(case: EvalCase, agent_result: AgentResult) -> Any:
        return make_output()

    cases = [_case(id="c1")]
    run(
        cases,
        agent_fn=agent_fn,
        judge_fn=judge_fn,
        judge_enabled=True,
        concurrency=1,
        out_dir=tmp_path,
    )
    assert sentinel == []
