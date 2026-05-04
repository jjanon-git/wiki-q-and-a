"""Frozen agent contract — shared library types.

Imported by both the agent (`agent_stub.py` during dev, `agent.py` from
workstream A in production) and the eval harness. Do not change shape in
v1 — the harness, the stub, and the real agent all code against this.

Pydantic over `@dataclass(frozen=True)` so we get:
- validation at construction (rejects type mismatches, missing fields)
- `model_dump_json()` / `model_validate_json()` for `results.jsonl` round-trip
- schema introspection for cross-workstream coordination
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParseWarning(StrEnum):
    """Categorical signals from the response parser.

    Emitted when the model's output deviates from the expected structure.
    Used by the eval as a deterministic correctness signal independent of
    the judge's rubric scoring. Round-trips through JSON as plain strings
    (StrEnum behavior).

    Within each block type (evidence/answer), the MISSING/UNCLOSED/EMPTY
    codes are mutually exclusive — a block has exactly one diagnostic state.
    REVERSED_ORDER and MULTIPLE_* are independent and can co-occur with the
    block-state codes.
    """

    REVERSED_ORDER = "reversed_order"
    """`<answer>` block appeared before `<evidence>` block. Suggests post-hoc
    rationalization rather than evidence-first reasoning. Parser refuses to
    extract under this condition."""

    MULTIPLE_EVIDENCE_BLOCKS = "multiple_evidence_blocks"
    """Model emitted more than one `<evidence>` block. Parser took the first.
    Count is recoverable from `raw_output`."""

    MULTIPLE_ANSWER_BLOCKS = "multiple_answer_blocks"
    """Model emitted more than one `<answer>` block. Parser took the first.
    Count is recoverable from `raw_output`."""

    MISSING_EVIDENCE_BLOCK = "missing_evidence_block"
    """No `<evidence>` opening tag present anywhere in the model's output.
    Strongest signal that the model ignored the output structure entirely
    for evidence."""

    MISSING_ANSWER_BLOCK = "missing_answer_block"
    """No `<answer>` opening tag present anywhere in the model's output."""

    UNCLOSED_EVIDENCE_TAG = "unclosed_evidence_tag"
    """`<evidence>` opening tag present but no matching `</evidence>` close.
    Distinct from MISSING_EVIDENCE_BLOCK — the model attempted the structure
    but emitted it malformed."""

    UNCLOSED_ANSWER_TAG = "unclosed_answer_tag"
    """`<answer>` opening tag present but no matching `</answer>` close."""

    EMPTY_EVIDENCE_BLOCK = "empty_evidence_block"
    """`<evidence>...</evidence>` matched but content is empty (or whitespace
    only) after stripping. Structure is clean; the model produced no
    grounding content."""

    EMPTY_ANSWER_BLOCK = "empty_answer_block"
    """`<answer>...</answer>` matched but content is empty (or whitespace
    only) after stripping."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCall(_Frozen):
    name: str
    query: str
    raw_result_str: str
    latency_ms: int


class TokenUsage(_Frozen):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class AgentResult(_Frozen):
    question: str
    evidence: str
    answer: str
    raw_output: str
    tool_calls: list[ToolCall]
    n_searches: int
    queries: list[str]
    stop_reason: str
    usage: TokenUsage
    raw_messages: list[dict[str, Any]]
    # Categorical signals surfaced by the response parser when the model's
    # output deviates from expected structure. Empty when output parsed
    # cleanly. Eval uses this as a deterministic structural-failure signal
    # independent of judge rubric scoring. Defaulted to [] so existing
    # constructors don't break; populate from `ParsedOutput.parse_warnings`.
    parse_warnings: list[ParseWarning] = Field(default_factory=list)
