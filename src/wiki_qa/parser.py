"""Parse the agent's final assistant text into evidence/answer/raw_output.

The system prompt instructs the model to emit:
    <evidence>...</evidence>
    <answer>...</answer>

Two distinct decisions about strictness — they pull in opposite directions
and need to be untangled:

  - **Tolerant on content**: extracts contain unescaped angle brackets in
    quotes, math, and code. Substring/regex extraction (not `xml.etree`) so
    those don't break parsing.

  - **Strict on order**: the prompt requires evidence first, then answer.
    Reversed order suggests the model wrote its conclusion first and
    back-filled evidence to match (post-hoc rationalization rather than
    grounding). We refuse to extract under reversed order and flag it as a
    parse warning — the eval should see this as a model failure, not as a
    successful answer with a quirk.

  - **Strict on multiplicity**: first-block-wins for repeated `<evidence>`
    or `<answer>` blocks, but the multiplicity is logged as a parse warning
    so it isn't silently dropped.

The function never raises. Malformed input produces empty fields with
warnings; `raw_output` always carries the full input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from wiki_qa.agent_contract import ParseWarning

_EVIDENCE_RE = re.compile(r"<evidence>(.*?)</evidence>", re.DOTALL)
_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)


@dataclass(frozen=True)
class ParsedOutput:
    """Result of parsing a model output. Local helper, not the agent contract.

    `parse_warnings` carries categorical signals about structural anomalies
    in the model's output. Same enum used on `AgentResult.parse_warnings`,
    so the agent loop can pass them straight through.
    """

    evidence: str
    answer: str
    raw_output: str
    parse_warnings: list[ParseWarning] = field(default_factory=list)


def parse_evidence_and_answer(text: str) -> ParsedOutput:
    """Extract <evidence> and <answer> block contents from model text.

    Surfaces a categorical warning for each structural anomaly. See
    `ParseWarning` for the full taxonomy. `raw_output` is always the input
    verbatim; a malformed parse never raises.
    """
    warnings: list[ParseWarning] = []

    evidence_matches = list(_EVIDENCE_RE.finditer(text))
    answer_matches = list(_ANSWER_RE.finditer(text))

    if len(evidence_matches) > 1:
        warnings.append(ParseWarning.MULTIPLE_EVIDENCE_BLOCKS)
    if len(answer_matches) > 1:
        warnings.append(ParseWarning.MULTIPLE_ANSWER_BLOCKS)

    evidence_match = evidence_matches[0] if evidence_matches else None
    answer_match = answer_matches[0] if answer_matches else None

    # Strict order: evidence must precede answer per the system prompt contract.
    # Reversed order suggests post-hoc rationalization — refuse to extract.
    # Both blocks exist in this branch, so the missing/unclosed/empty diagnostics
    # below don't apply; reversed-order is the load-bearing signal.
    if (
        evidence_match is not None
        and answer_match is not None
        and evidence_match.start() > answer_match.start()
    ):
        warnings.append(ParseWarning.REVERSED_ORDER)
        return ParsedOutput(evidence="", answer="", raw_output=text, parse_warnings=warnings)

    evidence = evidence_match.group(1).strip() if evidence_match else ""
    answer = answer_match.group(1).strip() if answer_match else ""

    # Per-block diagnostics. Within each block type these three states are
    # mutually exclusive: MISSING (no opening tag), UNCLOSED (opening tag but
    # no close), or EMPTY (matched but content stripped to empty). A clean
    # parse with non-empty content emits no per-block warning.
    if evidence_match is None:
        if "<evidence>" in text:
            warnings.append(ParseWarning.UNCLOSED_EVIDENCE_TAG)
        else:
            warnings.append(ParseWarning.MISSING_EVIDENCE_BLOCK)
    elif evidence == "":
        warnings.append(ParseWarning.EMPTY_EVIDENCE_BLOCK)

    if answer_match is None:
        if "<answer>" in text:
            warnings.append(ParseWarning.UNCLOSED_ANSWER_TAG)
        else:
            warnings.append(ParseWarning.MISSING_ANSWER_BLOCK)
    elif answer == "":
        warnings.append(ParseWarning.EMPTY_ANSWER_BLOCK)

    return ParsedOutput(
        evidence=evidence,
        answer=answer,
        raw_output=text,
        parse_warnings=warnings,
    )
