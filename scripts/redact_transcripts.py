"""Standalone redaction script for Claude Code transcript exports.

Per DECISIONS 2026-05-03 15:34: redaction runs at extract time, not inline.
This script is auditable and re-runnable. Reads HTML/JSON files from a
source directory, applies the redaction patterns, writes the results to a
destination directory. The source directory is preserved untouched.

Patterns redacted:
- Anthropic API keys (`sk-ant-api03-` followed by a key body) → `[REDACTED_API_KEY]`
- Personal email address → `[REDACTED_EMAIL]`
- `/Users/<username>/` absolute path prefix → `/Users/<user>/`

Patterns deliberately NOT redacted:
- The bare prefix `sk-ant-` or `sk-ant-api03` (discussion of the key format,
  not a key itself).
- `noreply@anthropic.com` (a public Anthropic email surface from co-author
  tags in commit messages).
- Wikipedia URLs and content (public).
- Repo-relative paths (the repo is public, paths are not sensitive).

Usage:
    uv run python scripts/redact_transcripts.py \\
        --in transcripts/raw/<dir> \\
        --out transcripts/redacted/<dir>

If `--user-email` is given, additional emails are redacted. Multiple
allowed.
"""

from __future__ import annotations

import argparse
import re
import shutil
from collections import Counter
from pathlib import Path

# Real Anthropic API keys: sk-ant-api03- followed by a long key body
# (typically ~95 chars). The minimum body length here (8) is conservative —
# anything that long after the prefix is almost certainly a real key.
_API_KEY_PATTERN = re.compile(r"sk-ant-api[0-9]+-[A-Za-z0-9_\-]{8,}")
_USER_PATH_PATTERN = re.compile(r"/Users/[a-zA-Z][a-zA-Z0-9_\-]*/")

# Emails always redacted (the personal one) plus any user-provided extras.
_DEFAULT_REDACT_EMAILS: tuple[str, ...] = ("jjanon@gmail.com",)


def build_redactor(extra_emails: list[str] | None = None) -> tuple[
    list[tuple[re.Pattern[str], str]], Counter[str]
]:
    """Compile the redaction rules. Returns (rules, counter).

    Each rule is (pattern, replacement). Order matters — earlier rules
    consume their matches before later rules see the text. We redact full
    emails first, then catch bare email-prefix fragments (which can appear
    when an email got HTML-split across tags, or — meta — when an earlier
    grep-for-the-email command got logged into the transcript).

    The counter accumulates per-rule redaction counts so the caller can
    report stats.
    """
    emails = list(_DEFAULT_REDACT_EMAILS) + list(extra_emails or [])
    rules: list[tuple[re.Pattern[str], str]] = [
        (_API_KEY_PATTERN, "[REDACTED_API_KEY]"),
        (_USER_PATH_PATTERN, "/Users/<user>/"),
    ]
    # Pass 1: full email matches
    for email in emails:
        rules.append((re.compile(re.escape(email)), "[REDACTED_EMAIL]"))
    # Pass 2: bare prefix (`local-part@` with nothing after, or with HTML
    # entities, closing tags, quotes). Catches fragments left over after
    # pass 1 fails to match the full email — typically because the email
    # got HTML-split or only the prefix appears.
    for email in emails:
        local_part = email.split("@", 1)[0]
        rules.append(
            (re.compile(re.escape(local_part) + r"@"), "[REDACTED_EMAIL]@")
        )
    return rules, Counter()


def redact_text(
    text: str,
    rules: list[tuple[re.Pattern[str], str]],
    counter: Counter[str],
) -> str:
    """Apply each rule to `text`. Updates `counter` with per-rule counts."""
    out = text
    for pattern, replacement in rules:
        new_out, n = pattern.subn(replacement, out)
        if n:
            counter[replacement] += n
        out = new_out
    return out


def redact_file(src: Path, dst: Path, rules: list, counter: Counter[str]) -> None:
    """Read src, redact, write to dst. Binary files are copied as-is."""
    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Binary or non-UTF-8: copy unchanged (favicon, images, etc.)
        shutil.copy2(src, dst)
        return
    redacted = redact_text(text, rules, counter)
    dst.write_text(redacted, encoding="utf-8")


def redact_directory(
    src_dir: Path,
    dst_dir: Path,
    extra_emails: list[str] | None = None,
) -> Counter[str]:
    """Recursively redact every file in src_dir to dst_dir.

    Returns the per-rule replacement counter so callers can report stats.
    """
    rules, counter = build_redactor(extra_emails)
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)

    for src_path in src_dir.rglob("*"):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(src_dir)
        dst_path = dst_dir / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        redact_file(src_path, dst_path, rules, counter)

    return counter


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Redact secrets from Claude Code transcript HTML exports.",
    )
    parser.add_argument(
        "--in", dest="src", required=True, type=Path,
        help="Source directory (transcript HTML/JSON files). Untouched.",
    )
    parser.add_argument(
        "--out", dest="dst", required=True, type=Path,
        help="Destination directory for redacted output. Will be wiped if exists.",
    )
    parser.add_argument(
        "--user-email", action="append", default=[],
        help=(
            "Additional email address to redact. May be passed multiple times. "
            f"jjanon@gmail.com is always redacted by default."
        ),
    )
    args = parser.parse_args()

    if not args.src.is_dir():
        raise SystemExit(f"--in is not a directory: {args.src}")

    counter = redact_directory(args.src, args.dst, extra_emails=args.user_email)

    print(f"Redacted {sum(1 for _ in args.dst.rglob('*') if _.is_file())} files "
          f"from {args.src} → {args.dst}")
    if not counter:
        print("No redactions applied (no secrets matched).")
        return
    print("\nRedactions applied:")
    for replacement, n in sorted(counter.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {replacement}")


if __name__ == "__main__":
    main()
