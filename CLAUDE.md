# wiki-q-and-a

Take-home assignment: build a Claude + Wikipedia QA system and evaluate it.
See `Prompt_Eng_Take-Home_Assignment.pdf` (in `~/Downloads`) for the brief.

## Collaboration conventions

- Skip preamble and acknowledgment phrases. Lead with the substantive point.
- Push back when something doesn't hold up. Don't agree by default.
- Plain prose over heavy formatting in conversation. Bullets and headers
  only when they earn their place.
- When a request is ambiguous, ask a clarifying question before acting.
- When a proposal has a defect, name it before proceeding rather than
  building on a flawed foundation.
- Don't praise decisions. Say when something works and when it doesn't,
  briefly, and move on.
- For long-running operations (eval runs, multi-step builds), give a brief
  "starting X, expect about N minutes" before kicking off, then progress
  at meaningful checkpoints, then results when done. Don't narrate every
  intermediate step.

## Tooling

- Python.
- `ruff` for formatting and linting.
- `mypy` in strict mode for types.
- `pytest` for tests.

## TDD workflow

Red, green, refactor. Write the failing test first, make it pass with the
simplest change, then refactor. Don't write implementation code without a
failing test for it.

Apply TDD strictly for system code (agent loop, tool integration, parsing).
For eval harness code where the test would essentially duplicate the harness
itself, exercise judgment — write the test first when it's clarifying, skip
it when the harness's outputs are the validation.

## Shared types

Types passed between modules (especially across workstream boundaries — agent
↔ eval harness, agent ↔ CLI, anything serialized to disk) use Pydantic
`BaseModel` with `model_config = ConfigDict(frozen=True, extra="forbid")`,
not `@dataclass(frozen=True)` or plain dicts.

Reasons:
- Validation at construction (rejects type mismatches and missing fields).
- `model_dump_json()` / `model_validate_json()` for clean JSON round-trip
  (results.jsonl, fixtures, calibration files).
- Schema introspection so cross-workstream coordination doesn't require
  reading source files.
- `extra="forbid"` makes contract drift loud — adding a field on one side
  without updating the contract surfaces immediately rather than silently
  dropping it.

Single source of truth: contracts live in their own module
(`src/wiki_qa/agent_contract.py` for the agent-eval boundary). Both sides
import; neither redefines.

## Quality gates

Run formatter, linter, type checker, and tests before declaring any code
change done. If a file doesn't pass, fix it before moving on. Do not
disable rules to make code pass; if a rule is wrong for the project,
raise it for discussion.

## DECISIONS.md

Maintain `DECISIONS.md` in the project root as an append-only chronological
log. Add a brief entry (2-4 sentences, timestamped) when:

- A design decision is made. Record alternatives considered (required when
  realistic alternatives exist), the choice, and the reasoning.
- An eval result reveals a failure mode, unexpected behavior, calibration
  mismatch, or contradicts a prior hypothesis. Record what was observed
  and what changed in response.
- A prompt or tool description is iterated. Record what changed, what
  motivated it, and the eval delta if measured.
- I push back, change my mind, or reject an approach. Write the entry
  before continuing — don't fold the change in silently.
- I explicitly mark something as worth capturing.
- A prior decision is reversed: append a new entry that explicitly
  supersedes the earlier one. Do not edit or remove past entries.

Format: each entry begins with `## YYYY-MM-DD HH:MM — short title`,
followed by 2-4 sentences. Append at the end of the file (chronological
forward) so the log reads as a story.

Capture observations and rationale, not value judgments. No praise or
blame; no "this is the better choice." Record A vs B, what was picked,
what was observed.

## Project constraints (from the brief)

- Use Anthropic API. No hosted search/RAG tools (no `web_search` tool type, etc.).
- Wikipedia source is our choice (live MediaWiki API is the default plan).
- Three deliverables: runnable prototype, GitHub repo, design rationale (video + written doc).
- Target 1-2 hours, hard limit 8 hours. Depth over breadth.

## Git

- Do not push to GitHub until I explicitly say so.
- Local commits are fine when they make sense.
