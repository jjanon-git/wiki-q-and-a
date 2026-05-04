# Eval iterations log

One section per iteration. Newest at the bottom (chronological forward).

For each iteration, capture:
- Timestamp (UTC)
- Prompt version + dataset version
- Per-dimension means (across all cases)
- Per-category means (across iteration-signal categories)
- Key findings driving the next iteration
- Link to `eval_runs/` output

The full design-decision rationale lives in `DECISIONS.md`. This file is
the focused per-iteration scoreboard for tracking deltas.

---

## v1 — 2026-05-03 18:54 UTC

- **Prompt version**: `prompts/system_v1.md`
- **Dataset version**: `tests/eval/cases/v1.yaml` (34 cases across 10 categories)
- **Models**: agent = `claude-opus-4-7`, judge = `claude-opus-4-7`
- **Run**: `eval_runs/v1_baseline_2026-05-04T01-41-57Z/`
- **Wall-clock**: ~5 min (concurrency=3)
- **Spend**: ~$3
- **Errors**: 0/34

### Per-dimension means (0-3 scale, all 34 cases)

| Dimension | Mean | Min | Max |
|---|---|---|---|
| factual_accuracy | 2.21 | 0 | 3 |
| groundedness | 2.29 | 0 | 3 |
| **citation_quality** | **1.94** | 0 | 3 |
| search_efficiency | 2.82 | 2 | 3 |
| calibration | 2.32 | 0 | 3 |

### Per-category × per-dimension means

| Category | n | factual | grounded | citation | search | calibration |
|---|---|---|---|---|---|---|
| `simple_factual` | 4 | 3.00 | 3.00 | 2.75 | 3.00 | 3.00 |
| `multi_hop` | 5 | 3.00 | 2.80 | 2.20 | 3.00 | 3.00 |
| `multi_source` | 3 | 3.00 | 3.00 | 3.00 | 3.00 | 3.00 |
| `disambiguation_explicit` | 3 | 3.00 | 3.00 | 2.67 | 3.00 | 3.00 |
| `buried_answer` | 3 | 2.33 | 3.00 | 2.67 | 2.67 | 3.00 |
| `false_premise` | 5 | 2.60 | 2.80 | 2.20 | 2.60 | 2.60 |
| `temporal` | 2 | 3.00 | 3.00 | 3.00 | 3.00 | 2.00 |
| `negative_capability` | 4 | **0.00** | **0.25** | **0.00** | 3.00 | 1.00 |
| `unanswerable_not_in_wp` | 3 | 1.00 | 0.67 | 0.67 | 2.67 | 1.00 |
| `unanswerable_too_recent` | 2 | 0.50 | 1.00 | 0.00 | 2.00 | 0.50 |

Iteration-signal categories (4+ cases): `simple_factual`, `multi_hop`,
`negative_capability`, `false_premise`. Excluding `negative_capability` (where
the prompt-format defect dominates), the agent is performing at or near
ceiling on grounded factual cases.

### Key findings

1. **Headline: prompt format defect on non-search cases.** All 8
   non-search cases (4 `negative_capability` + 4 `unanswerable_*`)
   emitted `missing_evidence_block` + `missing_answer_block` parse
   warnings. The agent's actual behavior was correct (didn't search for
   arithmetic; refused cleanly on real-time questions), but the v1
   prompt's `<evidence>/<answer>` structure was framed entirely around
   grounding-from-search and didn't tell the model what to do when not
   searching. The model dropped the wrappers, the parser scored as
   malformed, the judge scored 0 on factual / grounded / citation.
   Drives v1.1 change (a): generalize output structure to require
   wrappers even on non-search responses.

2. **Citation_quality is universally weakest.** 1.94 mean. Driven by
   a consistent failure mode: agent lists more sources in the
   `Sources:` section than it actually cites inline (extra references
   not used in prose). Drives v1.1 change (b): tighten citation rule
   to "only list sources you cited inline."

3. **`searched_when_required` failed on 4 unanswerable cases** —
   Reykjavík weather, Anthropic followers, NBA last night, FOMC. Agent
   recognized these as cases where Wikipedia couldn't help and skipped
   the search. Dataset flag `must_search: true` was overly prescriptive
   for intrinsically-unanswerable real-time/private/operational
   questions. Dataset refinement (split sub-types) deferred to v1.2.

### Behavior_checks pass rates (corrected analysis)

| Check | Pass | Fail | NA |
|---|---|---|---|
| `answer_length_plausible` | 26 | 8 | 0 |
| `did_not_search_when_prohibited` | 4 | 0 | 30 |
| `has_bracket_citations` | 26 | 0 | 8 |
| `has_collated_sources` | 26 | 0 | 8 |
| `no_markdown_links` | 26 | 0 | 8 |
| `not_excessive_searches` | 34 | 0 | 0 |
| `output_blocks_canonical` | 34 | 0 | 0 |
| `output_blocks_non_empty` | 34 | 0 | 0 |
| `output_blocks_well_formed` | 34 | 0 | 0 |
| `output_has_required_blocks` | 26 | 8 | 0 |
| `searched_when_required` | 26 | 4 | 4 |

The 8 fails on `output_has_required_blocks` and `answer_length_plausible`
are the same 8 non-search cases driving the headline format-defect
finding above. The harness is producing clean signal — it caught the
format defect, the citation-quality pattern, and the must_search
prescriptiveness issue all in one run.

### v1.1 scope

Three focused changes (deferring four others to v1.2 to keep per-dim
deltas attributable):

- **(a)** Output structure fix: `<evidence>/<answer>` required even on
  non-search responses (e.g., `<evidence>none — Wikipedia is not the
  appropriate source for this question</evidence>`).
- **(b)** Citation tightening: only list sources you cited inline.
- **(c)** Grounding rule: evidence-block-as-authoritative — claims in
  the answer must trace to a quoted passage in the evidence block.

Deferred to v1.2: per-search motivation framing, disambiguation
criteria refinement, length-by-complexity, evidence-as-you-go.

---

## v1.1 — 2026-05-03 19:13 UTC

- **Prompt version**: `prompts/system_v1_1.md`
- **Dataset version**: `tests/eval/cases/v1.yaml` (same 34 cases as v1)
- **Models**: agent = `claude-opus-4-7`, judge = `claude-opus-4-7`
- **Run**: `eval_runs/v1_1_2026-05-04T02-06-51Z/`
- **Wall-clock**: ~5 min (concurrency=3)
- **Spend**: ~$3
- **Errors**: 0/34

### Per-dimension means (0-3 scale, all 34 cases) — v1 vs v1.1

| Dimension | v1 | v1.1 | Δ |
|---|---|---|---|
| factual_accuracy | 2.21 | 2.94 | **+0.74** |
| groundedness | 2.29 | 2.74 | +0.44 |
| citation_quality | 1.94 | 2.85 | **+0.91** |
| search_efficiency | 2.82 | 2.88 | +0.06 |
| calibration | 2.32 | 2.82 | +0.50 |

All five dimensions improved. The two largest gains (factual_accuracy
+0.74, citation_quality +0.91) match the two biggest v1 failure modes
(format defect on non-search cases; over-listing in `Sources:`).

### Per-category × per-dimension means — v1.1 absolute

| Category | n | factual | grounded | citation | search | calibration |
|---|---|---|---|---|---|---|
| `simple_factual` | 4 | 3.00 | 3.00 | 3.00 | 3.00 | 3.00 |
| `multi_hop` | 5 | 3.00 | 3.00 | 2.80 | 3.00 | 3.00 |
| `multi_source` | 3 | 3.00 | 3.00 | 3.00 | 2.67 | 3.00 |
| `disambiguation_explicit` | 3 | 3.00 | 2.67 | 2.67 | 3.00 | 3.00 |
| `buried_answer` | 3 | 2.67 | 2.33 | 3.00 | 2.67 | 3.00 |
| `false_premise` | 5 | 3.00 | 2.80 | 3.00 | 3.00 | 2.60 |
| `temporal` | 2 | 3.00 | 3.00 | 3.00 | 3.00 | 2.00 |
| `negative_capability` | 4 | 3.00 | 2.25 | 2.25 | 3.00 | 3.00 |
| `unanswerable_not_in_wp` | 3 | 2.67 | 2.33 | 3.00 | 2.67 | 2.33 |
| `unanswerable_too_recent` | 2 | 3.00 | 3.00 | 3.00 | 2.50 | 3.00 |

### Per-category × per-dimension deltas (v1.1 − v1)

| Category | factual | grounded | citation | search | calibration |
|---|---|---|---|---|---|
| `simple_factual` | 0.00 | 0.00 | +0.25 | 0.00 | 0.00 |
| `multi_hop` | 0.00 | +0.20 | +0.60 | 0.00 | 0.00 |
| `multi_source` | 0.00 | 0.00 | 0.00 | **−0.33** | 0.00 |
| `disambiguation_explicit` | 0.00 | **−0.33** | 0.00 | 0.00 | 0.00 |
| `buried_answer` | +0.33 | **−0.67** | +0.33 | 0.00 | 0.00 |
| `false_premise` | +0.40 | 0.00 | +0.80 | +0.40 | 0.00 |
| `temporal` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| `negative_capability` | **+3.00** | **+2.00** | **+2.25** | 0.00 | **+2.00** |
| `unanswerable_not_in_wp` | **+1.67** | **+1.67** | **+2.33** | 0.00 | **+1.33** |
| `unanswerable_too_recent` | **+2.50** | **+2.00** | **+3.00** | +0.50 | **+2.50** |

### Behavior_checks comparison

| Check | v1 pass | v1.1 pass | v1 fail | v1.1 fail |
|---|---|---|---|---|
| `output_has_required_blocks` | 26 | **34** | 8 | **0** |
| `answer_length_plausible` | 26 | **34** | 8 | **0** |
| `searched_when_required` | 26 | **30** | 4 | **0** |
| `has_bracket_citations` | 26 | 30 | 0 | 0 |
| `has_collated_sources` | 26 | 30 | 0 | 0 |
| `no_markdown_links` | 26 | 30 | 0 | 0 |
| `did_not_search_when_prohibited` | 4 | 4 | 0 | 0 |
| `not_excessive_searches` | 34 | 34 | 0 | 0 |
| `output_blocks_canonical` | 34 | 34 | 0 | 0 |
| `output_blocks_non_empty` | 34 | 34 | 0 | 0 |
| `output_blocks_well_formed` | 34 | 34 | 0 | 0 |

Every previously-failing check is now at zero failures.

### Parse warnings

- v1: 8/34 cases (every `negative_capability` + every `unanswerable_*`)
- **v1.1: 0/34 cases** ✓

### Attribution: which change drove which delta?

These are best-guess attributions, not proofs. Where the evidence is
strong I say so; where it's weak I flag it.

**Change #1 (output structure for non-search) — strong attribution:**
- Parse warnings: 8/34 → 0/34 ✓ (direct).
- `output_has_required_blocks`: 8 fails → 0 ✓ (direct).
- `answer_length_plausible`: 8 fails → 0 ✓ (no more empty answers).
- Drove the massive per-category gains in `negative_capability` (factual
  0→3.0, calibration 1→3.0), `unanswerable_not_in_wp` (factual 1→2.67),
  and `unanswerable_too_recent` (factual 0.5→3.0). These cases had
  empty parsed `answer` strings in v1 because the model dropped the
  wrappers; v1.1 forces the wrappers, the parser extracts the content,
  the judge has something to grade. Most of the global +0.74 on
  factual_accuracy and +0.50 on calibration is attributable to this
  single change.

**Change #2 (citation tightening) — strong attribution on grounded cases:**
- Global citation_quality: 1.94 → 2.85 (+0.91), the biggest dimension
  jump. ✓
- Per-category citation gains on grounded categories (where the
  over-listing pattern was the failure mode):
  - `simple_factual`: 2.75 → 3.00 (+0.25)
  - `multi_hop`: 2.20 → 2.80 (+0.60)
  - `false_premise`: 2.20 → 3.00 (+0.80)
  - `buried_answer`: 2.67 → 3.00 (+0.33)
- Citation gains on non-search categories (negative_capability +2.25,
  unanswerable +2.33–3.00) are mostly attributable to **change #1**
  (those cases now emit citations at all), not #2. So #2's clear
  contribution is the +0.25 to +0.80 across grounded categories.

**Change #3 (evidence-block-as-authoritative) — weak / mixed attribution:**
- Hard to isolate from #1, since both affect grounding quality and #1's
  effect dominates.
- Looking only at categories that had non-zero grounded scores in v1
  (excluding the format-defect-driven non-search categories):
  groundedness changes were mostly flat or slightly negative.
  - `buried_answer` groundedness: 3.00 → 2.33 (−0.67). Two cases lost
    a point because the v1.1 model added context not strictly in
    retrieved content (e.g., "Aaron and Bonds breaking the record" for
    Babe Ruth; flagged "commonly reported" Cadabra/Relentless prior).
    The stricter rule appears to be tightening the judge's evaluation —
    which is the rule working, but it cost a point on cases where the
    v1 judge had been more lenient.
  - `disambiguation_explicit` groundedness: 3.00 → 2.67 (−0.33).
    Similar pattern: v1.1 model added "Surabaya being on Java", a minor
    inference not directly in the snippet.
- Net read: change #3 didn't move the grounded-case means much, and
  caused small regressions where the model added marked-or-implicit
  inferences. The marked-inference path may need clearer guidance in
  v1.2 (the judge dings inferences even when they're flagged "from
  prior", which the prompt nominally allows).

**Change #4 (verify absence by searching) — strong attribution:**
- `searched_when_required`: 4 fails → 0 fails ✓ (direct).
- All 4 previously-failing cases now search and produce searched-but-
  unanswerable evidence (per the new prompt shape).
- Drove the unanswerable categories' improvements jointly with #1.

### One regression worth watching: `multi_source` search_efficiency 3.00 → 2.67

`multi_source_003` (Elizabeth II vs Thatcher birth years) did 4
searches in v1.1 vs 2 in v1. Possible cause: the verify-absence rule
made the agent more thorough than necessary on a case where 2 searches
already had the answer. n=3 in this category — could be noise. Worth
watching in v1.2 to see if the verify-absence framing causes
over-searching on grounded cases.

### v1.2 scope candidates (deferred)

Carrying forward from the original v1.1 plan plus this iteration's
findings:

- **Per-search motivation framing** (deferred from v1.1)
- **Disambiguation criteria refinement** (deferred from v1.1)
- **Length-by-complexity** (deferred from v1.1)
- **Evidence-as-you-go** (deferred from v1.1)
- **Marked-inference clarity** — judge isn't crediting "from prior"
  inferences even when prompt allows them. Either tighten the prompt's
  marked-inference rule or update the rubric to match.
- **Dataset refinement on `unanswerable_*`** — not needed anymore;
  v1.1's verify-absence rule converted this from a dataset issue to a
  prompt fix.

---

## v1.2 — 2026-05-03 19:58 UTC

- **Prompt version**: `prompts/system_v1_2.md`
- **Dataset version**: `tests/eval/cases/v1.yaml` (same 34 cases as v1, v1.1)
- **Models**: agent = `claude-opus-4-7`, judge = `claude-opus-4-7`
- **Run**: `eval_runs/v1_2_2026-05-04T02-44-39Z/`
- **Wall-clock**: ~5 min (concurrency=3)
- **Spend**: ~$3
- **Errors**: 0/34

### Two changes from v1.1

1. **Marked-inference rule tightened to binary.** Every claim must
   either (a) trace to evidence, (b) be marked with the inference
   syntax, OR (c) not appear in the answer. Targeted the v1.1
   `buried_answer` and `disambiguation_explicit` groundedness
   regressions.
2. **Evidence-as-you-go.** Build the evidence block incrementally
   during search rather than reconstructing it post-hoc.

### Per-dimension means — v1 → v1.1 → v1.2

| Dimension | v1 | v1.1 | v1.2 | Δ v1.1→v1.2 | Δ v1→v1.2 |
|---|---|---|---|---|---|
| factual_accuracy | 2.21 | 2.94 | 2.88 | −0.06 | +0.68 |
| groundedness | 2.29 | 2.74 | 2.65 | −0.09 | +0.35 |
| citation_quality | 1.94 | 2.85 | 2.82 | −0.03 | +0.88 |
| search_efficiency | 2.82 | 2.88 | 2.88 | 0.00 | +0.06 |
| calibration | 2.32 | 2.82 | 2.85 | +0.03 | +0.53 |

### Per-category × per-dimension — v1.2 absolute

| Category | n | factual | grounded | citation | search | calibration |
|---|---|---|---|---|---|---|
| `simple_factual` | 4 | 3.00 | 3.00 | 3.00 | 3.00 | 3.00 |
| `multi_hop` | 5 | 3.00 | 2.80 | 2.80 | 3.00 | 3.00 |
| `multi_source` | 3 | 3.00 | 3.00 | 3.00 | 2.67 | 3.00 |
| `disambiguation_explicit` | 3 | 3.00 | 2.33 | 3.00 | 3.00 | 3.00 |
| `buried_answer` | 3 | 2.33 | **3.00** | 3.00 | 2.67 | 3.00 |
| `false_premise` | 5 | 2.80 | 2.80 | 3.00 | 2.80 | 2.60 |
| `temporal` | 2 | 3.00 | 3.00 | 3.00 | 3.00 | 2.00 |
| `negative_capability` | 4 | 3.00 | 2.00 | 2.00 | 3.00 | 3.00 |
| `unanswerable_not_in_wp` | 3 | 2.67 | 2.00 | 2.67 | 2.67 | 2.67 |
| `unanswerable_too_recent` | 2 | 3.00 | 2.50 | 3.00 | 3.00 | 3.00 |

### Per-category × per-dimension — v1.1 → v1.2 deltas

| Category | n | factual | grounded | citation | search | calibration |
|---|---|---|---|---|---|---|
| `simple_factual` | 4 | 0 | 0 | 0 | 0 | 0 |
| `multi_hop` | 5 | 0 | −0.20 | 0 | 0 | 0 |
| `multi_source` | 3 | 0 | 0 | 0 | 0 | 0 |
| `disambiguation_explicit` | 3 | 0 | −0.33 | +0.33 | 0 | 0 |
| `buried_answer` | 3 | −0.33 | **+0.67** | 0 | 0 | 0 |
| `false_premise` | 5 | −0.20 | 0 | 0 | −0.20 | 0 |
| `temporal` | 2 | 0 | 0 | 0 | 0 | 0 |
| `negative_capability` | 4 | 0 | −0.25 | −0.25 | 0 | 0 |
| `unanswerable_not_in_wp` | 3 | 0 | −0.33 | −0.33 | 0 | +0.33 |
| `unanswerable_too_recent` | 2 | 0 | −0.50 | 0 | +0.50 | 0 |

### Behavior_checks: identical to v1.1

Every previously-failing check still at 0 fails. Every check pass count
unchanged. Parse warnings 0/34 (same).

### Honest read

v1.2's deltas are within judge-noise at this dataset size. With
per-category n=2-3 in many places, a single judgment shifting one
point = 0.33-0.50 delta — meaning we can't statistically distinguish
a v1.2 prompt regression from a v1.1 prompt regression with this
sample.

**One clear local win**: `buried_answer` groundedness recovered from
2.33 (v1.1) to 3.00 (v1.2). This was the regression v1.2 change #1
(marked-inference tightening) was specifically aimed at. The model
now properly avoids unmarked additions on buried_answer cases. Cost:
factual_accuracy on the same category dropped 0.33 — a single case
where the stricter rule prevented the model from reaching a fully
correct answer.

**Aggregate**: v1.1 had already gotten most grounded categories to
ceiling (3.00 across simple_factual, multi_hop, multi_source,
disambiguation, false_premise factual). v1.2's room to improve in
those was zero; the small declines elsewhere are most likely judge
variance, not prompt regressions.

**Decision**: v1.1 remains the production default in
`src/wiki_qa/agent.py`. v1.2 is preserved as `prompts/system_v1_2.md`
for reference. To distinguish v1.1 vs v1.2 statistically would
require either a larger dataset (each category at 10+ cases for stable
per-dim means) or multi-run averaging on the same dataset (run each
prompt 3-5 times against the same cases to average out judge variance).
Both deferred — at this scale the iteration story is what's
informative, not the v1.2 final number.

### Methodological caveats now load-bearing

This iteration surfaces a real limitation of the eval methodology
worth being honest about:

1. **Small-n per category** means deltas under ~0.3 are inside
   judge-noise. Useful for detecting big failures (v1's 8/34 parse
   warnings) and big wins (v1 → v1.1 jumps). Not useful for
   distinguishing two reasonable prompts at near-ceiling performance.
2. **Single-run averaging** doesn't separate prompt-effect from
   judge-stochasticity. A prompt change that happens to produce a
   slightly different answer the judge marks differently looks like a
   regression even if the prompt itself is fine.

Both knowable from the start; v1.2 made them concrete. The takeaway:
this eval suite was designed for prompt-engineering iteration on
specific failure modes, not for ranking similar prompts. v1 → v1.1
was the kind of delta this dataset measures well; v1.1 → v1.2 is
near the floor of what it can distinguish.
