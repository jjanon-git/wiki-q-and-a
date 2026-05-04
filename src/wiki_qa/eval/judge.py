"""LLM-as-judge: prompt builder, response parser, evaluation orchestrator.

Three layers, separable:

1. `build_judge_input(case, agent_result) -> JudgeInput` — pure. Picks
   the deliberate subset of fields we send to the judge. Excludes
   `raw_output`, `raw_messages`, and convenience fields (`queries`,
   `n_searches`, `stop_reason`, `usage`) — `raw_output` is the unparsed
   model text (we already have parsed `evidence` and `answer`),
   `raw_messages` is the full conversation including the system prompt,
   and the convenience fields are derivable from `tool_calls`.
2. `build_judge_prompt(judge_input) -> str` — pure. Formats the input
   into the prompt string. Includes `parse_warnings` as informational
   context with explicit "do not apply additional rubric penalties on
   this basis" guidance — the harness already records structural
   failures separately via `behavior_checks`.
3. `parse_judge_output(text) -> JudgeOutput` — pure. Parses the judge's
   XML response. Handles malformed XML, missing dimensions, out-of-range
   scores, and surrounding prose.

`evaluate(case, agent_result, *, llm_fn) -> JudgeOutput` orchestrates:
builds input → builds prompt → calls llm_fn → parses → on malformed,
retries once with explicit "return only the <evaluation> block" guidance
→ on second failure, marks `judge_failure=True`.

`llm_fn` is injected for tests; the default (`default_llm_fn`) lazily
imports the Anthropic SDK and respects `WIKI_QA_JUDGE_MODEL`. No real
API calls happen in unit tests.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from wiki_qa.agent_contract import AgentResult, ParseWarning, ToolCall
from wiki_qa.eval.schema import EvalCase, ExpectedBehavior

DIMENSION_NAMES: tuple[str, ...] = (
    "factual_accuracy",
    "groundedness",
    "citation_quality",
    "search_efficiency",
    "calibration",
)

DEFAULT_JUDGE_MODEL = "claude-opus-4-7"
JUDGE_MODEL_ENV_VAR = "WIKI_QA_JUDGE_MODEL"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_RUBRIC_PATH = _REPO_ROOT / "tests" / "eval" / "rubric.md"


class LLMFn(Protocol):
    def __call__(self, prompt: str, /) -> str: ...


# ---------- data types ----------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class JudgeInput(_Frozen):
    """Deliberate subset of (EvalCase + AgentResult) sent to the judge."""

    question: str
    expected_answer: str
    expected_behavior: ExpectedBehavior
    evidence: str
    answer: str
    parse_warnings: list[ParseWarning]
    tool_calls: list[ToolCall]


class DimensionScore(_Frozen):
    name: str
    score: int | None
    reasoning: str
    flags: list[str] = []


class JudgeOutput(_Frozen):
    dimensions: list[DimensionScore]
    raw_response: str
    judge_failure: bool
    retries: int


# ---------- step 1: build_judge_input ----------


def build_judge_input(case: EvalCase, agent_result: AgentResult) -> JudgeInput:
    return JudgeInput(
        question=case.question,
        expected_answer=case.expected_answer,
        expected_behavior=case.expected_behavior,
        evidence=agent_result.evidence,
        answer=agent_result.answer,
        parse_warnings=list(agent_result.parse_warnings),
        tool_calls=list(agent_result.tool_calls),
    )


# ---------- step 2: build_judge_prompt ----------


_PROMPT_TEMPLATE = """\
You are evaluating an AI assistant's answer to a question, against the
Wikipedia content the assistant retrieved. Score each of five dimensions
on a 0-3 integer scale using the rubric provided.

Important conventions:
- Score each dimension independently. Do not average across dimensions.
- For factual_accuracy, groundedness, and citation_quality, cite specific
  evidence from `<tool_calls>` or the model's `<evidence>` block.
- Reasoning before score, per dimension.
- Return only the `<evaluation>` block in the schema shown at the end.

<question>
{question}
</question>

<expected_answer>
{expected_answer}
</expected_answer>

<expected_behavior>
must_surface_premise_discrepancy: {must_surface_premise_discrepancy}
must_refuse: {must_refuse}
</expected_behavior>

These flags describe the expected behavior for this case. Use them as
context for scoring the calibration dimension. The flags are inputs;
the judge does not emit per-flag pass/fail.

<parse_warnings>
{parse_warnings}
</parse_warnings>

The `parse_warnings` list flags where the model's output structure
deviated from the `<evidence>` / `<answer>` spec. Use these to *interpret*
the answer (e.g. an unsupported claim alongside `empty_evidence_block`
reads as the model failing to populate evidence rather than asserting
something ungrounded; an unsupported claim with no warnings reads as
genuine ungroundedness). The harness records structural failures
separately via deterministic checks — do not apply additional rubric
penalties on this basis.

<model_evidence>
{evidence}
</model_evidence>

<model_answer>
{answer}
</model_answer>

<tool_calls>
{tool_calls}
</tool_calls>

<rubric>
{rubric}
</rubric>

Return your evaluation as a single `<evaluation>` block, exactly in this
schema, with no surrounding prose:

<evaluation>
<dimension name="factual_accuracy">
<reasoning>...</reasoning>
<score>0-3</score>
</dimension>
<dimension name="groundedness">
<reasoning>...</reasoning>
<score>0-3</score>
</dimension>
<dimension name="citation_quality">
<reasoning>...</reasoning>
<score>0-3</score>
</dimension>
<dimension name="search_efficiency">
<reasoning>...</reasoning>
<score>0-3</score>
</dimension>
<dimension name="calibration">
<reasoning>...</reasoning>
<score>0-3</score>
</dimension>
</evaluation>
"""


def build_judge_prompt(judge_input: JudgeInput) -> str:
    return _PROMPT_TEMPLATE.format(
        question=judge_input.question,
        expected_answer=judge_input.expected_answer,
        must_surface_premise_discrepancy=str(
            judge_input.expected_behavior.must_surface_premise_discrepancy
        ).lower(),
        must_refuse=str(judge_input.expected_behavior.must_refuse).lower(),
        parse_warnings=_format_parse_warnings(judge_input.parse_warnings),
        evidence=judge_input.evidence or "(empty)",
        answer=judge_input.answer or "(empty)",
        tool_calls=_format_tool_calls(judge_input.tool_calls),
        rubric=_load_rubric(),
    )


def _format_parse_warnings(warnings: list[ParseWarning]) -> str:
    if not warnings:
        return "none"
    return ", ".join(str(w) for w in warnings)


def _format_tool_calls(tool_calls: list[ToolCall]) -> str:
    if not tool_calls:
        return "(no searches performed)"
    parts = []
    for i, tc in enumerate(tool_calls, start=1):
        parts.append(
            f'<call index="{i}">\n'
            f"<query>{tc.query}</query>\n"
            f"<latency_ms>{tc.latency_ms}</latency_ms>\n"
            f"<result>\n{tc.raw_result_str}\n</result>\n"
            f"</call>"
        )
    return "\n".join(parts)


@cache
def _load_rubric() -> str:
    return _RUBRIC_PATH.read_text()


# ---------- step 3: parse_judge_output ----------

_EVAL_BLOCK_RE = re.compile(r"<evaluation>.*?</evaluation>", re.DOTALL)


def parse_judge_output(text: str) -> JudgeOutput:
    block_match = _EVAL_BLOCK_RE.search(text)
    if block_match is None:
        return _judge_failure_output(text)

    try:
        root = ET.fromstring(block_match.group(0))
    except ET.ParseError:
        return _judge_failure_output(text)

    found_dims: dict[str, DimensionScore] = {}
    for dim_el in root.findall("dimension"):
        name = dim_el.get("name", "")
        if name not in DIMENSION_NAMES:
            continue
        reasoning_el = dim_el.find("reasoning")
        score_el = dim_el.find("score")
        reasoning = (reasoning_el.text or "").strip() if reasoning_el is not None else ""
        score, flags = _parse_score(score_el.text if score_el is not None else None)
        found_dims[name] = DimensionScore(name=name, score=score, reasoning=reasoning, flags=flags)

    dimensions: list[DimensionScore] = []
    for name in DIMENSION_NAMES:
        if name in found_dims:
            dimensions.append(found_dims[name])
        else:
            dimensions.append(
                DimensionScore(name=name, score=None, reasoning="", flags=["missing"])
            )

    return JudgeOutput(
        dimensions=dimensions,
        raw_response=text,
        judge_failure=False,
        retries=0,
    )


def _parse_score(raw: str | None) -> tuple[int | None, list[str]]:
    if raw is None:
        return None, ["missing_score"]
    stripped = raw.strip()
    try:
        value = int(stripped)
    except ValueError:
        return None, ["invalid_score"]
    if 0 <= value <= 3:
        return value, []
    clamped = max(0, min(3, value))
    return clamped, [f"clamped_from_{value}"]


def _judge_failure_output(raw: str) -> JudgeOutput:
    return JudgeOutput(
        dimensions=[
            DimensionScore(name=n, score=None, reasoning="", flags=["missing"])
            for n in DIMENSION_NAMES
        ],
        raw_response=raw,
        judge_failure=True,
        retries=0,
    )


# ---------- step 4: evaluate (orchestration) ----------


_RETRY_GUIDANCE = (
    "\n\nYour previous output was not valid XML matching the schema. "
    "Please return only the `<evaluation>` block exactly in the schema shown, "
    "with no surrounding prose."
)


def evaluate(
    case: EvalCase,
    agent_result: AgentResult,
    *,
    llm_fn: LLMFn | None = None,
) -> JudgeOutput:
    fn: LLMFn = llm_fn if llm_fn is not None else default_llm_fn
    judge_input = build_judge_input(case, agent_result)
    prompt = build_judge_prompt(judge_input)

    first_response = fn(prompt)
    first_parsed = parse_judge_output(first_response)
    if not first_parsed.judge_failure:
        return first_parsed

    retry_prompt = prompt + _RETRY_GUIDANCE
    second_response = fn(retry_prompt)
    second_parsed = parse_judge_output(second_response)

    if not second_parsed.judge_failure:
        return second_parsed.model_copy(update={"retries": 1})

    return second_parsed.model_copy(update={"retries": 1, "judge_failure": True})


def default_llm_fn(prompt: str, /) -> str:
    """Real Anthropic API call. Lazy-imports anthropic so unit tests
    that inject `llm_fn` don't pull in the SDK at all."""
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get(JUDGE_MODEL_ENV_VAR, DEFAULT_JUDGE_MODEL)
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)
