# wiki-q-and-a

A Wikipedia-grounded QA system built on Claude, with an eval harness measuring
5 rubric dimensions and 11 deterministic behavior checks. Submitted as an
Anthropic take-home; see [WRITEUP.md](WRITEUP.md) for the design rationale.

## Constraints satisfied

- **Anthropic API only.** Default model is Claude Opus 4.7
  (`claude-opus-4-7`), overridable via `WIKI_QA_AGENT_MODEL` /
  `WIKI_QA_JUDGE_MODEL` env vars for downgrade experiments.
- **No hosted search/RAG tools.** `search_wikipedia` is implemented from
  scratch against the MediaWiki action API; see
  [`src/wiki_qa/wikipedia.py`](src/wiki_qa/wikipedia.py).
- **Wikipedia source is live MediaWiki.** Single round-trip per call
  (search + lead-section extracts in one query via the `generator=search` +
  `prop=extracts` pattern).

## What's here

- A search-by-default agent powered by Claude Opus 4.7, calling a single
  `search_wikipedia(query)` tool against the live MediaWiki API.
- A 5-dimension LLM-as-judge with deterministic structural checks
  (`behavior_checks`) running alongside.
- A YAML dataset of hand-curated stress tests across 10 failure-mode
  categories.
- A decision log ([DECISIONS.md](DECISIONS.md)) that captures the
  prompt-engineering and eval-design reasoning chronologically. The writeup
  is reconstructed from this rather than from memory.

## Quick start

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install project deps
uv sync

# 3. Set your API key
cp .env.example .env
# edit .env, paste your ANTHROPIC_API_KEY

# 4. Run the v1 eval suite (34 cases, full judge, ~5 min at concurrency=3)
uv run python -m wiki_qa.eval run --cases tests/eval/cases/v1.yaml

# Or skip the LLM judge and run only the deterministic behavior checks:
uv run python -m wiki_qa.eval run --cases tests/eval/cases/v1.yaml --no-judge
```

Results land in `eval_runs/<UTC-timestamp>/results.jsonl` (one JSON line per
case). Each line carries the agent's parsed `evidence` / `answer`, the full
tool-call trace, the deterministic `behavior_checks` block, and the judge's
per-dimension scores.

**Judge calibration**: validate the LLM judge against your own reading.

```bash
# 1. Sample 8 cases stratified across rubric dimensions and score buckets.
#    Writes calibration.md (read-only context) + calibration.scores.yaml
#    (you fill in your scores 0-3 per dimension).
uv run python -m wiki_qa.eval calibrate sample \
  --in eval_runs/<run-dir> --cases tests/eval/cases/v1.yaml --n 8

# 2. Read calibration.md, fill in the `human` blocks of the YAML.

# 3. Compute per-dimension human/judge agreement.
#    Agreement = |human - judge| <= 1.
#    Flags any dimension with >25% disagreement as a calibration concern.
uv run python -m wiki_qa.eval calibrate analyze --in eval_runs/<run-dir>
```

To ask the system a single question:

```bash
# Single question
uv run python -m wiki_qa "When was the Battle of Hastings?"

# Verbose mode (also shows token usage and stop reason)
uv run python -m wiki_qa -v "Which is taller, K2 or Kangchenjunga?"

# Demo mode runs four sample questions across representative
# categories (simple_factual, multi_source, false_premise,
# negative_capability). ~30-60s total.
uv run python -m wiki_qa --demo
```

Output for each question shows the parsed answer (with inline
`[Article Title]` citations and a plain-text `Sources:` section if
applicable), the count and queries of any searches made with their
latencies, and any `parse_warnings` the response parser emitted (none
on a clean run).

## Repo layout

```
prompts/                          system prompt across iterations
  system_v1.md                    baseline
  system_v1_1.md                  in production
  system_v1_2.md                  preserved for iteration history
plans/                            design docs (search, eval harness)
DECISIONS.md                      append-only chronological decision log
WRITEUP.md                        consolidated design rationale
transcripts/                      Claude Code transcripts from development
eval_runs/                        per-iteration results.jsonl + calibration artifacts

src/wiki_qa/
  agent_contract.py               frozen Pydantic shared types (ToolCall,
                                  TokenUsage, AgentResult, ParseWarning)
  agent.py                        Claude tool-use loop
  agent_stub.py                   fixture-driven stub for harness dev
  wikipedia.py                    MediaWiki client (with retries)
  formatting.py                   tool-result XML formatter
  parser.py                       <evidence>/<answer> block extraction
  tools.py                        the tool definition (a prompt itself)
  eval/
    schema.py                     EvalCase, ExpectedBehavior
    dataset.py                    YAML loader with validation
    behavior_checks.py            11 deterministic checks
    judge.py                      JudgeInput / build_prompt / parse / evaluate
    runner.py                     concurrency, error isolation, sorted output
    results.py                    EvalResult (one line per case in jsonl)
    __main__.py                   CLI: `python -m wiki_qa.eval run`

tests/
  eval/
    rubric.md                     5-dim rubric (judge prompt + humans share)
    iterations.md                 per-iteration scoreboard with deltas
    cases/                        YAML dataset, split per category
    fixtures/                     canned agent outputs for the stub
  unit/                           ~150 unit tests; no real API calls
  integration/                    live MediaWiki tests (skipped by default)
```

## How the eval works

Each case in `tests/eval/cases/*.yaml` declares a question, an expected answer,
a category (`simple_factual`, `multi_hop`, `false_premise`,
`negative_capability`, `buried_answer`, `disambiguation_explicit`,
`unanswerable_*`, `temporal`, `multi_source`), and `expected_behavior` flags
(some deterministic, some judge-context).

The runner:

1. Loads cases, sorts by id.
2. For each case (concurrency 3 by default), invokes the agent and runs
   **11 deterministic behavior checks**: search expectations
   (`searched_when_required`, `did_not_search_when_prohibited`,
   `not_excessive_searches`), answer length, citation conventions
   (`has_bracket_citations` and `no_markdown_links` kept separate so
   iteration data localizes the failure mode), Title-URL Sources format,
   and four parse-warning cluster checks consuming the
   `ParseWarning` enum (missing / unclosed / empty / non-canonical
   structural anomalies).
3. Then invokes the **LLM judge** (Opus 4.7 by default) which scores
   five dimensions 0-3:
   - `factual_accuracy`
   - `groundedness`
   - `citation_quality`
   - `search_efficiency`
   - `calibration`

   The judge sees a deliberate subset of `AgentResult`: the question, gold
   answer, expected_behavior flags, parsed evidence/answer, parse_warnings
   (as informational context, see [`tests/eval/rubric.md`](tests/eval/rubric.md)
   and [`src/wiki_qa/eval/judge.py`](src/wiki_qa/eval/judge.py)), and the
   full `tool_calls` trace. `raw_output` and `raw_messages` are excluded.

4. Per-case error isolation: a blow-up at case 20 cannot abort the run.
   Results are sorted by `case_id` before write, so concurrency=1 and
   concurrency=3 produce byte-identical `results.jsonl`.

## Design rationale

- **System prompt** (`prompts/system_v1*.md`) - v1.1 in production; v1 and
  v1.2 preserved for iteration history. See
  [`tests/eval/iterations.md`](tests/eval/iterations.md) for per-version
  per-dimension deltas.
- **[`plans/`](plans/)** - design docs, written before implementation.
  - [`plans/search_wikipedia.md`](plans/search_wikipedia.md): tool definition,
    return shape, edge-case handling, MediaWiki strategy.
  - [`plans/eval_harness.md`](plans/eval_harness.md): dataset shape, rubric,
    judge prompt structure, deterministic checks, calibration workflow.
- **[`DECISIONS.md`](DECISIONS.md)** - append-only chronological log: every
  prompt change, eval observation, design pivot, and reversed call. Each entry
  notes alternatives considered, the choice, and the reasoning. The formal
  writeup is reconstructed from this log.
- **[`WRITEUP.md`](WRITEUP.md)** - consolidated design rationale organized
  thematically. The doc to read first if you want the design choices and
  reasoning without the chronology.
- **[Claude Code transcripts](https://htmlpreview.github.io/?https://github.com/jjanon-git/wiki-q-and-a/blob/main/transcripts/redacted/combined-html/index.html)** -
  three development sessions concatenated chronologically (105 prompts across
  21 pages). Redacted for local paths and email addresses before submission.
- **[`tests/eval/rubric.md`](tests/eval/rubric.md)** - the rubric in the form
  the judge sees; also the human-readable reference during calibration.

## Conventions

- `uv` for everything (no `pip install` directly).
- `ruff` for format + lint, `mypy --strict` for types, `pytest` for tests.
  All gates green on commit.
- Shared library types are Pydantic `BaseModel` with `frozen=True,
  extra="forbid"` (per `CLAUDE.md` "Shared types"). Single source of
  truth for the agent / harness contract is
  [`src/wiki_qa/agent_contract.py`](src/wiki_qa/agent_contract.py).
- `DECISIONS.md` is append-only; reversed decisions get a superseding
  entry, never an in-place edit.

## Time spent

5.5 hours of focused work over 23 elapsed hours.

## License & intent

Take-home submission for an Anthropic prompt-engineering interview. Not a
production system.
