# scripts/

Reproducibility scripts for eval runs and transcript export. Each script
is self-contained and runnable from the repo root via `uv run`.

## Eval runs

| Script | Purpose | Output |
|---|---|---|
| `eval_v1.py` | Re-run the v1 baseline against `tests/eval/cases/v1.yaml` | `eval_runs/v1_baseline_<UTC>/` |
| `eval_v1_1.py` | Re-run v1.1 (current production default) | `eval_runs/v1_1_<UTC>/` |
| `eval_v1_2.py` | Re-run v1.2 (preserved iteration artifact) | `eval_runs/v1_2_<UTC>/` |

Each script loads its specific prompt via the agent's `system_prompt=`
override, so the run is reproducible regardless of which prompt version
`src/wiki_qa/agent.py:_SYSTEM_PROMPT_PATH` currently points at.

Each run is ~5 min wall-clock at concurrency=3, ~$3 in Anthropic spend.
Per-iteration deltas are tracked in `tests/eval/iterations.md`.

## Transcript redaction

| Script | Purpose |
|---|---|
| `redact_transcripts.py` | Standalone redaction over Claude Code transcript HTML exports |

Per `DECISIONS.md` 2026-05-03 15:34: redaction runs at extract time, not
inline. Reads HTML/JSON files from a source directory, applies the
redaction patterns, writes the results to a destination directory. The
source is preserved untouched.

Patterns redacted:

- Anthropic API key bodies (`sk-ant-api03-` followed by a key body)
  → `[REDACTED_API_KEY]`
- The author's personal email (`jjanon@gmail.com` and bare-prefix
  fragments) → `[REDACTED_EMAIL]`
- `/Users/<username>/` absolute path prefix → `/Users/<user>/`

Patterns deliberately NOT redacted:

- The bare prefix `sk-ant-` (discussion of the key format, not a key).
- `noreply@anthropic.com` (public Anthropic email surface from
  co-author tags in commit messages).
- Wikipedia URLs and content (public).
- Repo paths (the repo is public; paths to repo files are not
  sensitive).
- The GitHub username `jjanon-git` (public, used in the repo URL).

Generating fresh transcripts (full workflow):

```bash
# 1. Concatenate session JSONLs in chronological order.
cat ~/.claude/projects/-Users-<user>-projects-wiki-q-and-a/*.jsonl \
  > transcripts/raw/combined.jsonl

# 2. Generate paginated HTML.
uvx claude-code-transcripts json transcripts/raw/combined.jsonl \
  -o transcripts/raw/combined-html \
  --repo jjanon-git/wiki-q-and-a

# 3. Redact.
uv run python scripts/redact_transcripts.py \
  --in transcripts/raw/combined-html \
  --out transcripts/redacted/combined-html

# 4. Commit `transcripts/redacted/`. The `transcripts/raw/` directory is
#    gitignored so unredacted output never lands on the remote.
```
