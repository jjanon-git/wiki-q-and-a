"""CLI entry: `python -m wiki_qa.eval [run|calibrate sample|calibrate analyze]`.

Defaults pick the v1 cases glob and a timestamped output directory under
`eval_runs/`, so a bare `python -m wiki_qa.eval run` produces a fresh run.

`calibrate sample --in <run-dir>` writes a markdown view + YAML scoring
sheet stratified across rubric dimensions and score buckets. After the
human fills the YAML, `calibrate analyze --in <run-dir>` reports per-
dimension agreement.

Exit code: 0 if all cases produced an `ok` result; 1 if any case errored.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from wiki_qa.eval.calibration import (
    analyze_scores,
    format_report,
    load_cases_by_id,
    load_results_jsonl,
    sample_for_calibration,
    write_calibration_artifacts,
)
from wiki_qa.eval.dataset import load_cases
from wiki_qa.eval.runner import DEFAULT_CONCURRENCY, run

_DEFAULT_CASES_GLOB = "tests/eval/cases/*.yaml"
_DEFAULT_RUNS_ROOT = "eval_runs"


@click.group()
def cli() -> None:
    """Eval harness CLI."""


@cli.command("run")
@click.option(
    "--cases",
    "cases_pattern",
    default=_DEFAULT_CASES_GLOB,
    show_default=True,
    help="Glob pattern for case YAML files.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=("Output directory for results.jsonl. Default: eval_runs/<UTC-timestamp>/."),
)
@click.option(
    "--concurrency",
    type=int,
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Max in-flight cases. 1 = serial.",
)
@click.option(
    "--judge/--no-judge",
    "judge_enabled",
    default=True,
    show_default=True,
    help=(
        "Run the LLM judge after behavior checks. --no-judge produces "
        "checks-only results without making Anthropic API calls."
    ),
)
def run_cmd(
    cases_pattern: str,
    out_dir: Path | None,
    concurrency: int,
    judge_enabled: bool,
) -> None:
    cases = load_cases(cases_pattern)
    if not cases:
        click.echo(f"No cases matched pattern: {cases_pattern}", err=True)
        sys.exit(2)

    target = out_dir if out_dir is not None else _default_out_dir()

    click.echo(f"Loaded {len(cases)} case(s) from {cases_pattern}")
    click.echo(f"Concurrency: {concurrency}")
    click.echo(f"Judge:       {'enabled' if judge_enabled else 'disabled'}")
    click.echo(f"Writing to:  {target}")

    report = run(
        cases,
        concurrency=concurrency,
        judge_enabled=judge_enabled,
        out_dir=target,
    )

    click.echo("")
    click.echo(f"Ran {report.total} case(s): {report.ok} ok, {report.errors} error(s).")
    click.echo(f"Results: {target / 'results.jsonl'}")

    sys.exit(0 if report.errors == 0 else 1)


def _default_out_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    return Path(_DEFAULT_RUNS_ROOT) / stamp


@cli.group("calibrate")
def calibrate_grp() -> None:
    """Judge calibration round-trip: stratify-sample, score, analyze."""


@calibrate_grp.command("sample")
@click.option(
    "--in",
    "run_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Run directory containing results.jsonl (e.g. eval_runs/v1_baseline_...).",
)
@click.option(
    "--cases",
    "cases_pattern",
    default=_DEFAULT_CASES_GLOB,
    show_default=True,
    help="Glob for the dataset that produced this run (needed for gold answers).",
)
@click.option(
    "--n",
    "n_samples",
    type=int,
    default=8,
    show_default=True,
    help="Number of cases to sample for calibration review.",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    show_default=True,
    help="RNG seed for reproducible sample selection.",
)
def calibrate_sample(run_dir: Path, cases_pattern: str, n_samples: int, seed: int) -> None:
    """Stratify-sample N cases and write calibration.md + calibration.scores.yaml."""
    import random

    results_path = run_dir / "results.jsonl"
    if not results_path.exists():
        click.echo(f"No results.jsonl in {run_dir}", err=True)
        sys.exit(2)

    results = load_results_jsonl(results_path)
    cases_by_id = load_cases_by_id(cases_pattern)

    samples = sample_for_calibration(results, n=n_samples, rng=random.Random(seed))
    if not samples:
        click.echo("No valid (non-judge_failure) results to sample from.", err=True)
        sys.exit(2)

    md_path, yaml_path = write_calibration_artifacts(samples, cases_by_id, run_dir)

    click.echo(f"Sampled {len(samples)} case(s) from {len(results)} total.")
    click.echo(f"  Read:  {md_path}")
    click.echo(f"  Score: {yaml_path}")
    click.echo("")
    click.echo("Fill in the `human` blocks in the YAML, then run:")
    click.echo(f"  python -m wiki_qa.eval calibrate analyze --in {run_dir}")


@calibrate_grp.command("analyze")
@click.option(
    "--in",
    "run_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Run directory containing calibration.scores.yaml.",
)
def calibrate_analyze(run_dir: Path) -> None:
    """Read filled calibration.scores.yaml and print per-dim agreement."""
    yaml_path = run_dir / "calibration.scores.yaml"
    if not yaml_path.exists():
        click.echo(
            f"No calibration.scores.yaml in {run_dir}. "
            f"Run `calibrate sample --in {run_dir}` first.",
            err=True,
        )
        sys.exit(2)

    report = analyze_scores(yaml_path.read_text())
    click.echo(format_report(report))


if __name__ == "__main__":
    cli()
