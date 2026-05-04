"""Per-case runner output.

`EvalResult` is one line per case in `results.jsonl`. Lives in its own
module to avoid circular imports between `schema` (EvalCase) and
`behavior_checks` (BehaviorChecks).

`status="ok"` means the agent ran and behavior_checks were computed (some
individual checks may have failed — that's a per-check status, not a
case-level status). `status="error"` means the agent_fn raised;
`agent_result` and `behavior_checks` are None and `error` carries a
short exception summary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from wiki_qa.agent_contract import AgentResult
from wiki_qa.eval.behavior_checks import BehaviorChecks
from wiki_qa.eval.judge import JudgeOutput


class EvalResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    case_id: str
    category: str
    difficulty: str
    question: str
    status: Literal["ok", "error"]
    agent_result: AgentResult | None
    behavior_checks: BehaviorChecks | None
    # Judge output is None when:
    # - judge was disabled for the run (--no-judge), or
    # - the agent errored, so there was nothing to judge.
    judge_output: JudgeOutput | None
    error: str | None
    duration_ms: int
