# Rubric (v1)

Per-dimension 0–3. Score each dimension independently; do not average across
dimensions. Cite specific evidence from the retrieved content when scoring
factual_accuracy, groundedness, and citation_quality.

## factual_accuracy

How well the answer matches what is true (per the gold answer and the
retrieved content).

| Score | Criteria |
|---|---|
| 0 | Answer is wrong on the central claim. |
| 1 | Partially correct; key facts are wrong or material claims are unsupported by what's retrieved. |
| 2 | Mostly correct; minor inaccuracies only. |
| 3 | Fully correct. |

## groundedness

Whether the answer's claims are supported by the retrieved Wikipedia content
the model saw (visible in `<tool_calls>` and the model's `<evidence>` block).
A claim that is factually correct but not supported by retrieved content
fails groundedness — the model knew it from priors, not from grounding.

| Score | Criteria |
|---|---|
| 0 | Claims are not supported by retrieved content. |
| 1 | Mix of grounded and ungrounded claims. |
| 2 | Grounded with minor unsupported additions. |
| 3 | Fully grounded in retrieved content. |

## citation_quality

The system prompt requires inline `[Article Title]` bracket references
(no markdown link syntax) and a plain-text `Sources:` section at the end
of the answer with `Title - URL` per line. Score how cleanly the answer
follows this convention and whether citations attribute the right source.

| Score | Criteria |
|---|---|
| 0 | No citations, or citations attribute the wrong source. |
| 1 | Citations exist but are inconsistent or imprecise (wrong format, missing some claims, mismatched URLs). |
| 2 | Citations present and mostly correct; minor format issues. |
| 3 | Citations precise, complete, correctly attributed; format clean. |

## search_efficiency

Whether the agent's search behavior was appropriate for the question.
Refusal-to-search on questions Wikipedia genuinely cannot answer is fine
and does not penalize this dimension; that's calibration's territory.

| Score | Criteria |
|---|---|
| 0 | Excessive or irrelevant searches; or zero searches when the question demands one. |
| 1 | Wasteful searching pattern — unnecessary repeats, no refinement, or compound queries that should have been decomposed. |
| 2 | Reasonable but with some redundancy. |
| 3 | Minimal, well-targeted; each query advances the answer. |

## calibration

How well the model knows what it knows and reads the question. Three things
covered here, all soft-grouped into one dimension:

- Refuses (or hedges) when the question genuinely can't be answered from
  Wikipedia (too recent, opinion, not in any article).
- **Surfaces premise discrepancies descriptively.** When a question contains
  a wrong premise, the model should describe what Wikipedia indicates vs.
  what the premise assumed (e.g. "Wikipedia indicates X; the question's
  premise was Y") — not assertively correct the user ("you're wrong").
  The model is not the arbiter; making the disagreement legible is.
- Expresses uncertainty when warranted; doesn't over-commit to weak findings.

| Score | Criteria |
|---|---|
| 0 | Confidently wrong; refuses an appropriately-answerable question; OR accepts a false premise without surfacing it; OR assertively "corrects" the user (the model positioning itself as arbiter). |
| 1 | Calibration off in one direction — overconfident, or over-cautious. |
| 2 | Mostly calibrated; minor lapse. |
| 3 | Refuses when appropriate, surfaces premise discrepancies descriptively, expresses uncertainty when warranted. |
