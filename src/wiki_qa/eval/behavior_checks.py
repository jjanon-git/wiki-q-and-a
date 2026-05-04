"""Deterministic behavior checks (no LLM).

Run alongside the LLM judge. Each check on an `(EvalCase, AgentResult)`
returns one of:
- "pass": check applies and the result satisfies it
- "fail": check applies and the result does not
- "na":   check does not apply (e.g., a search-related check on a case
         whose expected_behavior says the agent shouldn't search)

NA is reported, not silently dropped, so the eval output is honest about
which checks did and didn't run.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wiki_qa.agent_contract import AgentResult, ParseWarning
from wiki_qa.eval.schema import EvalCase

CheckStatus = Literal["pass", "fail", "na"]

MAX_SEARCHES = 5
MIN_ANSWER_WORDS = 2
MAX_ANSWER_WORDS = 1000

_BRACKET_CITATION_RE = re.compile(r"\[[A-Z][^\]\n]{1,80}\]")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+?\]\(\s*https?://[^)\s]+\s*\)")
_SOURCES_HEADER_RE = re.compile(r"(?im)^\s*(sources|references)\s*:\s*$")
_TITLE_URL_LINE_RE = re.compile(r"(?m)^\s*[^\n]+? - https?://\S+\s*$")

# Parse-warning clusters. Split by failure mode rather than collapsed into
# one check so iteration data localizes which class of structural failure
# is firing — different clusters point to different prompt fixes.
_MISSING_BLOCK_CODES: frozenset[ParseWarning] = frozenset(
    {ParseWarning.MISSING_EVIDENCE_BLOCK, ParseWarning.MISSING_ANSWER_BLOCK}
)
_UNCLOSED_TAG_CODES: frozenset[ParseWarning] = frozenset(
    {ParseWarning.UNCLOSED_EVIDENCE_TAG, ParseWarning.UNCLOSED_ANSWER_TAG}
)
_EMPTY_BLOCK_CODES: frozenset[ParseWarning] = frozenset(
    {ParseWarning.EMPTY_EVIDENCE_BLOCK, ParseWarning.EMPTY_ANSWER_BLOCK}
)
_NONCANONICAL_CODES: frozenset[ParseWarning] = frozenset(
    {
        ParseWarning.REVERSED_ORDER,
        ParseWarning.MULTIPLE_EVIDENCE_BLOCKS,
        ParseWarning.MULTIPLE_ANSWER_BLOCKS,
    }
)


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    status: CheckStatus
    detail: str


class BehaviorChecks(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    case_id: str
    checks: list[CheckResult]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def na(self) -> int:
        return sum(1 for c in self.checks if c.status == "na")


def run_behavior_checks(case: EvalCase, result: AgentResult) -> BehaviorChecks:
    return BehaviorChecks(
        case_id=case.id,
        checks=[
            _searched_when_required(case, result),
            _did_not_search_when_prohibited(case, result),
            _not_excessive_searches(result),
            _answer_length_plausible(result),
            _has_bracket_citations(result),
            _no_markdown_links(result),
            _has_collated_sources(result),
            _output_has_required_blocks(result),
            _output_blocks_well_formed(result),
            _output_blocks_non_empty(result),
            _output_blocks_canonical(result),
        ],
    )


def _searched_when_required(case: EvalCase, result: AgentResult) -> CheckResult:
    if not case.expected_behavior.must_search:
        return CheckResult(
            name="searched_when_required",
            status="na",
            detail="case does not require search",
        )
    if result.n_searches > 0:
        return CheckResult(
            name="searched_when_required",
            status="pass",
            detail=f"agent searched {result.n_searches} time(s)",
        )
    return CheckResult(
        name="searched_when_required",
        status="fail",
        detail="must_search=true but agent did not search",
    )


def _did_not_search_when_prohibited(case: EvalCase, result: AgentResult) -> CheckResult:
    if not case.expected_behavior.must_not_search:
        return CheckResult(
            name="did_not_search_when_prohibited",
            status="na",
            detail="case does not prohibit search",
        )
    if result.n_searches == 0:
        return CheckResult(
            name="did_not_search_when_prohibited",
            status="pass",
            detail="agent correctly did not search",
        )
    return CheckResult(
        name="did_not_search_when_prohibited",
        status="fail",
        detail=f"must_not_search=true but agent searched {result.n_searches} time(s)",
    )


def _not_excessive_searches(result: AgentResult) -> CheckResult:
    if result.n_searches <= MAX_SEARCHES:
        return CheckResult(
            name="not_excessive_searches",
            status="pass",
            detail=f"{result.n_searches} <= {MAX_SEARCHES}",
        )
    return CheckResult(
        name="not_excessive_searches",
        status="fail",
        detail=f"{result.n_searches} > {MAX_SEARCHES}",
    )


def _answer_length_plausible(result: AgentResult) -> CheckResult:
    word_count = len(result.answer.split())
    if MIN_ANSWER_WORDS <= word_count <= MAX_ANSWER_WORDS:
        return CheckResult(
            name="answer_length_plausible",
            status="pass",
            detail=f"{word_count} word(s)",
        )
    return CheckResult(
        name="answer_length_plausible",
        status="fail",
        detail=f"{word_count} word(s) outside [{MIN_ANSWER_WORDS}, {MAX_ANSWER_WORDS}]",
    )


def _has_bracket_citations(result: AgentResult) -> CheckResult:
    """Pass if the prose answer contains at least one [Article Title] bracket reference.

    The system prompt requires inline citations as title-only brackets (no
    embedded URL). This check looks at result.answer (parsed prose only,
    no <evidence> envelope).
    """
    if result.n_searches == 0:
        return CheckResult(
            name="has_bracket_citations",
            status="na",
            detail="no searches; nothing to cite",
        )
    if _BRACKET_CITATION_RE.search(result.answer):
        return CheckResult(
            name="has_bracket_citations",
            status="pass",
            detail="found at least one [Title] bracket reference in prose",
        )
    return CheckResult(
        name="has_bracket_citations",
        status="fail",
        detail="no [Title] bracket references found in prose",
    )


def _no_markdown_links(result: AgentResult) -> CheckResult:
    """Pass if the prose answer has no [Title](URL) markdown link syntax.

    The system prompt explicitly forbids markdown link syntax — URLs only
    belong in the plain-text Sources section. Separate from
    has_bracket_citations: zero citations and forbidden markdown links are
    distinct failure modes with distinct fixes (encourage citations vs.
    enforce format), so separating them keeps the iteration signal clean.
    """
    if result.n_searches == 0:
        return CheckResult(
            name="no_markdown_links",
            status="na",
            detail="no searches; format constraint not exercised",
        )
    match = _MARKDOWN_LINK_RE.search(result.answer)
    if match is None:
        return CheckResult(
            name="no_markdown_links",
            status="pass",
            detail="no [Title](URL) markdown link syntax in prose",
        )
    return CheckResult(
        name="no_markdown_links",
        status="fail",
        detail=f"forbidden markdown link found: {match.group(0)!r}",
    )


def _has_collated_sources(result: AgentResult) -> CheckResult:
    """Pass if the answer ends with a Sources/References section listing
    Title - URL plain-text lines (per system prompt v1).
    """
    if result.n_searches == 0:
        return CheckResult(
            name="has_collated_sources",
            status="na",
            detail="no searches; nothing to cite",
        )

    header_match = _SOURCES_HEADER_RE.search(result.answer)
    if not header_match:
        return CheckResult(
            name="has_collated_sources",
            status="fail",
            detail="no 'Sources:' or 'References:' section header found",
        )

    after_header = result.answer[header_match.end() :]
    if _MARKDOWN_LINK_RE.search(after_header):
        return CheckResult(
            name="has_collated_sources",
            status="fail",
            detail="markdown link syntax present in Sources section (must be plain Title - URL)",
        )
    if _TITLE_URL_LINE_RE.search(after_header):
        return CheckResult(
            name="has_collated_sources",
            status="pass",
            detail=(
                f"section '{header_match.group(1).lower()}:' followed by "
                "at least one 'Title - URL' line"
            ),
        )
    return CheckResult(
        name="has_collated_sources",
        status="fail",
        detail="section header present but no 'Title - URL' plain-text lines follow",
    )


def _parse_warning_cluster_check(
    *,
    name: str,
    cluster_label: str,
    fail_codes: frozenset[ParseWarning],
    result: AgentResult,
) -> CheckResult:
    fired = [code for code in result.parse_warnings if code in fail_codes]
    if not fired:
        return CheckResult(
            name=name,
            status="pass",
            detail=f"no {cluster_label} warnings",
        )
    codes = ", ".join(str(c) for c in fired)
    return CheckResult(
        name=name,
        status="fail",
        detail=f"{cluster_label} warning(s) fired: {codes}",
    )


def _output_has_required_blocks(result: AgentResult) -> CheckResult:
    """Fails when MISSING_EVIDENCE_BLOCK or MISSING_ANSWER_BLOCK fired.

    The model didn't emit the structure at all. Strongest signal that the
    output-format guidance in the system prompt isn't getting through.
    Fix direction: prompt strengthening on the output structure.
    """
    return _parse_warning_cluster_check(
        name="output_has_required_blocks",
        cluster_label="missing-block",
        fail_codes=_MISSING_BLOCK_CODES,
        result=result,
    )


def _output_blocks_well_formed(result: AgentResult) -> CheckResult:
    """Fails when UNCLOSED_EVIDENCE_TAG or UNCLOSED_ANSWER_TAG fired.

    The model attempted the structure but emitted it malformed (opened a
    tag, never closed it). Distinct from missing-block because the model
    tried — fix direction is tokenization/length investigation or a
    concrete example in the prompt, not a stronger structure requirement.
    """
    return _parse_warning_cluster_check(
        name="output_blocks_well_formed",
        cluster_label="unclosed-tag",
        fail_codes=_UNCLOSED_TAG_CODES,
        result=result,
    )


def _output_blocks_non_empty(result: AgentResult) -> CheckResult:
    """Fails when EMPTY_EVIDENCE_BLOCK or EMPTY_ANSWER_BLOCK fired.

    Structure clean, content missing. The model produced the tags but
    didn't populate them. Fix direction: prompt requirement that each
    block carry content (quoted passages in evidence; prose in answer).
    """
    return _parse_warning_cluster_check(
        name="output_blocks_non_empty",
        cluster_label="empty-block",
        fail_codes=_EMPTY_BLOCK_CODES,
        result=result,
    )


def _output_blocks_canonical(result: AgentResult) -> CheckResult:
    """Fails when REVERSED_ORDER or MULTIPLE_* fired.

    Structure present but emitted oddly: `<answer>` before `<evidence>`,
    or multiple instances of either block. Suggests post-hoc rationalization
    or per-claim block emission. Fix direction: emphasis on
    evidence-first reasoning and a single block per type.
    """
    return _parse_warning_cluster_check(
        name="output_blocks_canonical",
        cluster_label="non-canonical",
        fail_codes=_NONCANONICAL_CODES,
        result=result,
    )
