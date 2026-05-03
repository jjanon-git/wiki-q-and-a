"""Frozen agent contract.

Imported by both the agent (`agent_stub.py` during dev, `agent.py` from
workstream A in production) and the eval harness. Do not change shape in
v1 — the harness, the stub, and the real agent all code against this.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    name: str
    query: str
    raw_result_str: str
    latency_ms: int


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


@dataclass(frozen=True)
class AgentResult:
    question: str
    answer: str
    tool_calls: list[ToolCall]
    n_searches: int
    queries: list[str]
    stop_reason: str
    usage: TokenUsage
    raw_messages: list[dict[str, Any]]
