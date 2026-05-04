# Plan: Eval harness

Scope: dataset, judge, runner, scoring output, calibration workflow. Not the agent itself, not the system prompt. Most of this is settled at the design level; the eval-set composition is still under discussion.

## Goals

The eval suite is the artifact that drives prompt-engineering iteration. It needs to:
1. Surface the system's failure modes clearly enough to act on.
2. Be cheap enough to re-run after every prompt change (dozens of cases, not hundreds).
3. Score per-dimension so that improvements and regressions are localized.
4. Be calibrated against human judgment (the user's, in absence of SMEs).

## Architecture

```
tests/eval/
  cases/                    # YAML dataset, possibly split per category
    factual.yaml
    multi_hop.yaml
    ...
  rubric.md                 # human-readable rubric per dimension
  judge_prompt.py           # builds the judge prompt
  runner.py                 # loads cases, runs agent, runs judge, writes results
  reports/                  # eval run outputs (gitignored, except headers)
src/wiki_qa/eval/
  schema.py                 # EvalCase, EvalResult, JudgeOutput dataclasses
  judge.py                  # judge invocation + parsing
  scoring.py                # per-dim aggregation, calibration metrics
```

CLI: `python -m wiki_qa.eval run [--cases <glob>] [--out <dir>]`
Calibration: `python -m wiki_qa.eval calibrate --sample N --in <run-dir>` → writes a markdown sheet for human scoring.

## Agent contract (what the harness calls)

Lives at `src/wiki_qa/agent_contract.py`. Imported by both the agent
(`agent_stub.py` during dev, `agent.py` from workstream A) and the eval
harness — single source of truth, neither side redefines.

Pydantic `BaseModel` with `model_config = ConfigDict(frozen=True, extra="forbid")`
(see CLAUDE.md "Shared types"). Validation at construction; JSON
round-trip via `model_dump_json()` / `model_validate_json()` for
`results.jsonl`.

```python
def answer(question: str, *, max_iterations: int = 5) -> AgentResult: ...

class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    query: str               # convenience: input["query"] for search_wikipedia
    raw_result_str: str      # exactly what we passed to the model as tool_result
                             # (XML for success, error envelope for failure)
    latency_ms: int

class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int

class AgentResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    question: str
    evidence: str            # parsed <evidence> block content (without surrounding tags)
    answer: str              # parsed <answer> block prose only — citations live here
    raw_output: str          # full raw model text before parsing; debugging + judge fallback
    tool_calls: list[ToolCall]
    n_searches: int          # convenience: len(tool_calls) of search_wikipedia
    queries: list[str]       # convenience: [tc.query for tc in tool_calls]
    stop_reason: str
    usage: TokenUsage
    raw_messages: list[dict] # full conversation, for debugging
```

Frozen across v1; the agent (workstream A) and the harness (workstream B)
both code against this.

The three-way split (`evidence` / `answer` / `raw_output`) reflects the
system prompt v1 output structure (`<evidence>` block followed by
`<answer>` block). Behavior checks operate on `answer` (prose only); the
judge gets `evidence` separately for groundedness scoring; `raw_output`
preserves the original for debugging or when parsing fails.

## Dataset format

```yaml
- id: factual_001
  category: simple_factual
  difficulty: easy             # easy | medium | hard
  question: "When was the Battle of Hastings?"
  expected_answer: "1066"
  expected_behavior:
    must_search: true
    must_not_search: false
    must_correct_premise: false
    must_refuse: false
  notes: "Baseline factual lookup. Single canonical answer."

- id: false_premise_001
  category: false_premise
  difficulty: medium
  question: "When did Einstein win the Nobel Prize for relativity?"
  expected_answer: "Einstein won the 1921 Nobel Prize for the photoelectric effect, not for relativity."
  expected_behavior:
    must_search: true
    must_not_search: false
    must_surface_premise_discrepancy: true
    must_refuse: false
  notes: "Tests ability to detect and surface a premise/Wikipedia discrepancy descriptively (e.g. 'Wikipedia indicates X; the question's premise was Y') without positioning the model as the arbiter. The model must NOT correct the user assertively ('you're wrong')."
```

YAML over JSON for the comment value.

`expected_behavior` flags split into two groups:
- **Deterministic** (`must_search`, `must_not_search`): checked by the harness against `AgentResult` and recorded in `behavior_checks`. See [Deterministic checks](#deterministic-checks-run-beforealongside-the-llm-judge).
- **Judge-context** (`must_surface_premise_discrepancy`, `must_refuse`): passed into the judge prompt as the expected behavior for the case so the judge has the right target. The judge does not emit per-flag pass/fail; it produces rubric scores informed by these flags. The flags are inputs to the judge, not outputs.

## Categories

The eval set is small (~35 cases) by design. The goal is to identify and stress-test specific failure modes deterministically. Each case is a hypothesis about a failure mode the system might exhibit; the category structure is our taxonomy of failure modes; difficulty levels tier cases by how clearly they should pass.

**Alternatives considered:**
- Stratified random sampling from a public dataset (HotpotQA, SimpleQA, TriviaQA): better statistical estimates of average performance, but the cases are mostly easy and don't stress the failure modes that drive prompt-engineering decisions.
- Adversarial generation (red-team Claude to produce hard cases): would catch failure modes we haven't thought of, but takes meaningful setup and yields cases we haven't validated.
- Hand-curated stress tests (chosen): signal density per case, time budget for a 1-2 hour build, full control over rubric coverage. Trade-off accepted: no statistical confidence, only pattern detection.

Categories below are starting points — expect to add and edit as new failure modes surface in eval runs.

| Category | What it tests | Count | Difficulty mix |
|---|---|---|---|
| `simple_factual` | baseline lookup | 4 | easy |
| `multi_hop` | sequential chain (A→B→C); decomposition, context-carrying, knowing-when-to-stop | 5-6 | medium / hard |
| `multi_source` | parallel info gathering from multiple articles (covers comparative + synthesis — same underlying behavior) | 3 | medium |
| `disambiguation_explicit` | search returns a Wikipedia disambiguation page; tests recognition + re-search. In single-turn mode the rubric reduces to "pick a sensible sense AND note alternatives exist" (clarifying-question option deferred with multi-turn). | 3 | medium |
| `buried_answer` | answer exists in the article but not in the lead extract (e.g., "What did Bezos originally want to call Amazon?" → "Cadabra", buried in History section); tests refinement behavior. **Diagnostic for tool design**: repeated failures here = signal to add `fetch_wikipedia_article` or drop `exintro=true`. See [Known v1 limitations](#known-v1-limitations). | 3 | hard |
| `negative_capability` | should NOT search Wikipedia: math, code, opinion (including subjective "best of" questions), personal advice. Tests that "search by default" doesn't go too far. | 4 | easy / medium |
| `false_premise` | factually wrong premise embedded in question; tests **detection + surfacing the discrepancy** (e.g., "Wikipedia indicates X; the question's premise was Y") rather than asserting the user is wrong. The model is not the arbiter — its job is to make the disagreement legible and let the user reconcile it. | 5 | medium / hard |
| `unanswerable_not_in_wp` | factually answerable in principle but not in Wikipedia; tests refusal-after-search vs guess | 3 | medium |
| `unanswerable_too_recent` | events past Wikipedia's freshness; tests cutoff acknowledgment | 2 | medium |
| `temporal` | recency-bounded; agent must caveat with last known state | 2 | medium |

Total: ~35 cases. Full run ≈17 min (agent + judge).

**Iteration signal vs alarm signal** (be honest about per-category power):

- **Iteration-signal categories (4+ cases)** — `simple_factual`, `multi_hop`, `negative_capability`, `false_premise`. Per-dim mean across these cases is meaningful for measuring iteration deltas.
- **Pattern-detection categories (3 cases)** — `multi_source`, `disambiguation_explicit`, `buried_answer`, `unanswerable_not_in_wp`. Useful for spotting trends but a single failure could be a fluke.
- **Alarm-only categories (2 cases)** — `unanswerable_too_recent`, `temporal`. A single failure means look closer; a pass is inconclusive.

**Dropped categories** (originally proposed, removed on review):
- `disambiguation_default_sense`: the "right" behavior is ambiguous — serving the most common sense without disclaimers is usually correct. Hard to grade.
- `unanswerable_subjective`: same right-behavior as `negative_capability` (identify as opinion, decline to commit). Folded in.

**Deferred categories** (placeholder in dataset structure, no v1 cases):
- `negative_policy` — Wikipedia could provide info but the system shouldn't (legal/medical/financial advice, harm-adjacent). Real product concern but out of scope for take-home; mention in writeup as future work.

**Architectural decision deferred**: multi-turn conversation. v1 is single-turn (matches the brief's "takes a question and returns an answer"). Multi-turn would enable clarifying-question disambiguation but materially complicates both agent loop and eval harness. Listed in writeup as a "how I'd extend this" item.

## Known v1 limitations

- **Buried-answer recovery**: under the single-tool design with `exintro=true`, the lead extract is the only article content the agent ever sees. Bumping `exchars` widens the lead view but never reaches into the article body. For `buried_answer` cases, recovery is solely via re-searching with more specific queries that surface a different article whose lead has the detail. If v1 evals show a meaningful failure rate on this category, the structural fixes are: (a) add `fetch_wikipedia_article(title)` as a second tool, or (b) drop `exintro=true` and return more of the article body. Calling this out so we don't mistake the 2000-char extract bump for a fix to this class of failure.
- **Per-category statistical confidence**: with most categories at 2-4 cases, we get pattern detection at best, not statistical significance. This is a deliberate trade-off for the take-home time budget. In production we'd want each category at 20+ cases to get confidence intervals on per-dim means.

## Rubric

Per-dimension 0-3. Judge must cite evidence in reasoning.

**Aggregation rules:**
- **No aggregation across dimensions within a single case.** Averaging factual_accuracy and citation_quality together is meaningless and obscures failures.
- **Per-dimension mean across cases per iteration is computed and reported.** This is how we measure iteration deltas — e.g., "v2 prompt lifted factual_accuracy from 2.1 → 2.6, but groundedness dropped from 2.5 → 1.8 in the same change." Per-dim means are also computed per category for the iteration-signal categories (4+ cases).

| Dimension | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| **factual_accuracy** | answer is wrong | partially correct, key facts wrong | mostly correct, minor inaccuracies | fully correct |
| **groundedness** | claims unsupported by retrieved content | mix of grounded and ungrounded claims | grounded with minor unsupported additions | fully grounded in retrieved content |
| **citation_quality** | no citations or wrong citations | citations exist but inconsistent or imprecise | citations present, mostly correct | citations precise, complete, correctly attributed |
| **search_efficiency** | excessive or irrelevant searches; or zero when needed | wasteful searching pattern | reasonable but some redundancy | minimal, well-targeted |
| **calibration** | confidently wrong; or refuses appropriately-answerable; or accepts false premise | calibration off in one direction | mostly calibrated, minor lapse | refuses when appropriate, corrects premises, expresses uncertainty when warranted |

Rubric lives in `tests/eval/rubric.md` so the judge prompt and humans see the same text.

## Judge prompt structure

Inputs (deliberately scoped — `raw_messages` is excluded to keep the judge prompt focused):
- Question
- Gold answer (`expected_answer` from the dataset)
- Model answer (the final assistant text)
- Tool-call trace: per-call `query`, `raw_result_str` (the actual content the agent saw — needed for groundedness), `latency_ms`
- Full rubric

`raw_messages` (system prompt + full conversation) stays on `AgentResult` for debugging but is NOT passed to the judge. Sending it would bloat the prompt with redundant content (system prompt is the same across cases; assistant text is already captured as `model_answer`).

Output (XML, reasoning before score per dimension):

```xml
<evaluation>
  <dimension name="factual_accuracy">
    <reasoning>
      The model claimed X. Wikipedia result 1 (cited URL Y) confirms X.
      No contradicting evidence in other results. Fully correct.
    </reasoning>
    <score>3</score>
  </dimension>
  <dimension name="groundedness">
    <reasoning>...</reasoning>
    <score>2</score>
  </dimension>
  <dimension name="citation_quality">...</dimension>
  <dimension name="search_efficiency">...</dimension>
  <dimension name="calibration">...</dimension>
</evaluation>
```

Reasoning before score is per Anthropic's guidance — separating reasoning from final output via structured tags improves calibration on judgment tasks.

Parser uses an XML library (stdlib `xml.etree.ElementTree`) with explicit handling for:
- Missing dimension in output → that dim's score = None, flag the case
- Score out of range → clamp + flag
- Malformed XML on first attempt → retry once with a "your previous output was not valid XML, please return only the `<evaluation>` block matching the schema" follow-up. On second malformed output, mark `judge_failure=true` for the case.

**Judge failures are excluded from per-dim means** (means are computed only over cases where the judge produced valid output). Per-dim judge_failure_rate is reported as its own metric, and judge_failure cases are surfaced in a separate section of the report so they can be inspected and re-run individually.

## Deterministic checks (run before/alongside the LLM judge)

Some failure modes don't need an LLM. These produce a `behavior_checks` block in the result, separate from judge-graded dimensions, so hard-fails aren't conflated with rubric scores.

**Agent-output checks:**

| Check | Source |
|---|---|
| `searched_when_required` | `n_searches > 0` if `expected_behavior.must_search` |
| `did_not_search_when_prohibited` | `n_searches == 0` if `expected_behavior.must_not_search` |
| `not_excessive_searches` | `n_searches <= 5` |
| `answer_length_plausible` | `1 < len(answer.split()) < 1000` |
| `has_bracket_citations` | answer contains at least one `[Article Title]` bracket reference. Regex: `\[[A-Z][^\]\n]{1,80}\]` matches at least once. NA when `n_searches == 0`. |
| `no_markdown_links` | answer does NOT contain any `[Title](URL)` markdown link syntax. The system prompt explicitly forbids embedding URLs inline. NA when `n_searches == 0`. Kept separate from `has_bracket_citations` because zero citations and forbidden markdown links are distinct failure modes with distinct fixes — collapsing them hides which one is firing. |
| `has_collated_sources` | answer ends with a "Sources:" or "References:" section followed by at least one `Title - URL` plain-text line (regex `^.+? - https?://\S+\s*$`). Reject if markdown link syntax appears inside the section. NA when `n_searches == 0`. |
| `output_has_required_blocks` | Fails when `MISSING_EVIDENCE_BLOCK` or `MISSING_ANSWER_BLOCK` fired. Model didn't emit the structure at all. Fix direction: prompt strengthening on output format. |
| `output_blocks_well_formed` | Fails when `UNCLOSED_EVIDENCE_TAG` or `UNCLOSED_ANSWER_TAG` fired. Model attempted the structure but emitted it malformed. Distinct from missing-block; fix direction is tokenization/length investigation or a concrete example. |
| `output_blocks_non_empty` | Fails when `EMPTY_EVIDENCE_BLOCK` or `EMPTY_ANSWER_BLOCK` fired. Structure clean, content missing. Fix direction: prompt requirement that each block carry content. |
| `output_blocks_canonical` | Fails when `REVERSED_ORDER`, `MULTIPLE_EVIDENCE_BLOCKS`, or `MULTIPLE_ANSWER_BLOCKS` fired. Structure present but emitted oddly (post-hoc reasoning, repeated blocks). Fix direction: emphasis on evidence-first single-block structure. |

The four parse-warning checks operate on `AgentResult.parse_warnings` (workstream A's `ParseWarning` StrEnum). All four always apply — the parser runs for every agent invocation. Each lists the specific codes from its cluster in `detail`. Split rather than collapsed into a single check so iteration data localizes which class of structural failure is firing — different clusters point to different prompt fixes.

**Judge prompt note**: when the judge integration lands, `parse_warnings` should be passed in as **informational context** (e.g., `<parse_warnings>missing_evidence_block, empty_answer_block</parse_warnings>`), with rubric guidance that the harness already records structural-failure signal deterministically — the judge should use the warnings to *interpret* the answer (e.g. "claim unsupported because evidence block was empty" reads differently from "claim unsupported because the model hallucinated"), not apply additional penalties.

**Judge-output checks** (sanity-checks on the judge itself, alongside the score parsing):

| Check | Source |
|---|---|
| `judge_cited_evidence` | each `<reasoning>` block > 50 chars AND references a result index, URL, or quoted phrase |
| `judge_score_in_range` | each `<score>` is integer 0-3 |
| `judge_all_dimensions_present` | output contains all 5 named dimensions |

Failed judge checks don't necessarily invalidate the case — a missing dimension means we drop that dim from this case's contribution to the per-dim mean, while keeping the rest.

## Calibration workflow

1. Run agent on full dataset → `eval_runs/<timestamp>/results.jsonl`.
2. Run judge on results → `eval_runs/<timestamp>/judge.jsonl`.
3. `python -m wiki_qa.eval calibrate --sample 8 --in <run-dir>` → samples cases stratified by:
   - At least one per dimension where judge scored ≤1
   - At least one per dimension where judge scored 3
   - Spread across categories
4. Writes two files to the run dir:
   - `calibration.md` — human-readable view of each sampled case: question, gold, model answer, tool trace summary, per-dim judge score + reasoning. This is what the human reads.
   - `calibration.scores.yaml` — input skeleton, one block per sampled case with `human_score:` (per dim) and `judge_missed:` (free text) fields. This is what the human edits.
5. Human reads the markdown, fills in the YAML.
6. `python -m wiki_qa.eval calibrate --analyze --in <run-dir>` reads the YAML, computes per-dim agreement (|human - judge| ≤ 1).
7. If a dimension shows >25% disagreement, treat as signal to revise rubric or judge prompt.

YAML chosen over markdown round-trip because parsing human-edited markdown (whitespace, partial fills, score formats) is fragile. YAML keeps the input strict and the read-only view separate.

## Iteration plan

1. Build v1 prompt + tool, run full eval, collect per-dim scores + judge reasoning + behavior checks.
2. Identify the lowest-scoring dimension(s) and the cases driving the score down.
3. Revise: prompt change, tool description change, return-shape change — whichever the failures point to.
4. Re-run, record per-dim deltas in DECISIONS.md.
5. Repeat at least once more.

If two iterations don't move a dimension, that's evidence the rubric or model itself is the bottleneck — switch axis.

## v1 operational settings (locked 2026-05-03 16:12)

These are the v1 defaults agreed during the kickoff round. Recorded here so the implementer doesn't have to re-derive them and so changes are visible as plan diffs.

- **Concurrency**: 3 in-flight cases for both the agent phase and the judge phase, using `concurrent.futures.ThreadPoolExecutor`. Conservative starting point — concern is MediaWiki rate limits and unknown Anthropic API limits on this account. If 3 looks fine in the first run, can bump up; if any race conditions or rate-limit issues surface, drop to serial. Result ordering on disk is deterministic regardless of completion order.
- **Agent stub for harness development**: `src/wiki_qa/agent_stub.py` defines `answer(question, *, max_iterations=5) -> AgentResult`, returning canned results loaded from `tests/eval/fixtures/agent_outputs.yaml` (one entry per placeholder case, looked up by question). The runner imports `from wiki_qa.agent_stub import answer`. **Swap-in when workstream A's real agent lands**: change that single import line to `from wiki_qa.agent import answer`. Nothing else in the harness changes — the `AgentResult` contract is frozen.
- **Placeholder cases for harness dev**: at least 3 cases spanning ≥3 categories with ≥3 distinct `expected_behavior` configurations. Concrete picks:
  - one `simple_factual` with `must_search=true` (exercises happy path + search-required deterministic check)
  - one `negative_capability` with `must_not_search=true` (exercises must-not-search deterministic check)
  - one `false_premise` with `must_correct_premise=true` (exercises judge-context flag, premise-correction rubric)
  This ensures the runner, deterministic `behavior_checks`, judge prompt, and judge-context flag plumbing are all exercised before the full ~35-case dataset lands. The full dataset is a separate pass.
- **Judge model**: `WIKI_QA_JUDGE_MODEL` env var, defaulting to `claude-opus-4-7`. Module-level constant in `src/wiki_qa/eval/judge.py` so downgrade experiments (Sonnet 4.6, Haiku 4.5) are a one-line / one-env-var flip.

## Subagent brief (deferred until rubric and dataset categories are locked)

When ready, the subagent gets:
- This plan (frozen at brief time)
- The agent contract (frozen)
- Wikipedia search plan (for context on what tool_call traces look like)
- Brief: build the dataset YAML files (~25 cases, distribution per the category table), write the judge prompt, scaffold the harness against the contract stub, return a runnable suite.

## Open items

- **Dataset composition** — categories above are proposed; user to confirm or revise.
- **System prompt direction (workstream A)** — once settled, finalize the citation pattern (inline title + collated URLs at end) and false-premise behavior, and ensure the rubric language is aligned with what the prompt actually asks the agent to do.
- **Judge model downgrade plan** — start with Opus 4.7; once we have ≥1 calibration round agreeing with the judge, retest with Sonnet 4.6 and Haiku 4.5 on the same cases and compare per-dim score deltas.
- **Whether to split YAML one-per-category or one big file** — leaning per-category for organization; one-file is simpler. Defer.

## What's deliberately NOT in v1

- Rerun-on-flake. v1 runs each case once. Add seeding/reruns if results are noisy.
- Cost tracking dashboards. Token usage is captured per-result; aggregation is a writeup-time concern.
- A judge-of-judges or judge ensemble. Single Opus 4.7 judge, calibrated by human spot-check.
- Statistical significance testing on per-iteration deltas. With n=25 cases, eyeballing per-dim score changes is fine for prompt iteration.
