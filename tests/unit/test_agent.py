"""Tests for the agent loop.

The agent dispatches `search_wikipedia` calls in a loop against the
Anthropic API, threads tool_results back, parses the final assistant text
into evidence/answer, and returns an `AgentResult`.

Strategy: inject a FakeClient that scripts Anthropic API responses; mock
`search_wikipedia` itself via monkeypatch so we don't hit the real
MediaWiki API. Tests target the loop's behavior, not the search client
(which has its own suite).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from wiki_qa.agent import ProgressEvent, answer
from wiki_qa.agent_contract import ParseWarning
from wiki_qa.wikipedia import SearchResult, WikipediaSearchError

# ---------- Fakes for the Anthropic SDK ----------


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 20
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _FakeResponse:
    content: list[Any]
    stop_reason: str = "end_turn"
    usage: _FakeUsage = field(default_factory=_FakeUsage)


@dataclass
class _FakeMessages:
    responses: list[_FakeResponse]
    create_calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_calls.append(kwargs)
        if not self.responses:
            raise RuntimeError("FakeClient: no more scripted responses")
        return self.responses.pop(0)


class FakeClient:
    """Minimal stand-in for `anthropic.Anthropic`.

    Only `client.messages.create(...)` is exercised by the agent loop; that's
    the only entry point we need to script.
    """

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.messages = _FakeMessages(responses=responses)


def _text(text: str, stop_reason: str = "end_turn") -> _FakeResponse:
    return _FakeResponse(content=[_FakeTextBlock(text=text)], stop_reason=stop_reason)


def _tool_use(
    tool_id: str = "tool_1",
    name: str = "search_wikipedia",
    query: str = "Battle of Hastings",
    stop_reason: str = "tool_use",
    preceding_text: str | None = None,
) -> _FakeResponse:
    blocks: list[Any] = []
    if preceding_text:
        blocks.append(_FakeTextBlock(text=preceding_text))
    blocks.append(_FakeToolUseBlock(id=tool_id, name=name, input={"query": query}))
    return _FakeResponse(content=blocks, stop_reason=stop_reason)


# ---------- Fixture: monkeypatch search_wikipedia ----------


@pytest.fixture
def fake_search(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[..., list[SearchResult]]], None]:
    """Replace `wiki_qa.agent.search_wikipedia` with a caller-provided function.

    Returns a setter so each test installs its own fake search behavior.
    """

    def setter(impl: Callable[..., list[SearchResult]]) -> None:
        monkeypatch.setattr("wiki_qa.agent.search_wikipedia", impl)

    return setter


def _result(title: str = "Battle of Hastings") -> SearchResult:
    return SearchResult(
        title=title,
        url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        extract=f"Lead extract for {title}.",
        page_id=1,
        extract_truncated=False,
    )


# ---------- Tests ----------


class TestSingleTurnNoToolUse:
    def test_text_only_response_returns_parsed_answer(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])  # never called in this test
        client = FakeClient([_text("<evidence>e</evidence><answer>final</answer>")])
        result = answer("q", client=client)
        assert result.evidence == "e"
        assert result.answer == "final"
        assert result.n_searches == 0
        assert result.tool_calls == []
        assert result.queries == []
        assert result.stop_reason == "end_turn"

    def test_question_field_carried_through(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        result = answer("what year was Hastings?", client=client)
        assert result.question == "what year was Hastings?"


class TestSingleSearch:
    def test_tool_use_dispatches_search_then_returns_answer(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        captured_queries: list[str] = []

        def fake(query: str, **__: Any) -> list[SearchResult]:
            captured_queries.append(query)
            return [_result()]

        fake_search(fake)

        client = FakeClient(
            [
                _tool_use(query="Battle of Hastings"),
                _text("<evidence>e</evidence><answer>1066</answer>"),
            ]
        )
        result = answer("when was the battle of hastings?", client=client)

        assert captured_queries == ["Battle of Hastings"]
        assert result.n_searches == 1
        assert result.queries == ["Battle of Hastings"]
        assert result.answer == "1066"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search_wikipedia"
        assert result.tool_calls[0].query == "Battle of Hastings"
        assert "Battle of Hastings" in result.tool_calls[0].raw_result_str
        assert result.tool_calls[0].latency_ms >= 0

    def test_tool_result_xml_passed_back_to_model(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [_result(title="Niels Bohr")])
        client = FakeClient(
            [
                _tool_use(query="Bohr"),
                _text("<evidence>e</evidence><answer>a</answer>"),
            ]
        )
        answer("q", client=client)

        # The second create() call should have a tool_result message containing
        # XML built by format_results_for_model.
        second_call = client.messages.create_calls[1]
        last_user_message = second_call["messages"][-1]
        assert last_user_message["role"] == "user"
        tool_result_block = last_user_message["content"][0]
        assert tool_result_block["type"] == "tool_result"
        assert "<search_results" in tool_result_block["content"]
        assert "Niels Bohr" in tool_result_block["content"]


class TestMultiSearch:
    def test_three_search_turns_then_answer(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [_result()])
        client = FakeClient(
            [
                _tool_use(tool_id="t1", query="q1"),
                _tool_use(tool_id="t2", query="q2"),
                _tool_use(tool_id="t3", query="q3"),
                _text("<evidence>e</evidence><answer>final</answer>"),
            ]
        )
        result = answer("multi-part q", client=client)

        assert result.n_searches == 3
        assert result.queries == ["q1", "q2", "q3"]
        assert result.answer == "final"


class TestBudgetEnforcement:
    def test_max_iterations_caps_the_loop(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [_result()])
        # Model would search forever; we cap at 2
        client = FakeClient(
            [
                _tool_use(tool_id="t1", query="q1"),
                _tool_use(tool_id="t2", query="q2"),
                _tool_use(tool_id="t3", query="q3"),
                _tool_use(tool_id="t4", query="q4"),
                _tool_use(tool_id="t5", query="q5"),
            ]
        )
        result = answer("q", client=client, max_iterations=2)

        assert result.n_searches == 2
        assert result.stop_reason == "max_iterations"
        # No final answer was extracted — both blocks missing
        assert result.evidence == ""
        assert result.answer == ""


class TestSearchError:
    def test_wikipedia_error_surfaced_as_search_error_xml(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        def failing(*_: Any, **__: Any) -> list[SearchResult]:
            raise WikipediaSearchError("rate limit exceeded after 3 retries")

        fake_search(failing)

        client = FakeClient(
            [
                _tool_use(query="some query"),
                _text("<evidence>e</evidence><answer>recovered</answer>"),
            ]
        )
        result = answer("q", client=client)

        # Tool call recorded; raw_result_str carries the error envelope
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].raw_result_str.startswith("<search_error")
        assert "rate limit" in result.tool_calls[0].raw_result_str

        # The error envelope was passed back to the model as tool_result content
        second_call = client.messages.create_calls[1]
        tool_result_block = second_call["messages"][-1]["content"][0]
        assert "<search_error" in tool_result_block["content"]

        # Agent continued and produced an answer
        assert result.answer == "recovered"


class TestUsageAggregation:
    def test_token_usage_summed_across_turns(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [_result()])
        client = FakeClient(
            [
                _FakeResponse(
                    content=[
                        _FakeToolUseBlock(id="t1", name="search_wikipedia", input={"query": "x"})
                    ],
                    stop_reason="tool_use",
                    usage=_FakeUsage(input_tokens=100, output_tokens=50),
                ),
                _FakeResponse(
                    content=[_FakeTextBlock(text="<evidence>e</evidence><answer>a</answer>")],
                    stop_reason="end_turn",
                    usage=_FakeUsage(input_tokens=200, output_tokens=80),
                ),
            ]
        )
        result = answer("q", client=client)

        assert result.usage.input_tokens == 300
        assert result.usage.output_tokens == 130


class TestModelAndPromptWiring:
    def test_uses_env_var_model_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_search: Callable[[Callable[..., list[SearchResult]]], None],
    ) -> None:
        monkeypatch.setenv("WIKI_QA_AGENT_MODEL", "claude-test-model")
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client)

        assert client.messages.create_calls[0]["model"] == "claude-test-model"

    def test_default_model_is_opus_4_7(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_search: Callable[[Callable[..., list[SearchResult]]], None],
    ) -> None:
        monkeypatch.delenv("WIKI_QA_AGENT_MODEL", raising=False)
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client)

        assert client.messages.create_calls[0]["model"] == "claude-opus-4-7"

    def test_system_prompt_loaded_and_passed(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client)

        system = client.messages.create_calls[0]["system"]
        # Must include the load-bearing principles from system_v1.md
        assert "research assistant" in system.lower()
        assert "grounding" in system.lower() or "grounded" in system.lower()
        assert "evidence" in system.lower()

    def test_explicit_system_prompt_override(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client, system_prompt="custom prompt body")

        assert client.messages.create_calls[0]["system"] == "custom prompt body"

    def test_tool_definition_registered(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client)

        tools = client.messages.create_calls[0]["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "search_wikipedia"


# ---------- Parser-failure flow-through ----------


class TestParseFailuresFlowToAgentResult:
    """For each ParseWarning the parser can emit, verify it appears on
    AgentResult.parse_warnings when the model's output triggers it."""

    def test_clean_output_has_no_warnings(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        result = answer("q", client=client)
        assert result.parse_warnings == []

    def test_no_blocks_produces_both_missing_warnings(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("Sorry, I cannot help with that.")])
        result = answer("q", client=client)
        assert ParseWarning.MISSING_EVIDENCE_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_ANSWER_BLOCK in result.parse_warnings
        assert result.evidence == ""
        assert result.answer == ""

    def test_reversed_order_produces_warning_and_empty_fields(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<answer>a</answer><evidence>e</evidence>")])
        result = answer("q", client=client)
        assert ParseWarning.REVERSED_ORDER in result.parse_warnings
        assert result.evidence == ""
        assert result.answer == ""

    def test_multiple_evidence_blocks_propagates(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient(
            [_text("<evidence>first</evidence><evidence>second</evidence><answer>a</answer>")]
        )
        result = answer("q", client=client)
        assert ParseWarning.MULTIPLE_EVIDENCE_BLOCKS in result.parse_warnings
        assert result.evidence == "first"

    def test_unclosed_evidence_tag_propagates(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>oops never closes\n<answer>real answer</answer>")])
        result = answer("q", client=client)
        assert ParseWarning.UNCLOSED_EVIDENCE_TAG in result.parse_warnings
        assert result.answer == "real answer"

    def test_empty_evidence_block_propagates(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence></evidence><answer>a</answer>")])
        result = answer("q", client=client)
        assert ParseWarning.EMPTY_EVIDENCE_BLOCK in result.parse_warnings

    def test_max_iterations_path_produces_missing_blocks(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        # Hitting max_iterations means no final assistant text was captured;
        # parser should produce MISSING_* warnings on the empty string.
        fake_search(lambda *_, **__: [_result()])
        client = FakeClient(
            [
                _tool_use(tool_id="t1", query="q1"),
                _tool_use(tool_id="t2", query="q2"),
            ]
        )
        result = answer("q", client=client, max_iterations=1)
        assert result.stop_reason == "max_iterations"
        assert ParseWarning.MISSING_EVIDENCE_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_ANSWER_BLOCK in result.parse_warnings


# ---------- Progress event taxonomy ----------


class TestProgressEvents:
    """The progress callback is the agent's only way to surface live activity
    to the CLI. Order and field-population are observable contracts: the CLI
    relies on `query` being set on `search_start`, `latency_ms` and either
    `n_results` or `error` being set on `search_done`, and `stop_reason` on
    `complete`."""

    def _capture(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> tuple[list[ProgressEvent], FakeClient]:
        events: list[ProgressEvent] = []
        # Default fake_search; tests can override after this returns.
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        return events, client

    def test_no_progress_callback_is_silent(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        # Sanity: the agent must work the same way when no progress is passed.
        # Eval harness depends on this — it never passes progress.
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        result = answer("q", client=client)  # no progress arg
        assert result.answer == "a"

    def test_single_turn_emits_iteration_compose_complete(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        events: list[ProgressEvent] = []
        fake_search(lambda *_, **__: [])
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client, progress=events.append)

        kinds = [e.kind for e in events]
        # composing_answer fires before complete whenever the API returns
        # text-only — gives the CLI an explicit "no more searches, drafting"
        # marker for the silent stretch while the model generates final text.
        assert kinds == ["iteration_start", "composing_answer", "complete"]
        assert events[0].iteration == 1
        assert events[-1].stop_reason == "end_turn"

    def test_single_search_emits_full_sequence(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        events: list[ProgressEvent] = []
        fake_search(lambda *_, **__: [_result(), _result(title="Other"), _result(title="Third")])
        client = FakeClient(
            [
                _tool_use(query="Battle of Hastings"),
                _text("<evidence>e</evidence><answer>1066</answer>"),
            ]
        )
        answer("q", client=client, progress=events.append)

        kinds = [e.kind for e in events]
        assert kinds == [
            "iteration_start",
            "search_start",
            "search_done",
            "iteration_start",
            "composing_answer",
            "complete",
        ]
        # search_start carries the query
        assert events[1].query == "Battle of Hastings"
        # search_done carries n_results + latency, no error
        assert events[2].n_results == 3
        assert events[2].latency_ms is not None and events[2].latency_ms >= 0
        assert events[2].error is None
        # iterations are 1-indexed
        assert events[0].iteration == 1
        assert events[3].iteration == 2
        # composing_answer fires from the same turn as the text-only API call
        assert events[4].iteration == 2

    def test_composing_answer_does_not_fire_on_max_iterations_exit(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        # When the loop exits via the budget cap, the model never returned
        # text-only — there's no answer being composed. composing_answer must
        # NOT fire in that path, otherwise the CLI shows a misleading
        # "drafting" line for a question the agent failed to finish.
        fake_search(lambda *_, **__: [_result()])
        events: list[ProgressEvent] = []
        client = FakeClient(
            [
                _tool_use(tool_id="t1", query="q1"),
                _tool_use(tool_id="t2", query="q2"),
            ]
        )
        answer("q", client=client, max_iterations=1, progress=events.append)

        kinds = [e.kind for e in events]
        assert "composing_answer" not in kinds
        assert kinds[-1] == "complete"
        assert events[-1].stop_reason == "max_iterations"

    def test_search_error_populates_error_field(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        def failing(*_: Any, **__: Any) -> list[SearchResult]:
            raise WikipediaSearchError("rate limit exceeded")

        fake_search(failing)
        events: list[ProgressEvent] = []
        client = FakeClient(
            [
                _tool_use(query="x"),
                _text("<evidence>e</evidence><answer>recovered</answer>"),
            ]
        )
        answer("q", client=client, progress=events.append)

        done_events = [e for e in events if e.kind == "search_done"]
        assert len(done_events) == 1
        assert done_events[0].error is not None
        assert "rate limit" in done_events[0].error
        assert done_events[0].n_results is None

    def test_max_iterations_emits_complete_with_max_iterations_stop_reason(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [_result()])
        events: list[ProgressEvent] = []
        client = FakeClient(
            [
                _tool_use(tool_id="t1", query="q1"),
                _tool_use(tool_id="t2", query="q2"),
            ]
        )
        answer("q", client=client, max_iterations=1, progress=events.append)

        complete_events = [e for e in events if e.kind == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].stop_reason == "max_iterations"

    def test_max_iterations_field_is_propagated(
        self, fake_search: Callable[[Callable[..., list[SearchResult]]], None]
    ) -> None:
        fake_search(lambda *_, **__: [])
        events: list[ProgressEvent] = []
        client = FakeClient([_text("<evidence>e</evidence><answer>a</answer>")])
        answer("q", client=client, max_iterations=7, progress=events.append)

        # Every event should carry max_iterations=7
        assert all(e.max_iterations == 7 for e in events)
