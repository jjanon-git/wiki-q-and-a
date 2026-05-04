"""Tests for the LLM-as-judge module.

Three layers, tested separately:

1. `build_judge_input` (pure): selects the deliberate subset of fields
   from (case, agent_result) — no raw_output, no raw_messages, no
   convenience fields.
2. `build_judge_prompt` (pure): formats a JudgeInput into the prompt
   string. Includes parse_warnings as context with explicit "do not
   apply additional penalties" guidance.
3. `parse_judge_output` (pure): parses the judge's XML response into
   a JudgeOutput, with handling for missing dims, out-of-range scores,
   and malformed XML.
4. `evaluate` (orchestration): builds prompt, calls llm_fn, parses,
   retries once on malformed XML. llm_fn injected for tests — no real
   API calls in this file.
"""

from __future__ import annotations

from wiki_qa.agent_contract import AgentResult, ParseWarning, TokenUsage, ToolCall
from wiki_qa.eval.judge import (
    DIMENSION_NAMES,
    DimensionScore,
    JudgeInput,
    JudgeOutput,
    build_judge_input,
    build_judge_prompt,
    evaluate,
    parse_judge_output,
)
from wiki_qa.eval.schema import EvalCase, ExpectedBehavior

# ---------- fixtures ----------


def _case(
    *,
    id: str = "c1",
    question: str = "When was the Battle of Hastings?",
    expected_answer: str = "1066",
    must_search: bool = True,
    must_not_search: bool = False,
    must_surface_premise_discrepancy: bool = False,
    must_refuse: bool = False,
) -> EvalCase:
    return EvalCase(
        id=id,
        category="simple_factual",
        difficulty="easy",
        question=question,
        expected_answer=expected_answer,
        expected_behavior=ExpectedBehavior(
            must_search=must_search,
            must_not_search=must_not_search,
            must_surface_premise_discrepancy=must_surface_premise_discrepancy,
            must_refuse=must_refuse,
        ),
    )


def _agent_result(
    *,
    question: str = "When was the Battle of Hastings?",
    evidence: str = '[Battle of Hastings] "fought on 14 October 1066..."',
    answer: str = "1066, per [Battle of Hastings].\n\nSources:\nBattle of Hastings - https://x\n",
    raw_output: str = "<evidence>...</evidence><answer>...</answer>",
    parse_warnings: list[ParseWarning] | None = None,
    tool_calls: list[ToolCall] | None = None,
) -> AgentResult:
    tcs = (
        tool_calls
        if tool_calls is not None
        else [
            ToolCall(
                name="search_wikipedia",
                query="Battle of Hastings 1066",
                raw_result_str="<search_results count='1'><result><title>Battle of Hastings</title>"
                "<url>https://x</url><extract>...</extract></result></search_results>",
                latency_ms=240,
            )
        ]
    )
    return AgentResult(
        question=question,
        evidence=evidence,
        answer=answer,
        raw_output=raw_output,
        tool_calls=tcs,
        n_searches=len(tcs),
        queries=[tc.query for tc in tcs],
        stop_reason="end_turn",
        usage=TokenUsage(
            input_tokens=100, output_tokens=50, cache_read_tokens=0, cache_creation_tokens=0
        ),
        raw_messages=[{"role": "user", "content": "irrelevant noise"}],
        parse_warnings=parse_warnings or [],
    )


# Long fixture XML; in-test layout matters more than line length here.
# fmt: off
VALID_JUDGE_XML = (
    "<evaluation>\n"
    '<dimension name="factual_accuracy">\n'
    "<reasoning>The model claimed 1066, which the retrieved "
    "Battle of Hastings article confirms.</reasoning>\n"
    "<score>3</score>\n"
    "</dimension>\n"
    '<dimension name="groundedness">\n'
    "<reasoning>The cited extract supports the date claim; "
    "no ungrounded additions.</reasoning>\n"
    "<score>3</score>\n"
    "</dimension>\n"
    '<dimension name="citation_quality">\n'
    "<reasoning>Inline [Battle of Hastings] bracket reference, "
    "plain-text Sources line.</reasoning>\n"
    "<score>3</score>\n"
    "</dimension>\n"
    '<dimension name="search_efficiency">\n'
    "<reasoning>One targeted query; no waste.</reasoning>\n"
    "<score>3</score>\n"
    "</dimension>\n"
    '<dimension name="calibration">\n'
    "<reasoning>Direct factual answer with appropriate confidence.</reasoning>\n"
    "<score>3</score>\n"
    "</dimension>\n"
    "</evaluation>\n"
)
# fmt: on


# ---------- build_judge_input: deliberate subset ----------


def test_build_judge_input_includes_only_intended_fields() -> None:
    case = _case()
    result = _agent_result()

    judge_input = build_judge_input(case, result)

    assert isinstance(judge_input, JudgeInput)
    assert judge_input.question == case.question
    assert judge_input.expected_answer == case.expected_answer
    assert judge_input.expected_behavior == case.expected_behavior
    assert judge_input.evidence == result.evidence
    assert judge_input.answer == result.answer
    assert judge_input.tool_calls == result.tool_calls
    assert judge_input.parse_warnings == result.parse_warnings


def test_build_judge_input_excludes_raw_output_and_raw_messages() -> None:
    """raw_output and raw_messages must NOT appear on JudgeInput.

    Pydantic with extra='forbid' ensures these aren't smuggled in via
    accidental field additions either.
    """
    case = _case()
    result = _agent_result()

    judge_input = build_judge_input(case, result)
    fields = set(judge_input.__class__.model_fields.keys())

    assert "raw_output" not in fields
    assert "raw_messages" not in fields
    # Convenience fields that duplicate tool_calls also excluded
    assert "queries" not in fields
    assert "n_searches" not in fields
    assert "stop_reason" not in fields
    assert "usage" not in fields


def test_build_judge_input_propagates_parse_warnings() -> None:
    case = _case()
    result = _agent_result(parse_warnings=[ParseWarning.EMPTY_EVIDENCE_BLOCK])

    judge_input = build_judge_input(case, result)

    assert judge_input.parse_warnings == [ParseWarning.EMPTY_EVIDENCE_BLOCK]


# ---------- build_judge_prompt ----------


def test_judge_prompt_includes_question_gold_answer_evidence_and_answer() -> None:
    judge_input = build_judge_input(_case(), _agent_result())
    prompt = build_judge_prompt(judge_input)

    assert "When was the Battle of Hastings?" in prompt
    assert "1066" in prompt
    assert "Battle of Hastings" in prompt


def test_judge_prompt_includes_expected_behavior_flags() -> None:
    case = _case(must_surface_premise_discrepancy=True, must_refuse=False)
    judge_input = build_judge_input(case, _agent_result())

    prompt = build_judge_prompt(judge_input)

    assert "must_surface_premise_discrepancy" in prompt
    assert "must_refuse" in prompt


def test_judge_prompt_includes_parse_warnings_when_present() -> None:
    judge_input = build_judge_input(
        _case(),
        _agent_result(parse_warnings=[ParseWarning.EMPTY_EVIDENCE_BLOCK]),
    )

    prompt = build_judge_prompt(judge_input)

    assert "empty_evidence_block" in prompt


def test_judge_prompt_says_none_when_parse_warnings_empty() -> None:
    judge_input = build_judge_input(_case(), _agent_result(parse_warnings=[]))

    prompt = build_judge_prompt(judge_input)

    assert "none" in prompt.lower()


def test_judge_prompt_carries_do_not_double_penalize_guidance() -> None:
    """parse_warnings is informational context, not a scoring directive."""
    prompt = build_judge_prompt(build_judge_input(_case(), _agent_result()))

    assert "do not apply additional" in prompt.lower() or "not a scoring" in prompt.lower()


def test_judge_prompt_includes_tool_calls() -> None:
    judge_input = build_judge_input(_case(), _agent_result())

    prompt = build_judge_prompt(judge_input)

    assert "Battle of Hastings 1066" in prompt  # query
    assert "<tool_calls>" in prompt
    assert "240" in prompt  # latency


def test_judge_prompt_includes_full_rubric() -> None:
    prompt = build_judge_prompt(build_judge_input(_case(), _agent_result()))

    for dim in DIMENSION_NAMES:
        assert dim in prompt
    assert "0" in prompt and "3" in prompt


def test_judge_prompt_does_not_include_raw_output_or_raw_messages() -> None:
    result = _agent_result(raw_output="<evidence>SECRET RAW OUTPUT</evidence><answer>...</answer>")
    prompt = build_judge_prompt(build_judge_input(_case(), result))

    assert "SECRET RAW OUTPUT" not in prompt
    assert "irrelevant noise" not in prompt


# ---------- parse_judge_output: happy path ----------


def test_parse_judge_output_extracts_all_five_dimensions() -> None:
    output = parse_judge_output(VALID_JUDGE_XML)

    assert isinstance(output, JudgeOutput)
    assert output.judge_failure is False
    names = [d.name for d in output.dimensions]
    assert names == list(DIMENSION_NAMES)
    for d in output.dimensions:
        assert d.score == 3
        assert d.reasoning


def test_parse_judge_output_preserves_raw_response() -> None:
    output = parse_judge_output(VALID_JUDGE_XML)

    assert output.raw_response == VALID_JUDGE_XML


def test_parse_judge_output_extracts_distinct_per_dim_scores() -> None:
    xml = VALID_JUDGE_XML.replace("<score>3</score>", "<score>1</score>", 1)

    output = parse_judge_output(xml)

    assert output.dimensions[0].score == 1
    assert output.dimensions[1].score == 3


# ---------- parse_judge_output: defensive cases ----------


def test_parse_judge_output_clamps_score_above_range_and_flags() -> None:
    xml = VALID_JUDGE_XML.replace("<score>3</score>", "<score>5</score>")

    output = parse_judge_output(xml)

    for d in output.dimensions:
        assert d.score == 3
        assert "clamped" in " ".join(d.flags).lower()


def test_parse_judge_output_clamps_score_below_range_and_flags() -> None:
    xml = VALID_JUDGE_XML.replace("<score>3</score>", "<score>-1</score>", 1)

    output = parse_judge_output(xml)

    assert output.dimensions[0].score == 0
    assert any("clamped" in f.lower() for f in output.dimensions[0].flags)


def test_parse_judge_output_marks_missing_dimension_as_none() -> None:
    # drop the calibration dimension entirely
    xml = VALID_JUDGE_XML.split('<dimension name="calibration">')[0] + "</evaluation>\n"

    output = parse_judge_output(xml)

    by_name = {d.name: d for d in output.dimensions}
    assert by_name["calibration"].score is None
    assert any("missing" in f.lower() for f in by_name["calibration"].flags)
    # other dims still scored
    assert by_name["factual_accuracy"].score == 3


def test_parse_judge_output_marks_non_integer_score_as_none() -> None:
    xml = VALID_JUDGE_XML.replace("<score>3</score>", "<score>excellent</score>", 1)

    output = parse_judge_output(xml)

    assert output.dimensions[0].score is None
    assert any("invalid" in f.lower() or "missing" in f.lower() for f in output.dimensions[0].flags)


def test_parse_judge_output_returns_judge_failure_on_malformed_xml() -> None:
    output = parse_judge_output("This is not XML at all, just prose.")

    assert output.judge_failure is True
    # All dims marked None / missing
    for d in output.dimensions:
        assert d.score is None


def test_parse_judge_output_tolerates_surrounding_prose() -> None:
    """Models often add chatter before and after the structured block."""
    text = (
        "Sure! Here's my evaluation:\n\n"
        + VALID_JUDGE_XML
        + "\nLet me know if you'd like me to revise."
    )

    output = parse_judge_output(text)

    assert output.judge_failure is False
    assert all(d.score == 3 for d in output.dimensions)


# ---------- evaluate: retry on malformed ----------


def test_evaluate_succeeds_on_first_call_when_xml_valid() -> None:
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return VALID_JUDGE_XML

    output = evaluate(_case(), _agent_result(), llm_fn=fake_llm)

    assert len(calls) == 1
    assert output.judge_failure is False
    assert output.retries == 0


def test_evaluate_retries_once_on_malformed_xml() -> None:
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "this is not xml"
        return VALID_JUDGE_XML

    output = evaluate(_case(), _agent_result(), llm_fn=fake_llm)

    assert len(calls) == 2
    assert output.judge_failure is False
    assert output.retries == 1
    # Second call should mention the previous output was invalid
    second_prompt = calls[1].lower()
    assert "previous" in second_prompt or "valid xml" in second_prompt


def test_evaluate_marks_judge_failure_after_two_malformed_outputs() -> None:
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "still not xml"

    output = evaluate(_case(), _agent_result(), llm_fn=fake_llm)

    assert len(calls) == 2
    assert output.judge_failure is True
    assert output.retries == 1
    for d in output.dimensions:
        assert d.score is None


def test_evaluate_does_not_call_real_anthropic_api(
    monkeypatch: object,  # pyright: ignore[reportArgumentType]
) -> None:
    """Defensive: anthropic SDK should not be touched when llm_fn is provided."""
    import sys

    # If anthropic was imported during test, fail loud.
    # (The default_llm_fn imports it lazily; injecting llm_fn must skip it.)
    def fake_llm(prompt: str) -> str:
        return VALID_JUDGE_XML

    pre_modules = set(sys.modules)
    evaluate(_case(), _agent_result(), llm_fn=fake_llm)
    post_modules = set(sys.modules)
    new = post_modules - pre_modules
    assert "anthropic" not in new, f"anthropic imported during test: {new & {'anthropic'}}"


# ---------- DimensionScore validation ----------


def test_dimension_score_construction_validates() -> None:
    d = DimensionScore(name="factual_accuracy", score=2, reasoning="reasonable", flags=[])
    assert d.name == "factual_accuracy"
    assert d.score == 2
