"""Judge calibration workflow.

Two-step round trip:

1. `sample` — given an `eval_runs/<run>/results.jsonl` and the dataset
   YAML it was scored against, stratify-sample N cases across rubric
   dimensions and score buckets. Write a read-only markdown view
   (`calibration.md`) and a fillable YAML scoring sheet
   (`calibration.scores.yaml`).

2. `analyze` — read `calibration.scores.yaml` (the user's filled-in
   scores). For each rubric dimension, compute agreement with the
   judge (`|human - judge| ≤ 1` = agree). Print a per-dimension
   pass-rate report and surface specific cases where you flagged the
   judge as off.

The convention writes `calibration.md` for human reading and
`calibration.scores.yaml` for human writing — separating reading from
writing keeps parsing strict (YAML) without sacrificing readability
(markdown). YAML for the human input rather than markdown round-trip
because parsing human-edited markdown is fragile (whitespace, partial
fills, score formats).
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from wiki_qa.eval.dataset import load_cases
from wiki_qa.eval.results import EvalResult
from wiki_qa.eval.schema import EvalCase

RUBRIC_DIMS: tuple[str, ...] = (
    "factual_accuracy",
    "groundedness",
    "citation_quality",
    "search_efficiency",
    "calibration",
)


# ---- 1. Sample selection ----------------------------------------------------


def sample_for_calibration(
    results: list[EvalResult],
    *,
    n: int = 8,
    rng: random.Random | None = None,
) -> list[EvalResult]:
    """Stratified sample for calibration review.

    Strategy: for each rubric dimension, pick one case where it scored
    low (≤1) and one where it scored high (=3). De-duplicate (a case
    may satisfy multiple buckets), then trim or pad to `n`.

    Skips judge_failure cases (nothing to calibrate against). Uses a
    seeded RNG so the same `--in` directory produces the same sample
    across invocations — important if you re-run `sample` after
    accidentally clobbering the YAML.
    """
    rng = rng or random.Random(42)
    valid = [r for r in results if r.judge_output is not None and not r.judge_output.judge_failure]
    if not valid:
        return []

    by_dim_score: dict[tuple[str, int], list[EvalResult]] = defaultdict(list)
    for r in valid:
        if r.judge_output is None:
            continue
        for d in r.judge_output.dimensions:
            if d.score is not None:
                by_dim_score[(d.name, d.score)].append(r)

    selected: dict[str, EvalResult] = {}

    for dim in RUBRIC_DIMS:
        # Low-score bucket: scores 0 or 1
        low_pool = by_dim_score.get((dim, 0), []) + by_dim_score.get((dim, 1), [])
        if low_pool:
            # Prefer cases not already selected
            fresh = [r for r in low_pool if r.case_id not in selected]
            choice = rng.choice(fresh if fresh else low_pool)
            selected[choice.case_id] = choice
        # High-score bucket: 3
        high_pool = by_dim_score.get((dim, 3), [])
        if high_pool:
            fresh = [r for r in high_pool if r.case_id not in selected]
            choice = rng.choice(fresh if fresh else high_pool)
            selected[choice.case_id] = choice

    # Trim if oversampled
    if len(selected) > n:
        keep_ids = rng.sample(sorted(selected), n)
        selected = {cid: selected[cid] for cid in keep_ids}

    # Pad with random cases if undersampled
    if len(selected) < n:
        pool = [r for r in valid if r.case_id not in selected]
        rng.shuffle(pool)
        for r in pool[: n - len(selected)]:
            selected[r.case_id] = r

    return sorted(selected.values(), key=lambda r: r.case_id)


# ---- 2. Render markdown view (read-only) ------------------------------------


def render_calibration_md(
    samples: list[EvalResult],
    cases_by_id: dict[str, EvalCase],
    run_dir: Path,
) -> str:
    """Render the human-facing markdown calibration view."""
    lines: list[str] = []
    lines.append("# Judge calibration sample")
    lines.append("")
    lines.append(f"Run: `{run_dir.name}`")
    lines.append(f"{len(samples)} cases sampled across rubric dimensions and score buckets.")
    lines.append("")
    lines.append(
        "**Read this file for context.** Fill in your scores 0-3 in "
        "`calibration.scores.yaml` (sibling file). Then run "
        "`python -m wiki_qa.eval calibrate analyze --in <run-dir>` to "
        "see per-dimension agreement with the judge."
    )
    lines.append("")

    for i, r in enumerate(samples, start=1):
        case = cases_by_id.get(r.case_id)
        gold = case.expected_answer if case else "(case not found in dataset)"

        lines.append("---")
        lines.append("")
        lines.append(f"## {i}. `{r.case_id}` — {r.category} ({r.difficulty})")
        lines.append("")
        lines.append(f"**Question:** {r.question}")
        lines.append("")
        lines.append("**Gold (expected_answer):**")
        lines.append("")
        lines.append("```")
        lines.append(gold.strip())
        lines.append("```")
        lines.append("")

        if r.agent_result is not None:
            ar = r.agent_result
            lines.append("**Model answer:**")
            lines.append("")
            lines.append("```")
            lines.append((ar.answer or "(empty)").strip())
            lines.append("```")
            lines.append("")
            if ar.tool_calls:
                lines.append(f"**Tool calls** ({ar.n_searches}):")
                for tc in ar.tool_calls:
                    lines.append(f"- `{tc.query}` ({tc.latency_ms}ms)")
                lines.append("")
            if ar.parse_warnings:
                codes = ", ".join(w.value for w in ar.parse_warnings)
                lines.append(f"**Parse warnings:** `{codes}`")
                lines.append("")

        if r.judge_output is not None:
            lines.append("### Judge scores")
            lines.append("")
            lines.append("| Dimension | Score | Reasoning |")
            lines.append("|---|---|---|")
            for d in r.judge_output.dimensions:
                reasoning = (d.reasoning or "").replace("\n", " ").replace("|", r"\|")
                if len(reasoning) > 220:
                    reasoning = reasoning[:217] + "..."
                lines.append(
                    f"| `{d.name}` | {d.score if d.score is not None else '—'} | {reasoning} |"
                )
            lines.append("")

    return "\n".join(lines) + "\n"


# ---- 3. Render YAML scoring sheet (human writes) ----------------------------


def render_scores_yaml(samples: list[EvalResult]) -> str:
    """Render the human-fillable YAML scoring sheet.

    Pre-populates the judge scores; the `human` section is null-valued
    for the human to fill. `notes` is freeform.
    """
    out: list[dict[str, Any]] = []
    for r in samples:
        judge_scores: dict[str, int | None] = dict.fromkeys(RUBRIC_DIMS)
        if r.judge_output is not None:
            for d in r.judge_output.dimensions:
                judge_scores[d.name] = d.score
        human_scores: dict[str, int | None] = dict.fromkeys(RUBRIC_DIMS)
        out.append(
            {
                "case_id": r.case_id,
                "category": r.category,
                "judge": judge_scores,
                "human": human_scores,
                "notes": "",
            }
        )

    header = (
        "# Calibration scoring sheet — fill in the `human` block per case.\n"
        "# Scores are 0-3 (matching the rubric). Leave at null to skip a dim.\n"
        "# `notes`: freeform — what you saw differently from the judge.\n"
        "# Run `python -m wiki_qa.eval calibrate analyze --in <run-dir>` after\n"
        "# filling to see per-dim agreement.\n\n"
    )
    body = yaml.safe_dump(
        {"samples": out},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    return header + body


# ---- 4. Analysis (after the human fills the YAML) ---------------------------


@dataclass(frozen=True)
class DimensionAgreement:
    name: str
    agree: int
    disagree: int
    skipped: int
    disagreements: list[dict[str, Any]]

    @property
    def applicable(self) -> int:
        return self.agree + self.disagree

    @property
    def agree_rate(self) -> float | None:
        return self.agree / self.applicable if self.applicable else None


@dataclass(frozen=True)
class CalibrationReport:
    per_dim: dict[str, DimensionAgreement]
    n_samples: int


# Threshold: if more than this fraction of judgments disagree on a dim,
# treat it as a calibration concern worth investigating. From the design
# discussion (DECISIONS 2026-05-03 15:34): 25%.
DISAGREEMENT_THRESHOLD: float = 0.25


def analyze_scores(scores_yaml: str) -> CalibrationReport:
    """Compute per-dimension human↔judge agreement.

    Definition of agreement: `|human - judge| ≤ 1`. The rubric is on a
    0-3 scale so allowing ±1 captures "we basically agreed" without
    requiring exact-match. Larger-than-1 deltas are flagged in the
    disagreement detail list with the case_id, both scores, and any
    note the human left.
    """
    data = yaml.safe_load(scores_yaml) or {}
    samples = data.get("samples", []) or []

    per_dim: dict[str, dict[str, Any]] = {
        dim: {"agree": 0, "disagree": 0, "skipped": 0, "disagreements": []} for dim in RUBRIC_DIMS
    }

    for s in samples:
        case_id = s.get("case_id", "?")
        judge = s.get("judge") or {}
        human = s.get("human") or {}
        notes = s.get("notes", "")
        for dim in RUBRIC_DIMS:
            j_raw = judge.get(dim)
            h_raw = human.get(dim)
            if h_raw is None or j_raw is None:
                per_dim[dim]["skipped"] += 1
                continue
            try:
                j_score = int(j_raw)
                h_score = int(h_raw)
            except (TypeError, ValueError):
                per_dim[dim]["skipped"] += 1
                continue
            if abs(h_score - j_score) <= 1:
                per_dim[dim]["agree"] += 1
            else:
                per_dim[dim]["disagree"] += 1
                per_dim[dim]["disagreements"].append(
                    {
                        "case_id": case_id,
                        "human": h_score,
                        "judge": j_score,
                        "delta": h_score - j_score,
                        "note": notes,
                    }
                )

    return CalibrationReport(
        per_dim={dim: DimensionAgreement(name=dim, **counts) for dim, counts in per_dim.items()},
        n_samples=len(samples),
    )


def format_report(report: CalibrationReport) -> str:
    """Format a CalibrationReport for terminal display."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"Calibration analysis ({report.n_samples} cases x {len(RUBRIC_DIMS)} dims)")
    lines.append("=" * 70)
    lines.append("")
    lines.append(
        f"{'dimension':<22}{'agree':>8}{'disagree':>10}{'skipped':>10}{'rate':>10}{'flag':>10}"
    )
    lines.append("-" * 70)
    for dim in RUBRIC_DIMS:
        a = report.per_dim[dim]
        rate_str = f"{a.agree_rate:.0%}" if a.agree_rate is not None else "—"
        if a.applicable == 0:
            flag = "(no data)"
        elif (a.disagree / a.applicable) > DISAGREEMENT_THRESHOLD:
            flag = "⚠ FLAG"
        else:
            flag = "ok"
        lines.append(
            f"{dim:<22}{a.agree:>8}{a.disagree:>10}{a.skipped:>10}{rate_str:>10}{flag:>10}"
        )

    # Disagreement details
    flagged = [a for a in report.per_dim.values() if a.disagreements]
    if flagged:
        lines.append("")
        lines.append("Disagreements (per dim, where |human - judge| > 1):")
        for a in flagged:
            if not a.disagreements:
                continue
            lines.append(f"\n  {a.name}:")
            for d in a.disagreements:
                note = f" -- {d['note']}" if d.get("note") else ""
                lines.append(
                    f"    {d['case_id']:<32}  human={d['human']}  "
                    f"judge={d['judge']}  delta={d['delta']:+d}{note}"
                )
    else:
        lines.append("")
        lines.append(
            "No disagreements with |Δ| > 1. Judge appears calibrated against your reading."
        )

    threshold_pct = int(DISAGREEMENT_THRESHOLD * 100)
    flagged_dims = [
        dim
        for dim, a in report.per_dim.items()
        if a.applicable > 0 and (a.disagree / a.applicable) > DISAGREEMENT_THRESHOLD
    ]
    if flagged_dims:
        lines.append("")
        lines.append(
            f"⚠ {len(flagged_dims)} dimension(s) above {threshold_pct}% disagreement: "
            f"{', '.join(flagged_dims)}. Per the design rule, this is signal to "
            "revise the rubric or judge prompt for that dimension."
        )

    return "\n".join(lines) + "\n"


# ---- 5. Glue: load results from disk ---------------------------------------


def load_results_jsonl(path: Path) -> list[EvalResult]:
    """Read `results.jsonl` and round-trip into EvalResult instances."""
    out: list[EvalResult] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        out.append(EvalResult.model_validate_json(line))
    return out


def load_cases_by_id(case_glob: str) -> dict[str, EvalCase]:
    """Load all cases matching `case_glob` and index by case_id."""
    return {c.id: c for c in load_cases(case_glob)}


def write_calibration_artifacts(
    samples: list[EvalResult],
    cases_by_id: dict[str, EvalCase],
    run_dir: Path,
) -> tuple[Path, Path]:
    """Write `calibration.md` and `calibration.scores.yaml` into `run_dir`.

    Returns the two paths.
    """
    md_path = run_dir / "calibration.md"
    yaml_path = run_dir / "calibration.scores.yaml"
    md_path.write_text(render_calibration_md(samples, cases_by_id, run_dir))
    yaml_path.write_text(render_scores_yaml(samples))
    return md_path, yaml_path
