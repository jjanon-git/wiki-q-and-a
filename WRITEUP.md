# wiki-q-and-a — design rationale

> **Status: v1 draft.** Captures the design choices and reasoning from
> `DECISIONS.md`, organized thematically. To be revised against my style
> guide before final submission. Sections that depend on v1 eval results
> are marked `[pending baseline]`.

---

## What this is

A take-home submission. The brief: build a system that uses Claude and
Wikipedia to answer questions, and evaluate how well it works. This
document explains the design choices behind the prompt, the tool, and
the eval suite — what I picked, what I rejected, and why.

The system is a search-by-default agent powered by Claude Opus 4.7,
calling a single `search_wikipedia(query)` tool against the live
MediaWiki API. The eval harness loads YAML cases, runs the agent,
applies 11 deterministic behavior checks, and asks an LLM judge to
score 5 quality dimensions on a 0–3 scale. Results are written per-case
to `results.jsonl` for inspection and iteration.

Model choice: Opus 4.7 for both the agent and the judge. The reasoning
was to set the quality ceiling first and downgrade on eval evidence
rather than start middle and try to climb. Cost is not a real
constraint at this scale (a few hundred API calls total). The
downgrade story (Opus → Sonnet → Haiku, with measured per-dim deltas)
is itself a useful artifact for this writeup. Both models are
overridable via `WIKI_QA_AGENT_MODEL` / `WIKI_QA_JUDGE_MODEL` env
vars so the experiment is a one-line flip.

## Development approach: two parallel workstreams with a hardened contract

The implementation was split into two parallel workstreams, each
driven by its own agent under my direction:

- **Workstream A** — Wikipedia integration (`search_wikipedia`,
  MediaWiki client, response formatter), agent loop, output parser,
  system prompt iterations.
- **Workstream B** — eval harness (dataset loader, behavior_checks,
  runner, judge, CLI).

The two could evolve independently because they shared a single
**hardened contract** at `src/wiki_qa/agent_contract.py`: a Pydantic
`BaseModel` with `model_config = ConfigDict(frozen=True,
extra="forbid")`. Pydantic over `@dataclass(frozen=True)` was a
deliberate choice — captured in the project's `CLAUDE.md` "Shared
types" rule and applied to every type that crosses a workstream
boundary or hits disk:

- **Validation at construction.** Both sides get type checking and
  required-field enforcement immediately, not at first use.
- **`extra="forbid"`.** Contract drift is loud rather than silent. If
  one side adds a field without updating the contract, the other side
  fails at construction with a clear error rather than dropping it.
- **JSON round-trip out of the box.** `model_dump_json()` /
  `model_validate_json()` make `results.jsonl` a first-class artifact
  — every `EvalResult` round-trips for inspection or re-analysis.
- **Schema introspection.** Cross-workstream coordination doesn't
  require reading source files; the schema is queryable.

Concrete payoffs the contract earned during the build:

- When workstream A added the `ParseWarning` enum and a
  `parse_warnings` field on `AgentResult`, workstream B picked it up
  with zero breakage (the field defaulted to `[]`), and I added four
  cluster-based deterministic checks consuming the new signal in the
  same session.
- When the contract was migrated mid-build from `@dataclass` to
  Pydantic, and the single `answer: str` was split into
  `evidence` / `answer` / `raw_output` to reflect the two-block output
  structure, both sides updated cleanly because the contract was the
  single source of truth — no duplicate definitions to keep in sync.

Risk the approach creates: the harness codes against a contract that
drifts. Mitigation: define the contract first, treat it as frozen
until v1 eval is green, and route every change through an explicit
`DECISIONS.md` entry so the rationale survives the change.

## Prompt engineering approach

### Search by default, with explicit exceptions

The first tool description was narrow ("when you need factual info you
don't reliably know"). That left too much room for the model to skip
search and answer from priors — defeating the entire point of a
Wikipedia QA system. v1's tool description is **default-on**: use the
tool by default for any question that benefits from grounding;
exceptions are arithmetic, code generation, opinion/preference, and
content already in the conversation. The system prompt re-states the
same exception list — defense in depth.

### Two-block output: evidence first, then answer

The agent emits `<evidence>...</evidence>` (quoted passages from cited
articles) followed by `<answer>...</answer>` (the prose answer with
inline bracket citations). The structure was chosen for auditability:
the evidence block is the "show your work" surface that makes
groundedness and citation_quality inspectable, both for humans and the
LLM judge. The judge gets the parsed `evidence` separately and can
compare claims in the answer to passages in evidence rather than
parsing claim-citation pairs out of prose.

The grounding rule is two-part: every factual claim must be supported
by retrieved Wikipedia content, AND inferences are allowed but must be
marked explicitly ("Wikipedia says X; from this it follows that Y,
because…"). The marked-inference allowance preserves the model's
ability to handle synthesis questions; without it, the prompt would
paralyze the model on any question requiring connecting two facts.

### Citation conventions: brackets, plain text, no markdown links

Inline citations are bracket-only `[Article Title]` references with no
embedded URLs. URLs appear once at the end in a plain-text `Sources:`
section, formatted `Title - URL` per line. Markdown link syntax
(`[Title](URL)`) is explicitly forbidden.

This came from customer-feedback experience: raw URLs at the end keep
the prose readable and make the source list scannable; embedded
markdown links muddy the inline reading experience and are awkward to
copy out.

The deterministic behavior checks split this into two separate signals
(`has_bracket_citations` and `no_markdown_links`) rather than one
collapsed "citation format" check. A model producing zero citations is
a different failure mode (and different fix — strengthen the citation
requirement) than a model producing markdown citations (different fix
— emphasize the format ban). Collapsing them would hide which one is
firing across iterations.

### Surfacing premise discrepancies, not correcting

The original framing for the `false_premise` category had the model
"correct" wrong premises — e.g., "Einstein actually won the 1921 Nobel
for the photoelectric effect, not relativity." That was reversed
mid-design on customer-tone grounds: the model is not the arbiter of
truth and shouldn't position itself as correcting the user. Its job is
to make the disagreement legible — "Wikipedia indicates X; the
question's premise was Y" — and let the user reconcile it.

This shows up in three places: the rubric's `calibration` dimension
(which now penalizes assertive correction even if the fact is right),
the dataset flag (`must_surface_premise_discrepancy`), and the system
prompt's edge-cases section.

### Decomposition over compound queries; 5-call budget

For multi-part questions, the prompt instructs the agent to decompose
into separate searches (one per facet) rather than issue compound
queries. Hypothesis: MediaWiki search rewards specific terms; long
compound queries dilute the signal. The trade-off — extra searches
that don't help — is a thing eval should flag if it's happening; that's
what `search_efficiency` is for.

The search budget is 5 calls per question, with the explicit
instruction "answer with what you have rather than running out the
clock." This makes the cap a soft fail: the model should triage rather
than panic. 5 is a starting number; if eval shows multi-hop questions
hitting the cap, raise; if shows wasted searches near the cap, lower.

### Reflection between searches

After each tool call, the prompt requires the agent to make an explicit
decision: stop, refine the query, decompose to a different facet, or
admit Wikipedia doesn't have it. Four explicit branches make the
decision tree legible to the model. This was firmed up after observing
that Opus 4.7 sometimes gives up after one weak result.

### Length: 2-4 paragraphs

"Thorough but not comprehensive." Not a Wikipedia dump; a focused
answer. Single-fact questions should be short. The qualifier "longer
for genuine synthesis" preserves room for multi-source questions to
get the treatment they need.

## Eval design

### Hand-curated stress tests, not stratified random sampling

The eval set is ~35 cases across 10 failure-mode categories rather
than a sample from a public dataset like HotpotQA, SimpleQA, or
TriviaQA. The taxonomy, per-category counts, difficulty mix, and
`expected_behavior` flag semantics are mine; an agent drafted
individual cases (questions, gold answers, notes) within those
constraints, and I reviewed before they entered the dataset. See
"Use of AI in development" below for a fuller account of how the
agent was directed and what review I applied.

Trade-off accepted: no statistical confidence on average performance,
only pattern detection. With most categories at 2–4 cases, the eval
can detect meaningful regressions and surface failure patterns, but
can't put confidence intervals on per-dimension means.

What I gain in exchange: signal density. Every case is a hypothesis
about a specific failure mode. Public-dataset random samples are
mostly easy and don't stress the behaviors that drive
prompt-engineering decisions. The curated set lets every case inform
the rubric and every failure point at a specific fix.

I considered adversarial generation (red-team Claude to produce hard
cases). Rejected for v1 because it adds setup time and yields cases
without curation; I'd rather have 35 deliberate cases than 100
unvalidated ones.

### 10 failure-mode categories

Each category targets a distinct behavior the system might get wrong:

| Category | What it tests |
|---|---|
| `simple_factual` | baseline lookup |
| `multi_hop` | sequential A→B→C reasoning; decomposition; knowing when to stop |
| `multi_source` | parallel info gathering across articles (covers comparative + synthesis) |
| `disambiguation_explicit` | search returns a Wikipedia disambiguation page; pick a sense and note alternatives |
| `buried_answer` | answer exists in the article but not in the lead extract |
| `negative_capability` | should NOT search (math, code, opinion) |
| `false_premise` | factually wrong premise embedded in question; surface the discrepancy |
| `unanswerable_not_in_wp` | answerable in principle but not in Wikipedia; refuse cleanly |
| `unanswerable_too_recent` | events past Wikipedia's freshness; cutoff caveat |
| `temporal` | recency-bounded; caveat with last known state |

Categories were honest about their statistical power. Iteration-signal
categories (4+ cases) are `simple_factual`, `multi_hop`,
`negative_capability`, `false_premise`. Pattern-detection categories
(3 cases) are `multi_source`, `disambiguation_explicit`,
`buried_answer`, `unanswerable_not_in_wp`. Alarm-only categories
(2 cases) are `unanswerable_too_recent`, `temporal` — a single failure
means look closer; a pass is inconclusive.

### Per-dimension rubric, no cross-dimension aggregation

The judge scores five dimensions 0–3:

- **factual_accuracy** — does the answer match what's true
- **groundedness** — are claims supported by retrieved content
- **citation_quality** — are citations precise and correctly attributed
- **search_efficiency** — were searches well-targeted, not wasteful
- **calibration** — does the model know what it knows; refuse when
  appropriate; surface premise discrepancies descriptively; express
  uncertainty when warranted

Aggregation rule: **no aggregation across dimensions within a single
case.** Averaging factual_accuracy and citation_quality together is
meaningless and obscures failures. **Per-dimension means across cases
per iteration are computed and reported** — this is how I measure
iteration deltas, e.g. "v2 lifted factual_accuracy from 2.1 → 2.6 but
groundedness dropped from 2.5 → 1.8."

`groundedness` was added explicitly to separate it from
`factual_accuracy`. An answer can be factually correct but ungrounded
— the model knew it from priors, not from the retrieved content.
That's a fail on groundedness, pass on factual_accuracy. Without the
separation, this failure mode is invisible.

### Deterministic checks alongside the judge

Some failure modes don't need an LLM. Eleven checks run alongside the
judge and produce a separate `behavior_checks` block in the result, so
hard-fails aren't conflated with rubric scores:

- `searched_when_required`, `did_not_search_when_prohibited`,
  `not_excessive_searches`, `answer_length_plausible`
- `has_bracket_citations`, `no_markdown_links` (split — different
  failure modes, different fixes)
- `has_collated_sources` (Title - URL plain-text format)
- Four parse-warning cluster checks consuming the `ParseWarning` enum
  emitted by the response parser:
  - `output_has_required_blocks` (missing-block: model didn't emit
    structure → strengthen prompt)
  - `output_blocks_well_formed` (unclosed-tag: tried, malformed →
    tokenization/length investigation)
  - `output_blocks_non_empty` (empty-block: structure clean, content
    missing → prompt requirement to populate)
  - `output_blocks_canonical` (reversed-order or multiple-blocks: odd
    emission → emphasize evidence-first single-block)

The cluster split was a deliberate choice. An earlier draft collapsed
all 9 parse-warning codes into one binary `no_parse_warnings` check.
Reversed because the codes map to genuinely different fixes, and
collapsing them hides which one is firing.

### Judge sees a deliberate subset

The judge prompt is built from a `JudgeInput` Pydantic model — an
explicit data shape that makes "what we send the judge" readable from
one source.

Included: `question`, `expected_answer`, `expected_behavior` (the
judge-context flags `must_surface_premise_discrepancy` and
`must_refuse`), parsed `evidence`, parsed `answer`, `parse_warnings`,
and `tool_calls` (each with `query` + `raw_result_str` + `latency_ms`,
so the judge can assess groundedness against what the agent actually
saw).

Excluded: `raw_output` (we already have parsed `evidence` and `answer`
— sending the unparsed model text would just bloat the prompt),
`raw_messages` (the full conversation including the system prompt —
the same across cases, redundant), and convenience fields (`queries`,
`n_searches`, `stop_reason`, `usage`) that duplicate `tool_calls` or
aren't relevant to the judge's task.

`parse_warnings` is included as **informational context, not a scoring
directive.** The prompt says use them to *interpret* the answer ("an
unsupported claim alongside `empty_evidence_block` reads as the model
failing to populate evidence rather than asserting something
ungrounded"), but explicitly does NOT apply additional rubric
penalties on this basis — the harness already records structural
failure separately via the four parse-warning behavior checks. Without
this guidance the judge would double-count.

### Judge robustness: parse, retry, isolate

The judge produces XML in a fixed schema (one `<dimension>` per rubric
dimension, `<reasoning>` before `<score>`). Reasoning before score is
per Anthropic's prompt-engineering guidance — separating reasoning
from final output via structured tags improves calibration on judgment
tasks.

Parser handling:
- Missing dimension → that dim's score = `None`, flagged; other dims
  still score
- Score out of range → clamp to 0–3 + flag
- Malformed XML on first attempt → retry once with explicit "return
  only the `<evaluation>` block" guidance
- Second malformed → mark `judge_failure=true`, exclude from per-dim
  means, surface count separately

Per-dim means are computed only over cases where the judge produced
valid output. Per-dim `judge_failure_rate` is its own metric.

### Calibration

The LLM judge needs validation against human judgment to be
trustworthy. The plan: sample 5–10 cases stratified across dimensions
and judge-score buckets, dump them to a markdown sheet for human
review, fill in human scores via a YAML round-trip, and compute per-dim
agreement (`|human - judge| ≤ 1`). If a dimension shows >25%
disagreement, that's signal to revise the rubric or judge prompt.

YAML for the human input rather than markdown round-trip: parsing
human-edited markdown is fragile (whitespace, partial fills, score
formats). YAML keeps the input strict and the read-only markdown view
separate.

## What this eval measures — and what it doesn't

v1 measures correctness across five dimensions: factual_accuracy, 
groundedness, citation_quality, search_efficiency, calibration. 
It doesn't measure voice — whether uncertainty reads as confident 
or apologetic, whether surfacing a false premise feels respectful or 
corrective, whether the prose has warmth.
This is a deliberate gap. Wikipedia QA cases don't surface voice failures. 
Voice failures live in conversational shapes the dataset doesn't cover: 
frustrated users, emotional contexts, situations requiring graceful uncertainty. 
Scoring voice also requires more careful judge calibration than scoring 
correctness, since voice is more subjective.
v2 would add a tone dimension and expand the dataset with conversational 
cases. For v1, the calibration dimension absorbs some voice-adjacent behavior, 
but voice isn't a first-class concern in the rubric.

## v1 baseline findings

### Structural finding: prompt format defect on non-search cases

**v1 baseline surfaced a prompt defect.** The v1 output format assumed all
answers were grounded in retrieved content. On `negative_capability` and
`unanswerable_*` cases, the model correctly didn't search and produced
clean prose — but without `<evidence>` / `<answer>` wrappers, the parser
couldn't extract content and the judge scored 0. The agent's behavior was
correct; the prompt didn't tell it what format to use when the grounding
assumption didn't apply. v1.1 generalizes the output structure.

8 of 34 cases emitted `missing_evidence_block` + `missing_answer_block`
parse warnings — every `negative_capability` and `unanswerable_*` case.
Looking at the underlying behavior: for arithmetic the model produced
"1247 × 393 = 490,071" with a clear "not searching, this is calculation"
rationale; for unanswerable real-time questions it produced clean
refusals explaining Wikipedia is the wrong source and suggesting
alternatives. The model did the right thing; the prompt's strict
`<evidence>/<answer>` requirement (framed entirely around grounding from
retrieved articles) gave it no template for non-search responses, so it
dropped the wrappers and wrote free-form. The parser then scored those
as malformed (`evidence=""`, `answer=""`), and the judge graded zero on
factual_accuracy / groundedness / citation_quality.

### Eval-set validation: failure-mode coverage caught it

**The deliberate inclusion of non-search categories surfaced this
defect.** A correctness-only eval focused on search-and-retrieve would
have missed it. Hand-curated stress tests with failure-mode coverage
produced signal random sampling would not.

This is a vindication of the eval-design choice captured at
2026-05-03 15:57: "stress-test specific failure modes deterministically"
over "maximize topical coverage." The categories that look low-yield
(will the model search for arithmetic? will it refuse appropriately on
real-time questions?) turned out to be the categories that caught the
most consequential prompt defect. A random-sampled QA dataset would
have indexed on factual lookups and missed this entirely.

### Per-dimension v1 baseline (locked)

Run: `eval_runs/v1_baseline_2026-05-04T01-41-57Z/`. 34/34 cases ok, 0
errors, ~5 min wall-clock with concurrency=3, ~$3 spend.

| Dimension | Mean (0-3) | Min | Max |
|---|---|---|---|
| factual_accuracy | 2.21 | 0 | 3 |
| groundedness | 2.29 | 0 | 3 |
| **citation_quality** | **1.94** | 0 | 3 |
| search_efficiency | 2.82 | 2 | 3 |
| calibration | 2.32 | 0 | 3 |

The non-search format defect pulls the means down. Excluding the 8
non-search cases, factual_accuracy and groundedness sit close to
ceiling on the 26 grounded cases. The signal in the v1 baseline isn't
"the agent answers grounded questions poorly" — it's "the prompt
doesn't accommodate non-grounded responses." That's the iteration
target.

### Other findings worth flagging

- **Citation_quality is universally weakest (1.94).** Driven by a
  consistent failure mode: agent lists more sources in the `Sources:`
  section than it actually cites inline. Tightening the citation rule
  to "only list sources you cited inline" is one of the v1.1 changes.

- **Strong cases shine.** `false_premise_005` (Napoleon height myth):
  3/3 on factual_accuracy, groundedness, calibration. Agent searched
  "Napoleon complex" — a sharp, refined query — found the explicit
  "5'2" in pre-metric French = 5'6" imperial / average height" content,
  surfaced the discrepancy descriptively without correcting the user.

- **Single-tool design held.** All 3 `buried_answer` cases recovered
  via query refinement — agent searched a more specific query that
  surfaced a different article whose lead had the buried fact. No need
  to add `fetch_wikipedia_article` as a second tool in v1.

- **`searched_when_required` failed on 4 unanswerable cases** —
  Reykjavík weather, Anthropic followers, NBA last night, FOMC. The
  agent recognized these as cases where Wikipedia couldn't help and
  skipped the search entirely. The dataset flag `must_search: true`
  was overly prescriptive on these — real-time / private / operational
  data is intrinsically not on Wikipedia, and searching wastes a call.
  Distinct from the Curie-address case (where the agent did search,
  correctly, because the absence required verification). Dataset
  refinement (split `unanswerable_*` into search-then-refuse vs.
  refuse-without-searching sub-types) deferred to v1.2.

## Iteration plan and status

The brief explicitly asks for "key iterations you made based on eval
results" — a single pass doesn't answer that. The plan is at least two
iteration cycles:

1. Build v1 prompt + tool, run full eval, collect per-dim scores +
   judge reasoning + behavior_checks. **(Done — see above.)**
2. Identify the lowest-scoring dimensions and the cases driving each
   score down. **(Done — non-search format defect; citation
   over-listing.)**
3. Revise: prompt change, tool description change, return-shape change
   — whichever the failures point to. **(In progress: v1.1 with three
   focused changes.)**
4. Re-run, record per-dim deltas in `DECISIONS.md`.
5. Repeat at least once more.

If two iterations don't move a dimension, that's evidence the rubric
or model itself is the bottleneck — switch axis.

**Current status:** v1 baseline + v1.1 both run. v1.1 included four
focused changes: output structure for non-search, citation tightening,
evidence-block-as-authoritative, and verify-absence-by-searching (added
mid-iteration when v1 surfaced the `searched_when_required` failure
mode).

**v1.1 per-dim deltas vs v1:**

| Dimension | v1 | v1.1 | Δ |
|---|---|---|---|
| factual_accuracy | 2.21 | 2.94 | **+0.74** |
| groundedness | 2.29 | 2.74 | +0.44 |
| citation_quality | 1.94 | 2.85 | **+0.91** |
| search_efficiency | 2.82 | 2.88 | +0.06 |
| calibration | 2.32 | 2.82 | +0.50 |

Every previously-failing behavior check is now at zero failures
(`output_has_required_blocks` 8→0, `answer_length_plausible` 8→0,
`searched_when_required` 4→0). Parse warnings 8/34 → 0/34. Three small
regressions in `buried_answer` and `disambiguation_explicit` groundedness
(stricter evidence-as-authoritative rule biting back when the model
added context beyond retrieved content) — captured for v1.2.

Per-iteration tracking lives in `tests/eval/iterations.md`. Full
attribution (which change drove which delta) is in that file's v1.1
entry — the wins map cleanly to the failure modes each change
targeted.

## Limitations and future work

- **Buried-answer recovery.** Under the single-tool design with
  `exintro=true`, the lead extract is the only article content the
  agent ever sees. Bumping `exchars` widens the lead view but never
  reaches into the article body. For `buried_answer` cases, recovery
  is solely via re-searching with more specific queries that surface a
  different article whose lead has the detail. If v1 evals show a
  meaningful failure rate on this category, the structural fixes are:
  (a) add `fetch_wikipedia_article(title)` as a second tool, or
  (b) drop `exintro=true` and return more of the article body.

- **Multi-turn.** v1 is single-turn (matches the brief's "takes a
  question and returns an answer"). Multi-turn would enable
  clarifying-question disambiguation but materially complicates both
  agent loop and eval. v2.

- **Voice as a first-class dimension.** See above. Requires case
  expansion (conversational shapes, not factual ones) and judge
  calibration against human-scored examples.

- **Judge-of-judges or judge ensemble.** v1 uses a single Opus 4.7
  judge calibrated by human spot-check. An ensemble or
  judge-of-judges would make the calibration more robust at the cost
  of complexity.

- **Statistical significance on per-iteration deltas.** With n=35
  cases, eyeballing per-dim score changes is fine for prompt
  iteration. In production we'd want each category at 20+ cases to
  get confidence intervals on per-dim means.

- **Per-category search budget tuning.** v1 has one global cap (5
  calls). Multi-hop questions might need more; simple-factual might
  benefit from less.

- **Cost / latency dashboards.** Token usage is captured per-result;
  aggregation is currently a writeup-time concern.

## Use of AI in development

The brief expects AI tooling during development and asks for transcripts
alongside the code. Brief account of where AI was used and what
judgment I applied:

**Code (Claude Code).** The two-workstream split (see "Development
approach" above) put one agent on the Wikipedia / agent / parser side
and another on the eval harness, both coding against the shared
Pydantic contract. I directed both: defined the contract, specified
the architecture, called the design choices, pushed back on proposals.
Representative pushbacks: collapsed parse-warning checks → split into
four clusters because the failure modes need different fixes; single
citation check → split into `has_bracket_citations` and
`no_markdown_links` for the same reason; single-string `answer` field
→ split into `evidence` / `answer` / `raw_output` because the prior
shape was hiding the output structure. Every design decision in
`DECISIONS.md` was either mine or run by me before the change landed;
the agents implemented them.

**Eval cases.** I designed the failure-mode taxonomy (10 categories),
the per-category counts and difficulty mix, and the
`expected_behavior` flag semantics. An agent drafted individual cases
(questions, gold answers, notes) within those constraints. I reviewed
each case before it entered the dataset and edited where the draft
missed the failure mode it was supposed to test. The judgment that
matters here is taxonomic: deciding which behaviors are worth testing
(and which aren't — see the v1 voice gap above), and how many cases
each one warrants given the trade-off between signal density and
budget.

**System prompt.** The prompt itself went through several iterations
where I drafted, an agent suggested edits, I accepted or rejected.
The reversed false-premise framing (correct → surface the discrepancy)
is a representative example of where I rejected an agent suggestion
on customer-tone grounds and the change rippled through the rubric,
the dataset flag name, and the prompt's edge-cases section.

**What I did not let the AI do unsupervised:** define the rubric
dimensions, set the agent ↔ harness contract, or accept design
proposals without writing them into `DECISIONS.md` first. The decision
log is the surface where my judgment is auditable.

Transcripts: `[TODO: link to the Claude Code transcript artifact when
generated.]`

## Time spent

`[TODO: fill in once v1 is cut.]`

## Stack

Python 3.12+. `uv` for package management. `ruff` for format and lint.
`mypy --strict` for types. `pytest` for tests. `pydantic` for shared
types (validation, JSON round-trip, schema introspection). All quality
gates green on commit.

## Where the thinking lives

- **`DECISIONS.md`** — append-only chronological log. Every prompt
  change, eval observation, design pivot, and reversed call. Each
  entry notes alternatives considered, the choice, and the reasoning.
  This document is reconstructed from that log.
- **`prompts/system_v1.md`** — the system prompt itself.
- **`plans/`** — design docs written before implementation.
- **`tests/eval/rubric.md`** — the rubric the judge sees, also the
  human-readable reference during calibration.

The decision log convention is itself a deliberate choice: the
design-rationale deliverable is far easier to write from a live log
than reconstructed from memory after the fact.
