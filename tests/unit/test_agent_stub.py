"""Smoke tests for the fixture-driven agent stub."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wiki_qa import agent_stub
from wiki_qa.agent_contract import AgentResult


@pytest.fixture(autouse=True)
def _clear_fixture_cache() -> None:
    agent_stub._load_fixture.cache_clear()


def _write_fixture(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    path = tmp_path / "agent_outputs.yaml"
    path.write_text(yaml.safe_dump(entries))
    return path


def test_answer_returns_canned_result_for_known_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_path = _write_fixture(
        tmp_path,
        [
            {
                "question": "Q1?",
                "answer": "A1.",
                "tool_calls": [
                    {
                        "name": "search_wikipedia",
                        "query": "q",
                        "raw_result_str": "<search_results/>",
                        "latency_ms": 100,
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                },
            }
        ],
    )
    monkeypatch.setenv("WIKI_QA_AGENT_STUB_FIXTURE", str(fixture_path))

    result = agent_stub.answer("Q1?")

    assert isinstance(result, AgentResult)
    assert result.question == "Q1?"
    assert result.answer == "A1."
    assert result.n_searches == 1
    assert result.queries == ["q"]
    assert result.tool_calls[0].latency_ms == 100


def test_answer_raises_for_unknown_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_path = _write_fixture(
        tmp_path,
        [{"question": "Known?", "answer": "Yes.", "tool_calls": []}],
    )
    monkeypatch.setenv("WIKI_QA_AGENT_STUB_FIXTURE", str(fixture_path))

    with pytest.raises(KeyError, match="no canned output"):
        agent_stub.answer("Unknown?")


def test_default_fixture_loads_dev_cases() -> None:
    """The shipped fixture has entries for all 3 dev placeholder cases."""
    questions = {
        "When was the Battle of Hastings?",
        "What is 17 multiplied by 23?",
        "When did Einstein win the Nobel Prize for relativity?",
    }
    for q in questions:
        result = agent_stub.answer(q)
        assert result.question == q
        assert result.answer
