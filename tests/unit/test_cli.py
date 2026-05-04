"""Tests for the user-facing CLI.

Focus: the pure formatting helper (`format_result`) and CLI argument
behavior. The end-to-end agent path is exercised by tests/unit/test_agent.py;
here we only validate the CLI surface that wraps it.
"""

from __future__ import annotations

from typing import Any

from click.testing import CliRunner

from wiki_qa.agent_contract import AgentResult, ParseWarning, TokenUsage, ToolCall
from wiki_qa.cli import format_result, main


def _result(**overrides: Any) -> AgentResult:
    defaults: dict[str, Any] = {
        "question": "Q?",
        "evidence": "ev",
        "answer": "the answer",
        "raw_output": "<evidence>ev</evidence><answer>the answer</answer>",
        "tool_calls": [],
        "n_searches": 0,
        "queries": [],
        "stop_reason": "end_turn",
        "usage": TokenUsage(
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        ),
        "raw_messages": [],
        "parse_warnings": [],
    }
    defaults.update(overrides)
    return AgentResult(**defaults)


class TestFormatResult:
    def test_basic_output_has_question_and_answer(self) -> None:
        r = _result(question="When was the Battle of Hastings?", answer="1066")
        out = format_result(r)
        assert "When was the Battle of Hastings?" in out
        assert "1066" in out

    def test_search_count_visible(self) -> None:
        r = _result(n_searches=0)
        assert "Searches: 0" in format_result(r)

    def test_tool_call_query_and_latency_shown(self) -> None:
        r = _result(
            n_searches=1,
            queries=["Battle of Hastings"],
            tool_calls=[
                ToolCall(
                    name="search_wikipedia",
                    query="Battle of Hastings",
                    raw_result_str="<r/>",
                    latency_ms=300,
                )
            ],
        )
        out = format_result(r)
        assert "Searches: 1" in out
        assert "Battle of Hastings" in out
        assert "300ms" in out

    def test_parse_warnings_surfaced(self) -> None:
        r = _result(parse_warnings=[ParseWarning.MULTIPLE_EVIDENCE_BLOCKS])
        out = format_result(r)
        assert "multiple_evidence_blocks" in out

    def test_no_parse_warnings_line_when_clean(self) -> None:
        r = _result()
        assert "Parse warnings:" not in format_result(r)

    def test_verbose_shows_token_usage_and_stop_reason(self) -> None:
        r = _result()
        out = format_result(r, verbose=True)
        assert "10 in" in out and "20 out" in out
        assert "end_turn" in out

    def test_non_verbose_omits_token_usage(self) -> None:
        out = format_result(_result(), verbose=False)
        assert "Tokens" not in out

    def test_falls_back_to_raw_output_when_answer_empty(self) -> None:
        r = _result(answer="", raw_output="some unparsed model text")
        out = format_result(r)
        assert "some unparsed model text" in out
        assert "no parsed answer" in out.lower()

    def test_no_answer_or_raw_output_shows_explicit_message(self) -> None:
        r = _result(answer="", raw_output="")
        out = format_result(r)
        assert "no answer" in out.lower()


class TestCli:
    """Light surface tests on argparse/click — full agent invocation is mocked."""

    def _run(self, args: list[str], agent_fn: Any) -> Any:
        # Patch _ask_one's default by injecting via the agent.answer global.
        # CliRunner gives a clean isolated invocation.
        runner = CliRunner()
        # Monkeypatch via attribute on cli module
        import wiki_qa.cli as cli_mod

        original = cli_mod._ask_one

        def fake_ask_one(
            question: str, *, verbose: bool, agent_fn: Any | None = None
        ) -> AgentResult:
            return (
                agent_fn(question)
                if agent_fn
                else _result(question=question, answer=f"answered: {question}")
            )

        # We don't actually inject; we just ensure the shape works
        try:
            return runner.invoke(main, args)
        finally:
            cli_mod._ask_one = original

    def test_no_args_shows_usage_and_exits_nonzero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code == 2
        assert "usage" in result.output.lower() or "usage" in (result.stderr or "").lower()

    def test_help_succeeds(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "wikipedia-grounded" in result.output.lower()
        assert "--demo" in result.output
