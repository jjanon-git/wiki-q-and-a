"""Tests for deterministic behavior checks.

Each check returns pass / fail / na (not applicable). NA means "this check
doesn't apply to this case" — e.g., did_not_search_when_prohibited only
applies when expected_behavior.must_not_search is True. NA is reported,
not silently dropped, so the harness output is honest about coverage.

Citation checks (has_bracket_citations, no_markdown_links, has_collated_sources)
operate on result.answer (parsed prose only, NOT the <evidence>...</evidence>
envelope from raw_output). They reflect the system prompt v1 citation policy:
inline `[Article Title]` brackets only (no embedded URLs); plain-text
`Title - URL` Sources section at the end.
"""

from __future__ import annotations

from wiki_qa.agent_contract import AgentResult, ParseWarning, TokenUsage, ToolCall
from wiki_qa.eval.behavior_checks import (
    BehaviorChecks,
    CheckStatus,
    run_behavior_checks,
)
from wiki_qa.eval.schema import EvalCase, ExpectedBehavior


def _case(
    *,
    id: str = "test_001",
    category: str = "simple_factual",
    difficulty: str = "easy",
    question: str = "Q?",
    expected_answer: str = "A",
    must_search: bool = False,
    must_not_search: bool = False,
    must_surface_premise_discrepancy: bool = False,
    must_refuse: bool = False,
) -> EvalCase:
    return EvalCase(
        id=id,
        category=category,
        difficulty=difficulty,
        question=question,
        expected_answer=expected_answer,
        expected_behavior=ExpectedBehavior(
            must_search=must_search,
            must_not_search=must_not_search,
            must_surface_premise_discrepancy=must_surface_premise_discrepancy,
            must_refuse=must_refuse,
        ),
    )


def _result(
    *,
    answer: str = "Some answer.",
    evidence: str = "",
    raw_output: str | None = None,
    n_searches: int = 1,
    queries: list[str] | None = None,
    parse_warnings: list[ParseWarning] | None = None,
) -> AgentResult:
    qs = queries if queries is not None else (["q"] * n_searches)
    tool_calls = [
        ToolCall(
            name="search_wikipedia",
            query=q,
            raw_result_str="<search_results/>",
            latency_ms=10,
        )
        for q in qs
    ]
    full = (
        raw_output
        if raw_output is not None
        else f"<evidence>{evidence}</evidence>\n<answer>{answer}</answer>"
    )
    return AgentResult(
        question="Q?",
        evidence=evidence,
        answer=answer,
        raw_output=full,
        tool_calls=tool_calls,
        n_searches=n_searches,
        queries=qs,
        stop_reason="end_turn",
        usage=TokenUsage(
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0
        ),
        raw_messages=[],
        parse_warnings=parse_warnings or [],
    )


def _check(checks: BehaviorChecks, name: str) -> tuple[CheckStatus, str]:
    for c in checks.checks:
        if c.name == name:
            return c.status, c.detail
    raise AssertionError(f"check {name!r} not found in {[c.name for c in checks.checks]}")


# ---------- searched_when_required ----------


def test_searched_when_required_pass() -> None:
    case = _case(must_search=True)
    result = _result(n_searches=2)

    status, _ = _check(run_behavior_checks(case, result), "searched_when_required")

    assert status == "pass"


def test_searched_when_required_fail() -> None:
    case = _case(must_search=True)
    result = _result(n_searches=0)

    status, _ = _check(run_behavior_checks(case, result), "searched_when_required")

    assert status == "fail"


def test_searched_when_required_na_when_not_required() -> None:
    case = _case(must_search=False)
    result = _result(n_searches=0)

    status, _ = _check(run_behavior_checks(case, result), "searched_when_required")

    assert status == "na"


# ---------- did_not_search_when_prohibited ----------


def test_did_not_search_when_prohibited_pass() -> None:
    case = _case(must_not_search=True)
    result = _result(n_searches=0)

    status, _ = _check(run_behavior_checks(case, result), "did_not_search_when_prohibited")

    assert status == "pass"


def test_did_not_search_when_prohibited_fail() -> None:
    case = _case(must_not_search=True)
    result = _result(n_searches=2)

    status, _ = _check(run_behavior_checks(case, result), "did_not_search_when_prohibited")

    assert status == "fail"


def test_did_not_search_when_prohibited_na_when_not_prohibited() -> None:
    case = _case(must_not_search=False)
    result = _result(n_searches=2)

    status, _ = _check(run_behavior_checks(case, result), "did_not_search_when_prohibited")

    assert status == "na"


# ---------- not_excessive_searches ----------


def test_not_excessive_searches_pass_at_boundary() -> None:
    case = _case()
    result = _result(n_searches=5)

    status, _ = _check(run_behavior_checks(case, result), "not_excessive_searches")

    assert status == "pass"


def test_not_excessive_searches_fail_above_threshold() -> None:
    case = _case()
    result = _result(n_searches=6)

    status, _ = _check(run_behavior_checks(case, result), "not_excessive_searches")

    assert status == "fail"


# ---------- answer_length_plausible ----------


def test_answer_length_plausible_normal() -> None:
    case = _case()
    result = _result(answer="The Battle of Hastings was in 1066.")

    status, _ = _check(run_behavior_checks(case, result), "answer_length_plausible")

    assert status == "pass"


def test_answer_length_plausible_fail_empty() -> None:
    case = _case()
    result = _result(answer="")

    status, _ = _check(run_behavior_checks(case, result), "answer_length_plausible")

    assert status == "fail"


def test_answer_length_plausible_fail_single_word() -> None:
    case = _case()
    result = _result(answer="Yes")

    status, _ = _check(run_behavior_checks(case, result), "answer_length_plausible")

    assert status == "fail"


def test_answer_length_plausible_fail_too_long() -> None:
    case = _case()
    result = _result(answer=" ".join(["word"] * 1500))

    status, _ = _check(run_behavior_checks(case, result), "answer_length_plausible")

    assert status == "fail"


# ---------- has_bracket_citations ----------


def test_has_bracket_citations_pass_with_title_bracket() -> None:
    case = _case(must_search=True)
    result = _result(
        n_searches=1,
        answer=(
            "The Battle of Hastings was fought in 1066, per [Battle of Hastings].\n\n"
            "Sources:\nBattle of Hastings - https://en.wikipedia.org/wiki/Battle_of_Hastings\n"
        ),
    )

    status, _ = _check(run_behavior_checks(case, result), "has_bracket_citations")

    assert status == "pass"


def test_has_bracket_citations_fail_without_brackets() -> None:
    case = _case(must_search=True)
    result = _result(n_searches=1, answer="The Battle of Hastings was fought in 1066.")

    status, _ = _check(run_behavior_checks(case, result), "has_bracket_citations")

    assert status == "fail"


def test_has_bracket_citations_na_when_no_searches() -> None:
    case = _case(must_not_search=True)
    result = _result(n_searches=0, answer="17 * 23 = 391.")

    status, _ = _check(run_behavior_checks(case, result), "has_bracket_citations")

    assert status == "na"


# ---------- no_markdown_links ----------


def test_no_markdown_links_pass_when_brackets_only() -> None:
    case = _case(must_search=True)
    result = _result(
        n_searches=1,
        answer="See [Battle of Hastings] for details.\n\nSources:\nBattle of Hastings - https://x\n",
    )

    status, _ = _check(run_behavior_checks(case, result), "no_markdown_links")

    assert status == "pass"


def test_no_markdown_links_fail_when_markdown_link_present() -> None:
    case = _case(must_search=True)
    result = _result(
        n_searches=1,
        answer="See [Battle of Hastings](https://en.wikipedia.org/wiki/Battle_of_Hastings).",
    )

    status, detail = _check(run_behavior_checks(case, result), "no_markdown_links")

    assert status == "fail"
    assert "https://" in detail


def test_no_markdown_links_pass_with_plain_url_in_sources() -> None:
    case = _case(must_search=True)
    result = _result(
        n_searches=1,
        answer="Per [Foo].\n\nSources:\nFoo - https://en.wikipedia.org/wiki/Foo\n",
    )

    status, _ = _check(run_behavior_checks(case, result), "no_markdown_links")

    assert status == "pass"


def test_no_markdown_links_na_when_no_searches() -> None:
    case = _case(must_not_search=True)
    result = _result(n_searches=0, answer="Plain math.")

    status, _ = _check(run_behavior_checks(case, result), "no_markdown_links")

    assert status == "na"


# ---------- has_collated_sources ----------


def test_has_collated_sources_pass_with_title_url_format() -> None:
    case = _case(must_search=True)
    answer = (
        "The Battle of Hastings was fought in 1066 per [Battle of Hastings].\n\n"
        "Sources:\n"
        "Battle of Hastings - https://en.wikipedia.org/wiki/Battle_of_Hastings\n"
    )
    result = _result(n_searches=1, answer=answer)

    status, _ = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "pass"


def test_has_collated_sources_pass_with_references_label() -> None:
    case = _case(must_search=True)
    answer = "Some text per [Foo].\n\nReferences:\nFoo - https://en.wikipedia.org/wiki/Foo\n"
    result = _result(n_searches=1, answer=answer)

    status, _ = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "pass"


def test_has_collated_sources_fail_no_section() -> None:
    case = _case(must_search=True)
    result = _result(n_searches=1, answer="Plain answer per [Foo] with no sources section.")

    status, _ = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "fail"


def test_has_collated_sources_fail_label_but_wrong_format() -> None:
    case = _case(must_search=True)
    # bare URL, not Title - URL
    result = _result(
        n_searches=1,
        answer="Answer per [Foo].\n\nSources:\nhttps://en.wikipedia.org/wiki/Foo\n",
    )

    status, _ = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "fail"


def test_has_collated_sources_fail_when_markdown_link_in_section() -> None:
    case = _case(must_search=True)
    answer = "Answer per [Foo].\n\nSources:\n[Foo](https://en.wikipedia.org/wiki/Foo)\n"
    result = _result(n_searches=1, answer=answer)

    status, detail = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "fail"
    assert "markdown" in detail.lower()


def test_has_collated_sources_fail_label_but_no_url() -> None:
    case = _case(must_search=True)
    result = _result(n_searches=1, answer="Answer per [Foo].\n\nSources:\n(none)\n")

    status, _ = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "fail"


def test_has_collated_sources_na_when_no_searches() -> None:
    case = _case(must_not_search=True)
    result = _result(n_searches=0, answer="17 * 23 = 391.")

    status, _ = _check(run_behavior_checks(case, result), "has_collated_sources")

    assert status == "na"


# ---------- parse_warning cluster checks ----------
# Split into 4 cluster-based checks rather than one collapsed check, so
# iteration data localizes which class of structural failure is firing.
# Each cluster maps to a distinct prompt fix.


def test_output_has_required_blocks_pass_when_no_missing_codes() -> None:
    case = _case()
    result = _result(parse_warnings=[])

    status, _ = _check(run_behavior_checks(case, result), "output_has_required_blocks")
    assert status == "pass"


def test_output_has_required_blocks_fail_on_missing_evidence() -> None:
    case = _case()
    result = _result(parse_warnings=[ParseWarning.MISSING_EVIDENCE_BLOCK])

    status, detail = _check(run_behavior_checks(case, result), "output_has_required_blocks")
    assert status == "fail"
    assert "missing_evidence_block" in detail


def test_output_has_required_blocks_fail_on_missing_answer() -> None:
    case = _case()
    result = _result(parse_warnings=[ParseWarning.MISSING_ANSWER_BLOCK])

    status, _ = _check(run_behavior_checks(case, result), "output_has_required_blocks")
    assert status == "fail"


def test_output_has_required_blocks_ignores_other_clusters() -> None:
    """Empty-block warnings shouldn't fire the missing-block check."""
    case = _case()
    result = _result(parse_warnings=[ParseWarning.EMPTY_EVIDENCE_BLOCK])

    status, _ = _check(run_behavior_checks(case, result), "output_has_required_blocks")
    assert status == "pass"


def test_output_blocks_well_formed_pass_when_no_unclosed() -> None:
    case = _case()
    result = _result(parse_warnings=[])

    status, _ = _check(run_behavior_checks(case, result), "output_blocks_well_formed")
    assert status == "pass"


def test_output_blocks_well_formed_fail_on_unclosed_evidence() -> None:
    case = _case()
    result = _result(parse_warnings=[ParseWarning.UNCLOSED_EVIDENCE_TAG])

    status, detail = _check(run_behavior_checks(case, result), "output_blocks_well_formed")
    assert status == "fail"
    assert "unclosed_evidence_tag" in detail


def test_output_blocks_non_empty_pass_when_no_empty_codes() -> None:
    case = _case()
    result = _result(parse_warnings=[])

    status, _ = _check(run_behavior_checks(case, result), "output_blocks_non_empty")
    assert status == "pass"


def test_output_blocks_non_empty_fail_on_empty_answer() -> None:
    case = _case()
    result = _result(parse_warnings=[ParseWarning.EMPTY_ANSWER_BLOCK])

    status, detail = _check(run_behavior_checks(case, result), "output_blocks_non_empty")
    assert status == "fail"
    assert "empty_answer_block" in detail


def test_output_blocks_canonical_pass_when_no_noncanonical() -> None:
    case = _case()
    result = _result(parse_warnings=[])

    status, _ = _check(run_behavior_checks(case, result), "output_blocks_canonical")
    assert status == "pass"


def test_output_blocks_canonical_fail_on_reversed_order() -> None:
    case = _case()
    result = _result(parse_warnings=[ParseWarning.REVERSED_ORDER])

    status, detail = _check(run_behavior_checks(case, result), "output_blocks_canonical")
    assert status == "fail"
    assert "reversed_order" in detail


def test_output_blocks_canonical_fail_on_multiple_blocks() -> None:
    case = _case()
    result = _result(
        parse_warnings=[
            ParseWarning.MULTIPLE_EVIDENCE_BLOCKS,
            ParseWarning.MULTIPLE_ANSWER_BLOCKS,
        ]
    )

    status, detail = _check(run_behavior_checks(case, result), "output_blocks_canonical")
    assert status == "fail"
    assert "multiple_evidence_blocks" in detail
    assert "multiple_answer_blocks" in detail


def test_parse_warning_clusters_fire_independently() -> None:
    """A mix of warnings from two clusters fails exactly those two checks."""
    case = _case(must_search=True)
    result = _result(
        n_searches=1,
        answer="Per [Foo].\n\nSources:\nFoo - https://x\n",
        parse_warnings=[
            ParseWarning.MISSING_EVIDENCE_BLOCK,  # missing cluster
            ParseWarning.EMPTY_ANSWER_BLOCK,  # empty cluster
        ],
    )

    checks = run_behavior_checks(case, result)
    by_name = {c.name: c.status for c in checks.checks}

    assert by_name["output_has_required_blocks"] == "fail"
    assert by_name["output_blocks_non_empty"] == "fail"
    # Other two parse-warning clusters unaffected
    assert by_name["output_blocks_well_formed"] == "pass"
    assert by_name["output_blocks_canonical"] == "pass"


def test_parse_warning_checks_apply_even_when_no_searches() -> None:
    """Parser runs for every agent invocation regardless of search behavior."""
    case = _case(must_not_search=True)
    result = _result(
        n_searches=0,
        parse_warnings=[ParseWarning.EMPTY_ANSWER_BLOCK],
    )

    status, _ = _check(run_behavior_checks(case, result), "output_blocks_non_empty")
    assert status == "fail"


# ---------- aggregate ----------


def test_run_behavior_checks_returns_all_eleven_checks() -> None:
    case = _case(must_search=True)
    result = _result(
        n_searches=1,
        answer="Per [Foo].\n\nSources:\nFoo - https://x\n",
    )

    checks = run_behavior_checks(case, result)

    expected_names = {
        "searched_when_required",
        "did_not_search_when_prohibited",
        "not_excessive_searches",
        "answer_length_plausible",
        "has_bracket_citations",
        "no_markdown_links",
        "has_collated_sources",
        "output_has_required_blocks",
        "output_blocks_well_formed",
        "output_blocks_non_empty",
        "output_blocks_canonical",
    }
    assert {c.name for c in checks.checks} == expected_names
    assert checks.case_id == case.id


def test_run_behavior_checks_summary_counts_all_pass() -> None:
    case = _case(must_search=True)
    result = _result(
        n_searches=2,
        answer="Per [Foo] and [Bar].\n\nSources:\nFoo - https://x\nBar - https://y\n",
    )

    checks = run_behavior_checks(case, result)

    # must_not_search NA (not prohibited); other 10 (6 prior + 4 cluster) pass.
    assert checks.passed == 10
    assert checks.failed == 0
    assert checks.na == 1
