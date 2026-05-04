"""Fixture-driven agent stub for harness development.

The runner imports `answer` from here while workstream A's real agent is
under construction. When the real agent lands at `wiki_qa.agent`, change
the runner's import line — that single line — to point to the new
module. Nothing else in the harness changes; the AgentResult contract
is frozen.

Fixture format: a YAML list of entries keyed by `question`, each with
`answer`, `tool_calls`, `stop_reason`, `usage`. See
`tests/eval/fixtures/agent_outputs.yaml`.

The fixture path defaults to `<repo>/tests/eval/fixtures/agent_outputs.yaml`,
overridable via the `WIKI_QA_AGENT_STUB_FIXTURE` env var.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from wiki_qa.agent_contract import AgentResult, TokenUsage, ToolCall

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_FIXTURE_PATH = _REPO_ROOT / "tests" / "eval" / "fixtures" / "agent_outputs.yaml"
_FIXTURE_ENV_VAR = "WIKI_QA_AGENT_STUB_FIXTURE"


def answer(question: str, *, max_iterations: int = 5) -> AgentResult:
    fixture = _load_fixture()
    if question not in fixture:
        raise KeyError(
            f"agent_stub has no canned output for question {question!r}; "
            f"add an entry to {_fixture_path()}"
        )
    return fixture[question]


def _fixture_path() -> Path:
    override = os.environ.get(_FIXTURE_ENV_VAR)
    return Path(override) if override else _DEFAULT_FIXTURE_PATH


@lru_cache(maxsize=1)
def _load_fixture() -> dict[str, AgentResult]:
    path = _fixture_path()
    with path.open() as fh:
        raw = yaml.safe_load(fh) or []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected top-level YAML list")

    by_question: dict[str, AgentResult] = {}
    for entry in raw:
        result = _build_agent_result(entry)
        if result.question in by_question:
            raise ValueError(f"{path}: duplicate question {result.question!r}")
        by_question[result.question] = result
    return by_question


def _build_agent_result(entry: dict[str, Any]) -> AgentResult:
    question = str(entry["question"])
    answer_text = str(entry["answer"])
    evidence_text = str(entry.get("evidence", ""))
    raw_output = str(
        entry.get(
            "raw_output",
            f"<evidence>{evidence_text}</evidence>\n<answer>{answer_text}</answer>",
        )
    )
    tool_calls = [_build_tool_call(tc) for tc in entry.get("tool_calls", [])]
    queries = [tc.query for tc in tool_calls]
    n_searches = sum(1 for tc in tool_calls if tc.name == "search_wikipedia")
    usage = _build_usage(entry.get("usage", {}))
    return AgentResult(
        question=question,
        evidence=evidence_text,
        answer=answer_text,
        raw_output=raw_output,
        tool_calls=tool_calls,
        n_searches=n_searches,
        queries=queries,
        stop_reason=str(entry.get("stop_reason", "end_turn")),
        usage=usage,
        raw_messages=[],
    )


def _build_tool_call(raw: dict[str, Any]) -> ToolCall:
    return ToolCall(
        name=str(raw.get("name", "search_wikipedia")),
        query=str(raw["query"]),
        raw_result_str=str(raw["raw_result_str"]),
        latency_ms=int(raw.get("latency_ms", 0)),
    )


def _build_usage(raw: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=int(raw.get("input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        cache_read_tokens=int(raw.get("cache_read_tokens", 0)),
        cache_creation_tokens=int(raw.get("cache_creation_tokens", 0)),
    )
