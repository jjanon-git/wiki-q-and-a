"""Eval-harness data types.

`EvalCase` and `ExpectedBehavior` are the dataset shape. Loaded from
YAML by `wiki_qa.eval.dataset.load_cases`.

`expected_behavior` flags split into two groups (see plans/eval_harness.md):
- Deterministic (`must_search`, `must_not_search`): checked by the harness
  against AgentResult.
- Judge-context (`must_surface_premise_discrepancy`, `must_refuse`): passed
  into the judge prompt as case context. Inputs to the judge, not outputs.

Pydantic per CLAUDE.md "Shared types" — anything that hits disk or crosses
module boundaries lives as a frozen BaseModel with `extra="forbid"`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {
        "simple_factual",
        "multi_hop",
        "multi_source",
        "disambiguation_explicit",
        "buried_answer",
        "negative_capability",
        "false_premise",
        "unanswerable_not_in_wp",
        "unanswerable_too_recent",
        "temporal",
    }
)

ALLOWED_DIFFICULTIES: frozenset[str] = frozenset({"easy", "medium", "hard"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExpectedBehavior(_Frozen):
    must_search: bool = False
    must_not_search: bool = False
    must_surface_premise_discrepancy: bool = False
    must_refuse: bool = False


class EvalCase(_Frozen):
    id: str
    category: str
    difficulty: str
    question: str
    expected_answer: str
    expected_behavior: ExpectedBehavior = ExpectedBehavior()
    notes: str = ""
