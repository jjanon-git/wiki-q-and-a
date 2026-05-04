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
from pathlib import Path
from typing import Any

import anthropic

from wiki_qa.agent_contract import AgentResult, TokenUsage, ToolCall
from wiki_qa.formatting import format_error_for_model, format_results_for_model
from wiki_qa.parser import parse_evidence_and_answer
from wiki_qa.tools import SEARCH_WIKIPEDIA_TOOL
from wiki_qa.wikipedia import WikipediaSearchError, search_wikipedia

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
) -> AgentResult:
    """Answer a question by looping over search_wikipedia calls until the model returns text.

    `client` is dependency-injected so tests can pass a fake; production callers
    omit it and get a default Anthropic client (which reads `ANTHROPIC_API_KEY`
    from the environment). Typed as `Any` because the SDK's `messages.create`
    type signature is too strict for the dict-shaped messages we hand it
    (which are valid at the API level), and because the test fakes are
    duck-typed rather than subclasses of `anthropic.Anthropic`.
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

    for _ in range(max_iterations):
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
            text_blocks = [b.text for b in response.content if b.type == "text"]
            final_text = "\n".join(text_blocks)
            break

        # Append assistant turn (with tool_use) to messages so the next turn
        # can carry the tool_result responses.
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in tool_use_blocks:
            if block.name == "search_wikipedia":
                query = block.input.get("query", "")
                queries.append(query)
                start = time.perf_counter()
                try:
                    results = search_wikipedia(query)
                    raw_result_str = format_results_for_model(query, results)
                except WikipediaSearchError as e:
                    raw_result_str = format_error_for_model(query=query, reason=str(e))
                latency_ms = int((time.perf_counter() - start) * 1000)
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
