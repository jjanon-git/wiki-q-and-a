"""Interactive CLI for asking the Wikipedia QA agent a question.

Entry point: `python -m wiki_qa "<question>"` or `wiki-qa "<question>"`
(installed from pyproject's `[project.scripts]`).

The CLI is intentionally thin — it loads `.env`, calls `agent.answer()`,
and prints the result. All the substantive behavior lives in the agent.
Live progress events from the agent are mirrored to stderr while the
formatted answer goes to stdout, so piping (`> out.txt`) captures the
answer cleanly.
"""

from __future__ import annotations

import sys
from typing import Any

import click
from dotenv import load_dotenv

from wiki_qa.agent_contract import AgentResult


def _print_progress(event: Any) -> None:
    """Render a single ProgressEvent to stderr.

    Glyphs are deliberate: → for outbound, ← for inbound, · for in-flight
    (model thinking), ✏ for answer composition, ✓ for terminal. Single-line
    per event so the trace stays scannable across multiple turns.
    """
    kind = event.kind
    iteration = event.iteration
    if kind == "iteration_start":
        # max_iterations is on the event for any future caller that wants
        # the budget cap, but the CLI deliberately doesn't display it —
        # most questions finish in 2 turns and the cap doesn't help users.
        click.echo(f"  · turn {iteration} — consulting Claude", err=True)
    elif kind == "search_start":
        click.echo(f"  → searching: {event.query!r}", err=True)
    elif kind == "search_done":
        if event.error:
            click.echo(f"  ← search error: {event.error}", err=True)
        else:
            click.echo(
                f"  ← {event.n_results} result(s) in {event.latency_ms}ms",
                err=True,
            )
    elif kind == "composing_answer":
        click.echo("  ✏ composing answer (no further searches)", err=True)
    elif kind == "complete":
        click.echo(f"  ✓ done ({event.stop_reason})", err=True)


# A small, deliberately diverse sample for `--demo`. One case per
# representative category — fast enough to run as a smoke test, broad
# enough to show the agent's range. Each demo question is intentionally
# different from any worked example in the system prompt so we're testing
# the model's actual behavior, not its ability to regurgitate.
DEMO_QUESTIONS: tuple[str, ...] = (
    "When was the Battle of Hastings?",  # simple_factual
    "Which is taller, K2 or Kangchenjunga?",  # multi_source
    "When did Magellan complete the first circumnavigation of the globe?",  # false_premise
    "What is 4738 multiplied by 271?",  # negative_capability (= 1,283,998)
)


def format_result(result: AgentResult, *, verbose: bool = False) -> str:
    """Render an AgentResult as a human-readable CLI block.

    Always shows: question, parsed answer (or raw_output fallback), search
    count + queries with latency. With `verbose=True`, also shows token
    usage and stop reason. Parse warnings are shown when present so the
    user sees structural issues without having to grep raw output.
    """
    bar = "=" * 70
    sep = "-" * 70
    lines: list[str] = [bar, f"Q: {result.question}", bar]

    if result.answer:
        lines.append(result.answer)
    elif result.raw_output:
        lines.append("(no parsed answer; showing raw model output:)")
        lines.append("")
        lines.append(result.raw_output)
    else:
        lines.append("(no answer produced)")

    lines.extend(["", sep, f"Searches: {result.n_searches}"])
    for i, tc in enumerate(result.tool_calls, start=1):
        lines.append(f"  {i}. {tc.query!r}  ({tc.latency_ms}ms)")

    if result.parse_warnings:
        codes = [w.value for w in result.parse_warnings]
        lines.append(f"Parse warnings: {codes}")

    if verbose:
        u = result.usage
        lines.append(
            f"Tokens: {u.input_tokens} in / {u.output_tokens} out  "
            f"(cache_read={u.cache_read_tokens}, cache_creation={u.cache_creation_tokens})"
        )
        lines.append(f"Stop reason: {result.stop_reason}")

    return "\n".join(lines)


def _ask_one(question: str, *, verbose: bool, agent_fn: Any | None = None) -> AgentResult:
    """Dispatch a single question to the agent. `agent_fn` is injectable for tests.

    Production calls pass `progress=_print_progress` so the user sees live
    activity while the agent works. Tests inject their own agent_fn that
    ignores progress.
    """
    if agent_fn is None:
        from wiki_qa.agent import answer as agent_answer

        result: AgentResult = agent_answer(question, progress=_print_progress)
    else:
        result = agent_fn(question)
    return result


@click.command()
@click.argument("question", required=False)
@click.option(
    "--demo",
    is_flag=True,
    help=f"Run {len(DEMO_QUESTIONS)} sample questions across representative categories.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show token usage and stop reason in addition to answer + search trace.",
)
def main(question: str | None, demo: bool, verbose: bool) -> None:
    """Ask a question and get a Wikipedia-grounded answer.

    Examples:
        python -m wiki_qa "When was the Battle of Hastings?"
        python -m wiki_qa --demo
        python -m wiki_qa -v "Which is taller, K2 or Kangchenjunga?"
    """
    load_dotenv()

    if demo:
        click.echo(f"Running demo: {len(DEMO_QUESTIONS)} sample questions.")
        click.echo("Each runs the real Anthropic API and live MediaWiki. Expect ~30-60s total.")
        click.echo("")
        for i, q in enumerate(DEMO_QUESTIONS, start=1):
            click.echo(f"\n[{i}/{len(DEMO_QUESTIONS)}] {q}")
            try:
                result = _ask_one(q, verbose=verbose)
                click.echo(format_result(result, verbose=verbose))
            except Exception as e:
                click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        return

    if not question:
        click.echo(
            'Usage: python -m wiki_qa "<question>"  (or --demo)',
            err=True,
        )
        sys.exit(2)

    result = _ask_one(question, verbose=verbose)
    click.echo(format_result(result, verbose=verbose))


if __name__ == "__main__":
    main()
