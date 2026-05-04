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

3. **Behavior_checks all `na`.** All 11 deterministic checks returned
   "not applicable" for all 34 cases. Workstream B issue — likely a
   schema mismatch between case YAML fields and what the checks read
   from `AgentResult`. Must be fixed before v1.1 runs or v1.1 won't
   have behavior_check signal either.

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
