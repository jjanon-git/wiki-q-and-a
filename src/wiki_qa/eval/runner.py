"""Eval runner: cases → agent → behavior_checks → results.jsonl.

v1 scope: deterministic checks only. Judge integration is a separate
later pass.

Design notes:

- **Agent injection.** `run` takes `agent_fn` as a keyword arg. Default
  is `wiki_qa.agent_stub.answer`. When workstream A's real agent lands at
  `wiki_qa.agent`, change the import line at the top of this file —
  that single line — to `from wiki_qa.agent import answer`. The runner's
  internals are agnostic.
- **Per-case error isolation.** Each case runs inside `_run_one` which
  catches every exception from `agent_fn` and `run_behavior_checks` and
  produces an `EvalResult(status="error", ...)`. A blow-up at case 20
  cannot abort the run; the future never raises.
- **Deterministic on-disk ordering.** Concurrency is via thread pool, so
  cases complete in non-deterministic order. We collect all results in
  memory, sort by `case_id`, then write `results.jsonl`. Therefore
  concurrency=1 and concurrency=3 produce byte-identical files for the
  same inputs.
- **No real API calls in tests.** Tests inject a fake `agent_fn`; the
  default stub is never invoked from the test suite.
"""

from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from wiki_qa.agent_contract import AgentResult
from wiki_qa.agent_stub import answer as _default_agent_fn
from wiki_qa.eval.behavior_checks import run_behavior_checks
from wiki_qa.eval.judge import JudgeOutput
from wiki_qa.eval.judge import evaluate as _default_judge_fn
from wiki_qa.eval.results import EvalResult
from wiki_qa.eval.schema import EvalCase

DEFAULT_CONCURRENCY = 3


class AgentFn(Protocol):
    def __call__(self, question: str, /, *, max_iterations: int = 5) -> AgentResult: ...


class JudgeFn(Protocol):
    def __call__(self, case: EvalCase, agent_result: AgentResult, /) -> JudgeOutput: ...


class RunReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    total: int
    ok: int
    errors: int
    out_dir: str

    @property
    def all_ok(self) -> bool:
        return self.errors == 0 and self.total > 0


def run(
    cases: list[EvalCase],
    *,
    agent_fn: AgentFn | None = None,
    judge_fn: JudgeFn | None = None,
    judge_enabled: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
    out_dir: Path,
) -> RunReport:
    fn: AgentFn = agent_fn if agent_fn is not None else _default_agent_fn
    jfn: JudgeFn | None = (
        (judge_fn if judge_fn is not None else _default_judge_fn) if judge_enabled else None
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    if not cases:
        results_path.write_text("")
        return RunReport(total=0, ok=0, errors=0, out_dir=str(out_dir))

    results: list[EvalResult] = []

    if concurrency <= 1:
        for case in cases:
            results.append(_run_one(case, fn, jfn))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_run_one, case, fn, jfn) for case in cases]
            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda r: r.case_id)
    _write_jsonl(results_path, results)

    ok_count = sum(1 for r in results if r.status == "ok")
    error_count = sum(1 for r in results if r.status == "error")
    return RunReport(total=len(results), ok=ok_count, errors=error_count, out_dir=str(out_dir))


def _run_one(case: EvalCase, agent_fn: AgentFn, judge_fn: JudgeFn | None) -> EvalResult:
    start = time.perf_counter()
    try:
        agent_result = agent_fn(case.question)
        checks = run_behavior_checks(case, agent_result)
        judge_output = judge_fn(case, agent_result) if judge_fn is not None else None
        return EvalResult(
            case_id=case.id,
            category=case.category,
            difficulty=case.difficulty,
            question=case.question,
            status="ok",
            agent_result=agent_result,
            behavior_checks=checks,
            judge_output=judge_output,
            error=None,
            duration_ms=_elapsed_ms(start),
        )
    except Exception as exc:
        return EvalResult(
            case_id=case.id,
            category=case.category,
            difficulty=case.difficulty,
            question=case.question,
            status="error",
            agent_result=None,
            behavior_checks=None,
            judge_output=None,
            error=_format_error(exc),
            duration_ms=_elapsed_ms(start),
        )


def _elapsed_ms(start: float) -> int:
    return max(1, int((time.perf_counter() - start) * 1000))


def _format_error(exc: BaseException) -> str:
    summary = f"{type(exc).__name__}: {exc}"
    tb = traceback.format_exception_only(type(exc), exc)
    detail = "".join(tb).strip()
    return detail if detail else summary


def _write_jsonl(path: Path, results: list[EvalResult]) -> None:
    with path.open("w") as fh:
        for r in results:
            fh.write(r.model_dump_json())
            fh.write("\n")
