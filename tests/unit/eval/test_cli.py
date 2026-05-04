"""Smoke tests for the eval CLI.

CliRunner only — no real API calls. The runner uses agent_stub.answer
by default, which is fixture-driven (see tests/eval/fixtures/agent_outputs.yaml).
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from wiki_qa.eval.__main__ import cli


def test_run_command_against_dev_fixture_no_judge(tmp_path: Path) -> None:
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--cases",
            "tests/eval/cases/dev.yaml",
            "--out",
            str(out),
            "--concurrency",
            "1",
            "--no-judge",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "3 case(s)" in result.output
    assert "0 error(s)" in result.output
    assert "Judge:       disabled" in result.output
    assert (out / "results.jsonl").exists()
    lines = (out / "results.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_run_command_exits_2_on_no_matching_cases(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--cases", str(tmp_path / "nonexistent_*.yaml")])
    assert result.exit_code == 2
    assert "No cases matched" in result.output
