# Design rationale

> Companion to the take-home submission. Captures the bet, the design choices,
> and what three iteration cycles surfaced. The full chronological trail —
> alternatives considered, reversed calls, and design pivots — lives in
> `DECISIONS.md`.

A search-by-default agent on Claude Opus 4.7 with a single
`search_wikipedia(query)` tool, evaluated by a 34-case YAML dataset across
10 failure-mode categories. An LLM-as-judge scores 5 quality dimensions
(0–3); 11 deterministic `behavior_checks` run alongside. Three iterations
have run; v1.1 is the production default and lifted every dimension over
v1 (factual_accuracy +0.74, citation_quality +0.91, parse warnings 8/34 →
0/34). The eval explicitly does not score voice or tone — that gap is
deliberate and discussed below.

## The bet: eval-first design

The hardest part of this brief is not writing the prompt. It's knowing
what to iterate the prompt toward. Most prompt-engineering effort is
wasted because the iteration target is fuzzy — "make it better" — and
without a calibrated way to localize a regression, every change is a
guess.

So the bet was to build the eval first and let it shape everything that
followed. The prompt is in service of the eval, not the other way
around.

A concrete example: the two-block `<evidence>...</evidence>` /
`<answer>...</answer>` output structure exists because the judge needs
to score groundedness independently of factual_accuracy. Without a
separate evidence block, "the answer is right but ungrounded" (the model
knew it from priors) is invisible. The structure was added to the prompt
to make a specific eval dimension scorable — not because the user-facing
output needed it.

This bet also dictated the order of work. The contract between the agent
and the harness was the first thing locked. The dataset taxonomy was
designed before any cases were drafted. The rubric was written before
the system prompt was finalized. The prompt iterated against eval
output, not intuition.

**Calibration caveat.** My prior prompt-engineering experience is mostly
with Opus 4.6, not 4.7. Some choices below — particularly anything about
how firm the search-by-default guidance needs to be, or how the model
handles structured output — reflect 4.6-tuned intuition. Read the v1
baseline numbers with that in mind: I expect a chunk of v2's headroom is
just learning where 4.7's defaults differ from 4.6's.

## Six choices that earned the time

### 1. Stress tests over random sampling

34 hand-curated cases across 10 failure-mode categories rather than
~200 from HotpotQA / SimpleQA / TriviaQA. The trade-off is explicit:
no statistical confidence on average performance, only pattern
detection at this scale. Public-dataset random samples are mostly easy
and don't stress the behaviors that drive prompt-engineering decisions.
Hand-curation buys signal density — every case is a hypothesis about a
specific failure mode, and a fail points at a specific fix. With more
time I'd add the public-dataset sample as a complement, not a
replacement (see "What I'd do with more time"). Adversarial generation
was rejected for v1: it adds setup time and yields cases without
curation; I'd rather have 34 deliberate cases than 100 unvalidated.

### 2. Don't collapse signals that map to different fixes

A recurring pattern across the design. Citation conventions split into
two checks (`has_bracket_citations`, `no_markdown_links`) instead of one
"citation format" check — zero citations and forbidden markdown
citations are different failure modes with different fixes. Parse
warnings clustered into four buckets (missing-block, unclosed-tag,
empty-block, non-canonical) rather than collapsed into one or split
9-way — the four clusters map to four prompt-fix directions. The rubric
explicitly forbids cross-dimension aggregation: averaging
factual_accuracy and citation_quality together is meaningless and
obscures failures. The rule isn't "split everything"; it's "if two
fails would suggest different remediations, keep them visible."

### 3. Deterministic checks alongside the LLM judge

11 deterministic `behavior_checks` (did the model search when it
should have? does the answer have inline citations? did the parser
emit any warnings?) run for every case, separately from the 5-dim
rubric. Each catches what the other can't. The judge can score
groundedness or calibration with judgment that no pattern-match could
replicate; the deterministic checks catch format failures that the
judge might score around if the answer happens to read well. The two
signals stay in separate blocks of the result so a hard format-fail
doesn't get conflated with rubric scores. The judge also gets
`parse_warnings` as informational context with explicit guidance: use
them to *interpret* the answer (an unsupported claim alongside
`empty_evidence_block` reads as a populating failure, not a
hallucination), do not double-count by docking rubric points — the
deterministic checks already handle that signal.

### 4. Surface premise discrepancies, don't correct them

The original framing for the `false_premise` category had the model
"correct" wrong premises ("Einstein actually won the 1921 Nobel for the
photoelectric effect, not relativity"). Reversed mid-design on
customer-tone grounds: the model is not the arbiter of truth and
shouldn't position itself as correcting the user. Its job is to make the
disagreement legible — "Wikipedia indicates X; the question's premise
was Y" — and let the user reconcile it. The reversal rippled through
three places: the rubric's `calibration` dimension penalizes assertive
correction even if the fact is right; the dataset flag was renamed
`must_correct_premise` → `must_surface_premise_discrepancy`; the system
prompt's edge-cases section documents the descriptive-surfacing
behavior. This is the kind of choice the eval can grade because the
rubric was rewritten alongside the prompt change — without the joint
update, the judge would have kept rewarding "correction."

### 5. Two-block `<evidence>` / `<answer>` output for auditability

Every response emits an evidence block (quoted passages from cited
articles) followed by an answer block (prose with inline `[Article
Title]` brackets and a plain-text `Sources:` section). The structure
was chosen for auditability. The judge can compare claims in the
answer to passages in evidence rather than parsing claim-citation pairs
out of prose. The "evidence-block-as-authoritative" rule that landed in
v1.1 makes this load-bearing: every claim in the answer must trace to a
quoted passage in evidence — that's what made `citation_quality` jump
+0.91 and let the judge's groundedness score actually mean something.

### 6. Two parallel agents, one Pydantic contract

The implementation was split into two parallel workstreams under my
direction: workstream A (Wikipedia integration, agent loop, parser,
prompt iterations) and workstream B (eval harness — dataset, runner,
judge, behavior_checks). Both coded against
`src/wiki_qa/agent_contract.py`, a Pydantic `BaseModel` with `frozen=True,
extra="forbid"`. The choice of Pydantic over `@dataclass(frozen=True)`
was deliberate: validation at construction (typed errors instead of
silent drops), `extra="forbid"` makes contract drift loud rather than
silent, JSON round-trip out of the box for `results.jsonl`, and schema
introspection so cross-workstream coordination doesn't require reading
source files. Concrete payoff: when workstream A added the
`ParseWarning` enum and `parse_warnings` field, workstream B picked it
up without breakage and I added four cluster-based deterministic checks
on the new signal in the same session.

## What this eval doesn't measure

The v1 eval measures correctness: factual_accuracy, groundedness,
citation_quality, search_efficiency, calibration. These are the
dimensions that determine whether the answer is right. They don't
measure voice or tone — whether uncertainty is expressed with
confidence or apologetically, whether surfacing a false premise feels
respectful or corrective, whether the agent's prose has the warmth
that distinguishes a thoughtful response from a merely competent one.
This gap is real and deliberate. Voice failures don't surface cleanly
in Wikipedia QA categories — they live in conversational shapes
(frustrated users, ambiguous emotional contexts, situations requiring
graceful uncertainty) that aren't represented in the dataset. Adding
a voice dimension would require both case expansion and judge
calibration against human-scored examples, since voice scoring is more
subjective than correctness scoring and the LLM judge would need a
more calibrated rubric to score it reliably. v2 would address this.
For v1, the calibration dimension partially captures voice-adjacent
behavior — "surface the discrepancy without correcting" is partly a
voice question — but the rubric isn't designed to score voice as a
first-class concern.

## Iterations and what they showed

**Summary.** Three runs against the same 34-case eval. v1 → v1.1 was a
big lift driven by two obvious failure modes (parse warnings 8/34 →
0/34, factual_accuracy +0.74, citation_quality +0.91). v1.1 → v1.2
confirmed a specific local hypothesis on `buried_answer`
(groundedness 2.33 → 3.00) but moved nothing else outside
judge-variance. Kept v1.1 as production. The eval surfaced its own
limit: at n=34 with mostly-ceiling performance, this dataset can find
big failures and big wins but can't statistically distinguish two
near-ceiling prompts.

### v1 baseline

Per-dim means: factual 2.21, grounded 2.29, **citation 1.94** (weakest),
search 2.82, calibration 2.32. Two failure modes dominated:

1. **Parse-warning blowout on non-search cases.** All 8 non-search cases
   (4 `negative_capability` + 4 `unanswerable_*`) emitted
   `missing_evidence_block` + `missing_answer_block`. The agent's
   *behavior* was correct (didn't search arithmetic; refused real-time
   questions cleanly), but the v1 prompt's `<evidence>` / `<answer>`
   structure was framed entirely around grounding-from-search. With no
   search, the model dropped the wrappers; the parser scored them
   malformed; the judge gave 0s on factual / grounded / citation.
2. **Citation over-listing.** The `Sources:` section listed more
   articles than the prose actually cited inline.

### v1.1 — the big lift

Three focused changes: (a) wrappers required even on non-search cases
(e.g. `<evidence>none — Wikipedia is not the appropriate source</evidence>`);
(b) "only list sources you cited inline"; (c) evidence-block-as-
authoritative — claims must trace to a quoted passage. Result: every
dimension up, parse warnings 0/34, every previously-failing behavior
check at 0 fails. The +0.91 jump on citation_quality is the largest
single delta in the iteration story.

### v1.2 — confirmed the local hypothesis, didn't justify shipping

Tightened the marked-inference rule to a binary (every claim either
traces to evidence, is explicitly marked as inference, or doesn't
appear). Targeted the v1.1 `buried_answer` groundedness regression
(3.00 → 2.33). Result: buried_answer groundedness recovered to 3.00
(+0.67). Every other delta was within judge-noise (global Δ between
−0.09 and +0.03). Kept v1.1 in production; preserved v1.2 as
`prompts/system_v1_2.md` for reference.

**The methodology lesson** v1.2 surfaced: at this n with mostly-ceiling
performance, deltas under ~0.30 are inside judge-stochasticity —
meaning a v1.2 prompt regression and a v1.1 prompt regression are
indistinguishable at this dataset size. The eval was designed for
surfacing big failures (v1's 8/34 parse warnings) and big wins (v1 →
v1.1), not for ranking similar prompts at near-ceiling. The right
investment isn't another prompt iteration — it's bigger n and
multi-run averaging. See "What I'd do with more time."

## What I'd do with more time

Three clusters, ordered roughly by impact-per-hour. Happy to iterate
on this list with you.

**Eval depth and external validity.** ~34 hand-curated cases gives
pattern detection, not statistical confidence — and v1.2 made the
limit concrete. With more time:
- Stratified random sample from HotpotQA / SimpleQA (~200 cases) as a
  complement, not a replacement. Catches failure modes I didn't think
  to taxonomize and gives a generalization baseline.
- Bump iteration-signal categories to 20+ cases each for confidence
  intervals on per-dimension means.
- Run the calibration workflow against multiple SMEs, not just my own
  spot-check. The current plan is the right shape — sample
  stratified across dims and judge-buckets, dump to a markdown sheet,
  read scores back via YAML round-trip — but the right *N* is
  "multiple humans, 50+ cases each," with per-dim agreement statistics.
- Multi-run averaging on the same dataset (run each prompt 3–5× and
  average) to separate prompt-effect from judge-stochasticity.
  Required to distinguish v1.1 vs v1.2 at all.
- Diverse gold sources beyond a single annotator. Extend the dataset
  with cases written by domain experts and from real customer logs
  (de-identified) to escape the hand-curation echo chamber.

**More iteration cycles, with structural moves.** v1.2 hit diminishing
returns on prompt-only changes for `buried_answer`. The next move is
structural, not prompt-only:
- Add `fetch_wikipedia_article(title)` as a second tool, or drop
  `exintro=true` and return more of the article body. Fixes the
  ceiling that prompt iteration alone can't reach under the
  single-tool / lead-extract design.
- New failure-mode categories: `controversial_topic` (where Wikipedia
  itself is contested), `policy_advice` (legal/medical/financial —
  shouldn't answer even though Wikipedia could), `fresh_search` (where
  the cutoff caveat needs more care).
- Per-category search budgets (5 calls is one global cap; multi-hop
  probably wants more, simple_factual less).

**Voice as a first-class dimension.** Per the gap section above —
requires case expansion (conversational shapes, not Wikipedia-QA
shapes) and judge calibration against human voice-scored examples.
This is v2 territory, not v1.

**Smaller items.** Multi-turn (clarifying-question disambiguation),
judge ensemble (multiple judges per case, take median + flag
disagreement), cost / latency dashboards, transcript redaction at
extract time, per-version prompt diffs in `iterations.md` so the
prompt-evolution trail is queryable.

## Use of AI

The brief expects AI tooling during development and asks for transcripts
alongside the code. Brief account of where AI was used and what
judgment I applied:

**Code.** The two-workstream split (see "Six choices" #6) put one agent
on the Wikipedia / agent / parser side and another on the eval harness,
both coding against the shared Pydantic contract. I directed both:
defined the contract, specified the architecture, called the design
choices, pushed back on proposals. Representative pushbacks: collapsed
parse-warning checks → split into four clusters; single citation check
→ split into bracket-presence and no-markdown-links; single-string
`answer` field → split into `evidence` / `answer` / `raw_output`. Every
design decision in `DECISIONS.md` was either mine or run by me before
the change landed; the agents implemented them.

**Eval cases.** I designed the failure-mode taxonomy, the per-category
counts and difficulty mix, and the `expected_behavior` flag semantics.
An agent drafted individual cases (questions, gold answers, notes)
within those constraints. I reviewed each case before it entered the
dataset and edited where the draft missed the failure mode it was
supposed to test. The judgment that matters here is taxonomic: deciding
which behaviors are worth testing (and which aren't — see the voice
gap), and how many cases each one warrants.

**System prompt.** Drafted, AI suggested edits, accepted or rejected.
The reversed false-premise framing (correct → surface) is the
representative example of where I rejected an AI suggestion on
customer-tone grounds and the change rippled through the rubric, the
dataset flag name, and the prompt's edge-cases section.

**What I did not let the AI do unsupervised:** define the rubric
dimensions, set the agent ↔ harness contract, accept design proposals
without writing them into `DECISIONS.md` first. The decision log is
the surface where my judgment is auditable.

Transcripts: `[TODO: link to the Claude Code transcript artifact when
generated.]`

## Time spent

`[TODO: fill in once v1 is cut.]`
