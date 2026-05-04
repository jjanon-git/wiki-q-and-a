"""CLI entry: `python -m wiki_qa.eval run [options]`.

v1 scope: the `run` subcommand only. `calibrate` and the judge integration
are separate later passes.

Defaults pick the dev cases glob and a timestamped output directory under
`eval_runs/`, so a bare `python -m wiki_qa.eval run` produces a fresh run
from the placeholder cases.

Exit code: 0 if all cases produced an `ok` result; 1 if any case errored.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import click

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


if __name__ == "__main__":
    cli()
