"""Agent loop: dispatches `search_wikipedia` calls against the Anthropic API.

Reads model id from `WIKI_QA_AGENT_MODEL` (default `claude-opus-4-7`), system
prompt from `prompts/system_v1.md` (the section after the `---` divider), and
caps the search loop at `max_iterations`.

Returns an `AgentResult` populated with parsed evidence/answer, the full
tool-call trace, token usage, stop reason, and any structural parse_warnings
surfaced by the response parser. Never raises on a Wikipedia search failure
— errors are wrapped in `<search_error>` and fed back to the model so it can
recover.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict

from wiki_qa.agent_contract import AgentResult, TokenUsage, ToolCall
from wiki_qa.formatting import format_error_for_model, format_results_for_model
from wiki_qa.parser import parse_evidence_and_answer
from wiki_qa.tools import SEARCH_WIKIPEDIA_TOOL
from wiki_qa.wikipedia import WikipediaSearchError, search_wikipedia


class ProgressEvent(BaseModel):
    """Out-of-band notification fired by the agent loop as it works.

    Surfaced via the `progress=` callback on `answer()` so callers (CLI,
    notebooks) can show a live activity trace without changing the
    `AgentResult` contract or relying on logging side effects. Eval
    harness ignores by passing `progress=None` (default).

    Field semantics:
    - `iteration`: 1-indexed position in the search loop. Always set.
    - Per-event optional fields: only the ones relevant to the event
      kind are populated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "iteration_start",
        "search_start",
        "search_done",
        "composing_answer",
        "complete",
    ]
    iteration: int
    max_iterations: int
    # search_start
    query: str | None = None
    # search_done
    n_results: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    # complete
    stop_reason: str | None = None


ProgressCallback = Callable[[ProgressEvent], None]

_DEFAULT_MODEL = "claude-opus-4-7"
AGENT_MODEL_ENV_VAR = "WIKI_QA_AGENT_MODEL"
_MAX_TOKENS = 4096
# Default points at the most recent validated prompt version. v1.1 is the
# current baseline — it dropped parse_warnings to 0/34 and lifted every
# rubric dimension over v1. v1.2 (in flight) overrides via the
# `system_prompt=` argument until validated.
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "system_v1_1.md"

logger = logging.getLogger(__name__)


def _load_system_prompt() -> str:
    """Read `prompts/system_v1.md` and return the body after the `---` divider.

    The file has a header explaining what it is, then `---`, then the actual
    system prompt. We send only the body to the API.
    """
    text = _SYSTEM_PROMPT_PATH.read_text()
    parts = text.split("\n---\n", 1)
    return (parts[1] if len(parts) == 2 else text).strip()


def answer(
    question: str,
    *,
    max_iterations: int = 5,
    system_prompt: str | None = None,
    client: Any = None,
    progress: ProgressCallback | None = None,
) -> AgentResult:
    """Answer a question by looping over search_wikipedia calls until the model returns text.

    `client` is dependency-injected so tests can pass a fake; production callers
    omit it and get a default Anthropic client (which reads `ANTHROPIC_API_KEY`
    from the environment). Typed as `Any` because the SDK's `messages.create`
    type signature is too strict for the dict-shaped messages we hand it
    (which are valid at the API level), and because the test fakes are
    duck-typed rather than subclasses of `anthropic.Anthropic`.

    `progress` is an optional callback fired at iteration boundaries and
    around tool calls so a CLI can show live activity. Out-of-band — does
    not affect `AgentResult`. Eval harness omits this kwarg.
    """
    if client is None:
        client = anthropic.Anthropic()
    if system_prompt is None:
        system_prompt = _load_system_prompt()

    model = os.environ.get(AGENT_MODEL_ENV_VAR, _DEFAULT_MODEL)

    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    raw_messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    tool_calls: list[ToolCall] = []
    queries: list[str] = []

    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0
    stop_reason = "unknown"
    final_text = ""

    def _emit(event: ProgressEvent) -> None:
        if progress is not None:
            progress(event)

    for iter_idx in range(max_iterations):
        iteration_num = iter_idx + 1  # 1-indexed for human-readable progress
        _emit(
            ProgressEvent(
                kind="iteration_start",
                iteration=iteration_num,
                max_iterations=max_iterations,
            )
        )
        # Type as Any: the SDK's response.content is a union of >12 block types,
        # only two of which we handle (text, tool_use). Mypy can't narrow on the
        # `.type` string check because narrowing across unions that wide isn't
        # supported. Duck-typing is the SDK's documented pattern; the unit tests
        # exercise both code paths against fakes.
        response: Any = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            tools=[SEARCH_WIKIPEDIA_TOOL],
            messages=messages,
        )

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens
        total_cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_cache_creation += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

        stop_reason = response.stop_reason

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Capture the assistant message regardless of whether tools were called
        assistant_content = _serialize_assistant_content(response.content)
        raw_messages.append({"role": "assistant", "content": assistant_content})

        if not tool_use_blocks:
            # The API returned text-only — Claude finalized the answer
            # without requesting another search. Surface this explicitly so
            # the user knows the silent stretch was answer-composition, not
            # the agent stalling between searches.
            _emit(
                ProgressEvent(
                    kind="composing_answer",
                    iteration=iteration_num,
                    max_iterations=max_iterations,
                )
            )
            text_blocks = [b.text for b in response.content if b.type == "text"]
            final_text = "\n".join(text_blocks)
            _emit(
                ProgressEvent(
                    kind="complete",
                    iteration=iteration_num,
                    max_iterations=max_iterations,
                    stop_reason=stop_reason,
                )
            )
            break

        # Append assistant turn (with tool_use) to messages so the next turn
        # can carry the tool_result responses.
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in tool_use_blocks:
            if block.name == "search_wikipedia":
                query = block.input.get("query", "")
                queries.append(query)
                _emit(
                    ProgressEvent(
                        kind="search_start",
                        iteration=iteration_num,
                        max_iterations=max_iterations,
                        query=query,
                    )
                )
                start = time.perf_counter()
                error_str: str | None = None
                n_results: int | None = None
                try:
                    results = search_wikipedia(query)
                    raw_result_str = format_results_for_model(query, results)
                    n_results = len(results)
                except WikipediaSearchError as e:
                    raw_result_str = format_error_for_model(query=query, reason=str(e))
                    error_str = str(e)
                latency_ms = int((time.perf_counter() - start) * 1000)
                _emit(
                    ProgressEvent(
                        kind="search_done",
                        iteration=iteration_num,
                        max_iterations=max_iterations,
                        query=query,
                        n_results=n_results,
                        latency_ms=latency_ms,
                        error=error_str,
                    )
                )
                tool_calls.append(
                    ToolCall(
                        name="search_wikipedia",
                        query=query,
                        raw_result_str=raw_result_str,
                        latency_ms=latency_ms,
                    )
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": raw_result_str,
                    }
                )

        user_turn = {"role": "user", "content": tool_results}
        messages.append(user_turn)
        raw_messages.append(user_turn)
    else:
        # Loop exhausted without a text-only turn breaking out
        stop_reason = "max_iterations"
        _emit(
            ProgressEvent(
                kind="complete",
                iteration=max_iterations,
                max_iterations=max_iterations,
                stop_reason=stop_reason,
            )
        )
        logger.warning(
            "agent hit max_iterations=%d for question=%r without producing a final answer",
            max_iterations,
            question,
        )

    parsed = parse_evidence_and_answer(final_text)

    if parsed.parse_warnings:
        logger.warning(
            "agent response had parse warnings for question=%r: %s",
            question,
            [w.value for w in parsed.parse_warnings],
        )

    return AgentResult(
        question=question,
        evidence=parsed.evidence,
        answer=parsed.answer,
        raw_output=parsed.raw_output,
        tool_calls=tool_calls,
        n_searches=len(tool_calls),
        queries=queries,
        stop_reason=stop_reason,
        usage=TokenUsage(
            input_tokens=total_input,
            output_tokens=total_output,
            cache_read_tokens=total_cache_read,
            cache_creation_tokens=total_cache_creation,
        ),
        raw_messages=raw_messages,
        parse_warnings=list(parsed.parse_warnings),
    )


def _serialize_assistant_content(content: list[Any]) -> list[dict[str, Any]]:
    """Convert anthropic SDK content blocks into JSON-serializable dicts.

    The SDK returns typed objects; the API expects dicts on subsequent turns
    when we echo the assistant's tool_use blocks back as part of the
    conversation history.
    """
    out: list[dict[str, Any]] = []
    for block in content:
        if block.type == "text":
            out.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return out
