# DECISIONS

Append-only chronological log. Newest entries at the bottom.

## 2026-05-03 15:02 — Stack: Python + ruff + mypy (strict) + pytest

Python for the SDK and tooling familiarity. `ruff` for format+lint, `mypy --strict` for types, `pytest` for tests. Alternatives (TypeScript, Go) considered but Python's `anthropic` SDK and the user's existing convention base in CLAUDE.md make Python the lower-friction pick for a 1-2 hour build.

## 2026-05-03 15:02 — TDD strict for system code

Red-green-refactor for agent loop, tool integration, and parsing. Eval-harness code uses judgment — write tests when clarifying, skip when the harness's outputs are themselves the validation. Reasoning: system code has well-defined behavior worth pinning; eval harness is more exploratory and the test would often duplicate the harness.

## 2026-05-03 15:02 — Single tool: `search_wikipedia(query: str)` for v1

The brief specifies `search_wikipedia(query: str)`. Sticking to a single tool for v1. Alternative considered: add `fetch_wikipedia_article(title)` so search returns lightweight hits and the model drills in selectively. Deferred — single tool is the literal spec, and we want eval evidence before expanding the surface. Constraint this creates: with one tool the agent loop is search → reason → optionally re-search with refined query, so the return shape needs to support iterative refinement.

## 2026-05-03 15:02 — v1 search return shape: top-3 results, title + ~500-char extract + URL

Picked because the single-tool constraint rules out drill-down via a second tool. Top-N with full-ish extracts gives the model enough to answer common questions in one call while still allowing re-search with a refined query for harder cases. Alternatives: titles+snippets only (forces re-search even for easy cases — wastes turns), full articles for top-N (context bloat). N=3 and ~500 chars are starting points; expect to iterate based on eval results.

## 2026-05-03 15:02 — Models: Sonnet 4.6 for agent, Haiku 4.5 for judge

Sonnet 4.6 for the agent — strong tool-use and reasoning at reasonable cost; this is the workload the brief is designed around. Haiku 4.5 for the LLM-as-judge — judging is a narrower task (compare answer to rubric+ground truth), so cheaper/faster is appropriate. We can compare Haiku vs Sonnet judges later if calibration is suspect.

## 2026-05-03 15:02 — Eval set: hand-written, 20-30 cases across deliberate failure modes

Dimensions: simple-factual, multi-hop, disambiguation, false-premise, unanswerable, temporal. Alternative considered: pull from HotpotQA/TriviaQA/SimpleQA. Hand-written wins on signal density — most public-dataset cases are easy and don't stress the failure modes that make the prompt-engineering interesting. Cost: ~30-45 min to build, but those cases double as the demo.

## 2026-05-03 15:02 — Two parallel workstreams

Workstream A (main): scaffolding → agent contract → wikipedia integration → tool definition → v1 prompt → CLI. Workstream B (subagent, kicked off after contract is defined): hand-written dataset, judge prompt, eval harness scaffolded against contract. Risk: subagent codes against a contract that drifts. Mitigation: define the contract first and treat it as frozen until v1 eval is green.

## 2026-05-03 15:02 — Iteration plan: at least 2 cycles

Build v1 prompt → run full eval → identify failure modes → revise prompt (and possibly tool return shape) → re-run → record delta. Reasoning: the brief explicitly asks "key iterations you made based on eval results" — a single pass doesn't answer that.

## 2026-05-03 15:02 — DECISIONS.md convention adopted

Append-only chronological log; alternatives required when realistic ones exist; entries written before continuing on pushback/course-correction; reversed decisions get a superseding entry rather than edits. Reason: the design-rationale deliverable is far easier to write from a live log than reconstructed from memory.

## 2026-05-03 15:05 — Models: Opus 4.7 for both agent and judge (supersedes 15:02 model decision)

Reversing the earlier "Sonnet for agent, Haiku for judge" call. New principle: start with the strongest model across the board to establish a quality ceiling, then downgrade with eval evidence. Cost isn't a real constraint at this scale (a few hundred API calls total). Same principle applied to the judge — using Sonnet there would conflate "judge model too weak" with "rubric weak" when something looks off. The eval-driven downgrade story (Opus → Sonnet → Haiku, with measured deltas) is itself a useful artifact for the writeup.

## 2026-05-03 15:19 — Tool design v1 revised on user pushback (supersedes 15:02 search-shape decision in part)

Five revisions to the v1 tool definition and Wikipedia integration, all driven by user pushback on `plans/search_wikipedia.md`:

1. **Tool description rewritten from narrow ("when you need factual info you don't reliably know") to default-on ("use by default for any question that benefits from grounding; exceptions are arithmetic, code, opinion, conversational content").** Reasoning: the entire point of the system is Wikipedia grounding; a permissive description leaves too much room for the model to skip search and answer from priors.

2. **Removed entity-only framing in the query parameter description.** Original implied queries should be "2-6 keywords identifying the entity, event, or concept," which would have steered the agent wrong on synthesis questions like "impact of the Gulf War on oil prices." New guidance: favor specific noun phrases over conversational questions, and for multi-part questions decompose into separate calls (one per facet).

3. **Strengthened re-search guidance.** Original "you may call this tool again with a refined query" was too soft. New language requires the model to assess results and explicitly handles three branches: refine query, decompose to a different facet, or admit Wikipedia doesn't have the answer rather than guess. Direct response to user concern that Opus 4.7 specifically needs firmer guidance here.

4. **Extract length increased from 500 to 2000 chars.** New principle: optimize for sufficient context first, cut down only if eval evidence shows context bloat is hurting more than under-context is helping. Wrong answers (the under-context failure mode) are more expensive in evaluation than token cost (the over-context failure mode). Added `truncated="true"` attribute when MediaWiki cuts off the lead, so the model has explicit signal it could re-search for more.

5. **Tool result format changed from plain formatted text to XML.** Anthropic models attend well to XML structure; XML also gives natural per-result references and clean error/zero-result variants. Extracts HTML-escaped to handle stray `<`, `>`, `&`.

6. **429 handling added to v1.** Originally deferred to "no retry/backoff in v1." User flagged separately; respecting `Retry-After` and using 1s/2s/4s exponential backoff (max 3 retries) is cheap politeness to the API and avoids spurious eval failures.

## 2026-05-03 15:34 — Eval harness: rubric, judge structure, dataset format, calibration

Multiple settled items batched (all from one round of user feedback):

- **Package manager**: `uv`. Modern, fast, single tool.
- **MediaWiki UA**: placeholder, no email in committed code.
- **Agent contract telemetry**: `n_searches`, `queries`, per-call `ToolCall` (query, raw_result_xml, latency_ms), `usage` (token counts). Captured for both correctness scoring and efficiency/iteration scoring.
- **Rubric**: per-dimension 0-3 scores, judge required to cite evidence in reasoning, no aggregation. Per-dim scores stay visible in output so failures aren't obscured by an average.
- **Rubric dimensions (v1)**: factual_accuracy, groundedness, citation_quality, search_efficiency, calibration. Groundedness added per user request — distinct from accuracy: an answer can be factually correct but ungrounded (model knew it from priors, not retrieved content) — that's a fail on groundedness, pass on accuracy.
- **Judge prompt structure**: question, gold answer, model answer, tool-call trace, rubric → judge outputs `<evaluation>` with one `<dimension name="...">` per dim, each containing `<reasoning>` then `<score>`. Reasoning before score per Anthropic guidance: confirmed via prompt-engineering docs ("use structured tags like `<thinking>` and `<answer>` to cleanly separate reasoning from final output") and LLM-as-judge guidance ("ask the LLM to think first before deciding an evaluation score").
- **Dataset format**: YAML, one file or one-per-category (TBD). Each case has `id`, `category`, `difficulty` (1-5), `question`, `expected_answer`, `expected_behavior`, `notes`. YAML chosen over JSON because comments are valuable for documenting why each case exists.
- **Citation pattern (preliminary)**: inline attribution by article title with URLs collated at the end. Driven by user customer-feedback experience. Final form ties to system prompt direction (still pending).
- **Transcript redaction**: standalone script, runs at extract time, never inline. Patterns: `sk-ant-*` keys, email addresses, `/Users/<username>/` path prefixes. Auditable and re-runnable.

## 2026-05-03 15:34 — Judge calibration plan

User will manually inspect 5-10 cases stratified across dimensions and judge-score buckets to validate the LLM-judge against their own judgment. Acknowledged limitation: not an SME, but the alternative (no calibration at all) is worse. If per-dim disagreement rate exceeds ~25%, that's signal to revise the rubric or judge prompt. v1 implementation: dump sampled cases to a markdown file user fills in by hand; only build interactive flow if judge clearly needs ongoing adjustment.

## 2026-05-03 15:44 — Eval-set design philosophy

The eval set is small (~32 cases across 12 categories) by design. The goal is to **stress-test specific failure modes deterministically**, not to maximize topical coverage. Each case is a hypothesis about a failure mode the system might exhibit; the category structure is our taxonomy of failure modes; difficulty levels (easy/medium/hard) tier cases by how clearly they should pass. A larger random-sampled dataset would give better statistical estimates of average performance but worse signal on what to fix. Since we're iterating on prompts, signal-on-failures beats breadth.

Difficulty tiers: easy = must-pass baselines (failure indicates something fundamental broken); medium = typical case where most rubric judgment happens; hard = stress tests (failure expected to some degree, tells us where the ceiling is). Three tiers chosen over 1-5 because at this set size 1-5 yields ~5 cases per tier — too thin to spot per-tier patterns.

## 2026-05-03 15:44 — Eval-set categories revised on user pushback

Multiple revisions to the category taxonomy from earlier round:

- **Collapsed `comparative` + `synthesis` into `multi_source`.** User correctly pointed out the original split (two entities vs more-than-two) didn't change agent behavior. Cleaner cut: sequential reasoning (`multi_hop`) vs parallel reasoning (`multi_source`); comparative and synthesis both live in the latter.
- **Increased `multi_hop` weight from 3-4 to 5-6.** It's the category that exercises the most distinct system behaviors per case (decomposition, refinement, intermediate context, termination). User explicitly weighted it up.
- **Split `disambiguation` into `disambiguation_explicit` (WP returns disambig page) and `disambiguation_default_sense` (WP returns most-common sense, may be wrong).** Two genuinely different failure modes. Multi-turn clarification (a third related behavior) treated as an architectural decision, not a category — see deferral below.
- **Split `negative` into `negative_capability` (math/code/opinion — wrong tool) and `negative_policy` (legal/medical/financial advice — could "answer" but shouldn't).** Capability cases included in v1; policy cases deferred (out of scope for take-home, mentioned in writeup as future work).
- **Added `buried_answer` category** (e.g., "What did Bezos originally want to call Amazon?" — Cadabra, deep in Amazon article history). Tests refinement when the lead extract doesn't have the answer. Diagnostic for tool design: repeated failures here = signal to add `fetch_wikipedia_article`.
- **Split `unanswerable` into three** (`unanswerable_not_in_wp`, `unanswerable_too_recent`, `unanswerable_subjective`) and increased weight from 2 to 7 cases. User: "this makes-or-breaks customer trust." Each subtype demands a different right-behavior (refuse cleanly vs cutoff caveat vs identify as opinion).
- **Multi-turn deferred to v2.** v1 is single-turn (matches brief's "takes a question and returns an answer"). Multi-turn enables clarifying-question disambiguation but materially complicates agent loop and eval. Documented as "how I'd extend this" in writeup.

Total moved from ~24-31 to ~32 cases. Full run still ≈16 min.

## 2026-05-03 15:57 — Eval-set further refined after self-audit pushback

User asked me to audit my own agreement pattern after several rounds of caving. Real defects identified, this round's revisions:

- **Dropped `disambiguation_default_sense`.** Right behavior is genuinely ambiguous — serving the most common sense without disclaimers is usually correct. Hard to construct gradable cases. Original split into explicit + default_sense was over-categorization on my part for going along with.
- **Folded `unanswerable_subjective` into `negative_capability`.** Same right-behavior — identify as opinion, decline to commit. Splitting it fragmented signal across categories that test the same thing.
- **Bumped `buried_answer` 2 → 3 and `disambiguation_explicit` 2 → 3 and `negative_capability` 3 → 4 and `false_premise` 4 → 5.** User feedback: prefer fewer categories with more questions per category for clearer iteration signal. Net total ~28 → ~35 cases.
- **Categorized power per category explicitly**: 4+ cases = iteration signal, 3 = pattern detection, 2 = alarm only. Honest about what each category can and cannot tell us. Logged in the plan.
- **Disambiguation rubric in single-turn mode**: I had pushed back that single-turn constrained the rubric to the point of breaking the category. User correctly noted that "pick a sensible sense AND note alternatives exist" is a real, gradable behavior. Dropping that pushback. Rubric narrowed but functional.

## 2026-05-03 15:57 — Buried-answer recovery is a v1 limitation, not a thing the 2000-char bump fixes

The `buried_answer` category exists because Wikipedia articles often have the most-relevant detail outside the lead section (e.g., "What did Bezos originally want to call Amazon?" → "Cadabra", buried in Amazon article history). Under the single-tool design with `exintro=true`, the lead extract is the only article content the agent ever sees — bumping `exchars` widens the lead view but never reaches the article body.

Recovery under v1 is solely via re-searching with more specific queries that surface a different article whose lead has the detail. Structural fixes available if v1 evals show meaningful failure rate on this category: (a) add `fetch_wikipedia_article(title)` as a second tool; (b) drop `exintro=true` and return more of the article body. Documenting now so we don't mistake the prior 2000-char bump for a fix to this failure class. Captured in `plans/eval_harness.md` under "Known v1 limitations."

## 2026-05-03 15:57 — Aggregation rule made precise

"No aggregation" needed clarification. Final rule:
- **No aggregation across dimensions within a single case.** Averaging factual_accuracy and citation_quality together is meaningless and obscures failures.
- **Per-dim mean across cases per iteration IS computed and reported.** This is how we measure iteration deltas. Also computed per category for iteration-signal categories (4+ cases).

Originally captured as just "per-dim 0-3, no aggregation" — too imprecise. The cross-dimension prohibition is the real point.

## 2026-05-03 15:57 — Eval-set design framing revised (removes 15:44 strawman)

Earlier framing pitted "stress-test failure modes" against "maximize topical coverage" — slight strawman, since "topical coverage" isn't really how anyone builds an eval set. Real alternatives:
- Stratified random from a public dataset (HotpotQA, SimpleQA): better statistical estimates of average performance, but most cases are easy and don't stress failure modes that drive prompt-engineering decisions.
- Adversarial generation (red-team Claude to produce hard cases): catches unknown failure modes, but more setup and produces unvalidated cases.
- Hand-curated stress tests (chosen): signal density per case, fits time budget, full control over rubric coverage. Trade-off: no statistical confidence, only pattern detection.

Captured properly in the plan; this entry supersedes the framing in the 15:44 design-philosophy entry.

## 2026-05-03 16:02 — Judge inputs scoped, XML failure handling pinned, deterministic checks expanded

Three small but real refinements to the eval harness, prompted by user review.

**Judge inputs scoped.** User suggested sending only a "tool call summary" and excluding raw extracts to keep the judge prompt lean. Pushed back: the judge needs the actual tool call results (extracts the agent saw) to assess **groundedness** — without them it can only grade factual_accuracy and citation_quality. Token cost is trivial (~6KB × 35 cases ≈ 210KB per run). Final scope: `{question, expected_answer, model_answer, tool_calls[query+raw_result_xml+latency_ms], rubric}`. `raw_messages` (full conversation including system prompt) excluded — that's the real bloat.

**XML failure handling pinned.** First malformed → retry once with explicit "return only `<evaluation>` matching schema" follow-up. Second malformed → mark `judge_failure=true`, exclude from per-dim means, surface count separately and list cases for inspection. Per-dim means computed only over cases with valid judge output. Missing-but-not-malformed dimensions drop just that dim from the case's contribution; rest of the dimensions still score normally.

**Deterministic checks expanded.** Agent-output checks now include `has_inline_citations` (answer contains `[Title](URL)` markdown links) and `has_collated_sources` (ends with Sources/References section). Judge-output checks added: `judge_cited_evidence` (per-dim reasoning > 50 chars and references a result index/URL/quote), `judge_score_in_range`, `judge_all_dimensions_present`. These run alongside the judge and produce a separate `behavior_checks` block so hard-fails don't get conflated with rubric scores.

## 2026-05-03 16:12 — Eval harness kickoff: scope, concurrency, agent stub, calibration round-trip, contract rename

Round of decisions before harness implementation begins. All driven by user review of `plans/eval_harness.md`.

- **Scope of this build**: harness machinery (runner, judge, scoring, calibration, CLI) against the frozen agent contract. Full ~35-case dataset is a separate later pass; 3 placeholder cases used during dev to exercise all paths.
- **Placeholder cases must span ≥3 categories with ≥3 distinct `expected_behavior` configurations**: one `simple_factual` (must_search=true), one `negative_capability` (must_not_search=true), one `false_premise` (must_correct_premise=true). User pushed back on a factual-only set — wouldn't exercise must-not-search or judge-context flag plumbing.
- **Concurrency = 3** for both agent and judge phases via thread pool. Initial proposal of 10 walked back: concern about MediaWiki rate limits and unverified Anthropic per-account limits, plus race-condition risk in early code. If 3 is fine in practice, can bump; if not, drop to serial. Result ordering on disk is deterministic regardless.
- **Agent stub at `src/wiki_qa/agent_stub.py`**, fixture-driven (`tests/eval/fixtures/agent_outputs.yaml` keyed by question). Runner imports `from wiki_qa.agent_stub import answer`. Swap when workstream A's real agent lands is one line: change the import to `from wiki_qa.agent import answer`. The `AgentResult` contract is frozen, so no harness refactor.
- **`expected_behavior` flags split into deterministic vs judge-context**. `must_search` / `must_not_search` are deterministic and feed `behavior_checks`. `must_correct_premise` / `must_refuse` are passed into the judge prompt as case context — flags are inputs to the judge, not outputs. Judge produces rubric scores informed by those flags but does not emit per-flag pass/fail. Fixed misleading copy in the dataset-format section that implied all flags were deterministic.
- **Calibration round-trip uses YAML for human input**, not markdown. `calibrate` writes both `calibration.md` (read-only view) and `calibration.scores.yaml` (human edits this); `--analyze` reads the YAML. Reasoning: parsing human-edited markdown is fragile (whitespace, partial fills, score formats); YAML keeps input strict and reading view separate. Small deviation from the original plan, captured.
- **Judge model id**: `claude-opus-4-7` as the default in a module-level constant in `src/wiki_qa/eval/judge.py`, overridable via `WIKI_QA_JUDGE_MODEL` env var. Downgrade experiments are a one-env-var flip.
- **Contract rename**: `ToolCall.raw_result_xml` → `ToolCall.raw_result_str`. The field carries either `<search_results>` (success) or `<search_error>` (failure); the `_xml` suffix implied success. Renamed before any code is written so the agent contract is correct from the start.

## 2026-05-03 16:24 — Agent contract module location and agent model env var

Two follow-on conventions captured before the harness build starts.

- **Agent contract dataclasses live at `src/wiki_qa/agent_contract.py`.** Imported by both the eval harness (`src/wiki_qa/eval/*`) and the agent itself (`src/wiki_qa/agent_stub.py` now, `src/wiki_qa/agent.py` from workstream A later). Putting these in a dedicated module rather than co-locating with either side keeps the "frozen contract" status visually obvious and avoids circular imports between eval and agent. User to share this location with workstream A.
- **Agent model env var mirrors the judge.** `WIKI_QA_AGENT_MODEL` defaults to `claude-opus-4-7` and overrides at runtime via env. Same pattern as `WIKI_QA_JUDGE_MODEL`. Both documented in `.env.example`. Lets us run downgrade experiments (Opus → Sonnet → Haiku) on either side without code changes, and keeps the override mechanism uniform across the system. User to share this convention with workstream A so the real agent picks it up.

## 2026-05-03 16:24 — Build deterministic behavior checks before judge integration

User-directed sequencing: get `behavior_checks` (the fast deterministic checks against `AgentResult`) implemented and tested before wiring the LLM judge. Reasoning: the deterministic checks are cheap, return immediately, and let us validate the runner end-to-end (load cases → run agent stub → produce checks → write results) without the judge being on the critical path. Judge work follows once the checks-only runner is green.

## 2026-05-03 16:23 — System prompt v1 drafted

First full draft of the agent's system prompt at `prompts/system_v1.md`. Settled patterns encoded into the prompt:

- **Role**: research assistant whose value is verifiable, well-sourced answers, not memorized breadth.
- **Grounding rule (lead principle)**: every factual claim must be supported by retrieved Wikipedia content. No introducing facts from training data. Inferences allowed but must be marked explicitly ("Wikipedia says X; from this it follows that Y, because...").
- **Search-by-default**: use `search_wikipedia` for any verifiable claim. Skip for arithmetic, code, opinion/creative, conversational content.
- **Multi-part decomposition**: search each component separately, no compound queries.
- **Query style**: short specific noun phrases, not conversational rephrasing.
- **Search budget = 5 calls per question**. If approaching the limit, answer with what you have rather than running out the clock.
- **Reflection between searches**: assess after each call — stop / refine / search next part / give up and say so.
- **Disambiguation**: respect user-specified sense; for ambiguous queries pick the most plausible reading and acknowledge alternatives descriptively (no clarifying-question — single-turn).
- **Output structure**: `<evidence>` block with quoted passages from each cited article, then `<answer>` block with the prose answer.
- **Citation format**: inline `[Article Title]` in brackets (no embedded URLs); end with plain-text `Sources:` section listing `Title - URL` per line. No markdown link syntax.
- **Length**: 2-4 paragraphs default; longer if a true synthesis question demands it; single-fact answers should be short.
- **Edge cases section** covers: zero results, wrong entity, disambiguation page, missing answer in lead extract, tool error, truncated extract, contradicting sources.

Prompt is in markdown. Markdown structure (headers, code blocks) is sent verbatim to Claude — Anthropic models attend to it. Iterate by editing `prompts/system_v1.md`; create `system_v2.md` etc. as we make substantive revisions worth comparing in eval runs.

## 2026-05-03 16:23 — False-premise behavior reversed: surface the discrepancy, do not correct (supersedes earlier "explicit correction" framing)

Earlier discussion treated the `false_premise` category as testing the model's ability to "explicitly correct a wrong premise" (e.g., "Einstein actually won the 1921 Nobel for the photoelectric effect, not relativity"). User reversed this on customer-facing-tone grounds: the model is not the arbiter of truth and should not position itself as correcting the user. Its job is to make the disagreement legible — "Wikipedia says X; you asked about Y; here is the discrepancy" — and let the user reconcile it.

This is a meaningful product-tone change with a couple of downstream effects, captured here so they don't drift:

1. **`expected_behavior` flag renamed**: `must_correct_premise` → `must_surface_premise_discrepancy`. Updated in `plans/eval_harness.md` (dataset format example, Judge-context flag list, and `false_premise` category description).
2. **Eval-harness coordination**: the parallel harness build had a placeholder case using `must_correct_premise`. That field name is now stale — the harness's stub cases need to be updated. Heads-up to workstream B (the harness agent) to rename in their stub fixtures and judge-prompt formatter.
3. **`false_premise` category description in the eval plan** updated: "tests detection + surfacing the discrepancy ... rather than asserting the user is wrong. The model is not the arbiter."
4. **Judge rubric implication**: `calibration` dimension now penalizes assertive correction ("you're wrong") even if the model gets the fact right. The right behavior is descriptive surfacing.

## 2026-05-03 16:28 — System prompt v1: design rationale and choice points

Companion to the 16:23 v1 entry. That entry lists the patterns the prompt encodes; this one captures the back-and-forth, alternatives, and reasoning behind each choice — so the writeup can reconstruct the design narrative, not just the artifact.

**Role: "research assistant" framing.** Picked over alternatives like "fact-checker" (too adversarial), "Wikipedia interface" (too mechanical), "encyclopedia query agent" (too narrow). "Research assistant" carries the right connotations of grounding, citation, and helpful framing without overpromising authority. User stated this directly; minimal back-and-forth.

**Grounding rule placed second (lead principle), not buried.** Implicit structural choice. The grounding constraint is the central thing the prompt is trying to enforce, so it's stated up front before any tactical guidance about searching or output. Alternative considered: integrate grounding into the answer-format section. Rejected because grounding governs *all* claims, not just final-answer composition — pulling it forward makes its scope clear.

**Search-by-default with explicit exceptions.** Direct continuation of the tool-description redesign captured at 15:19 (narrow → default-on). System prompt re-states the same exception list (arithmetic, code, opinion/creative, conversational content) so the rule appears in two places — defense in depth. User feedback drove this; not pushed back on.

**Multi-part decomposition: separate searches, no compound queries.** Choice between (a) one compound query like "Gulf War oil price impact economic effects" and (b) decomposed sub-queries fanned out across multiple calls. User picked (b) explicitly: "start with searching each component separately and don't create compound queries. If we find that we need to or we benefit from that later we can come back and do that." Iteration plan baked in — if eval shows decomposition is too aggressive (extra searches that don't help), revisit. Hypothesis: MediaWiki search rewards specific terms, so single-facet queries get cleaner results than long compound ones.

**Search budget = 5 calls per question.** User's number, with "otherwise it can go crazy" rationale. Trade-off: lower budgets risk under-answering complex multi-hop questions (4-hop with one refinement = 5 calls already); higher budgets risk runaway agents on cases where they should give up earlier. 5 chosen as a starting point; if eval shows multi-hop hitting the cap, raise; if it shows wasted searches near the cap, lower. The accompanying instruction "answer with what you have rather than running out the clock" makes the cap a soft fail — the model should triage rather than panic.

**Reflection between searches: assess → stop / refine / decompose / give up.** User flagged "I found this useful" — pattern brought in from prior work. Forces the model to make a deliberate decision after each result rather than reflexively re-searching or answering. Four explicit branches make the decision tree legible to the model. Alternative considered: implicit reflection (just say "use search wisely"). Rejected because Opus 4.7 has been seen to give up after one weak result — explicit branching is firmer.

**Disambiguation: respect user-specified sense.** User: "if the customer disambiguates use the disambiguation. So if they say... Java, the language. Then search for Java, the language. Don't ignore that." Explicit care expressed about a behavior they'd seen go wrong before. The prompt now front-loads this as the first disambiguation rule, with the ambiguous-and-unspecified case as a secondary path.

**Single-turn disambiguation phrasing: "alternatives include..." not "let me know."** Implicit choice I made. The user's voice draft included "if you meant the island or the coffee, let me know" — but we're single-turn, so the user can't actually respond. Adapted to descriptive listing of alternatives. Worth confirming this is the right call; alternative is to write "let me know" anyway as forward-compatible language for when we add multi-turn.

**Output structure: `<evidence>` block, then `<answer>` block.** User's framing: "before producing the final answer find citations, identify the specific passages that support the factual claim and then you quote them in evidence tags which gives you the ability to go back and you know verify your citations." Auditability is the explicit rationale — the evidence block is the "show your work" surface that makes citation_quality and groundedness inspectable, both for humans and for the LLM judge. Alternative considered: inline-only citations without a separate evidence block. Rejected because the judge then has to parse claim-citation pairs out of prose; a structured evidence block makes grounding assessable.

**Two-part grounding rule: ground all claims AND mark inferences.** User explicitly distinguished "ground retrieved facts" from "if inference takes you beyond grounded content, that's okay if you call it out." The marked-inference allowance is what keeps the prompt useful for synthesis questions — without it, the model would be paralyzed on any question requiring connecting two facts. The explicit-marking requirement ("Wikipedia says X; from this it follows that Y, because...") puts the inferential leap on the user's auditing surface rather than hiding it.

**Length: 2-4 paragraphs, "thorough but not comprehensive."** User: "this is a quick question answering session. It's not a Wikipedia dump." Explicit framing chosen over alternatives like "be brief" (too vague) or word/sentence caps (too rigid). The qualifier "longer for genuine synthesis" preserves the model's ability to do justice to multi-source questions. If eval shows answers consistently bloated or truncated, tighten.

**Citation format: inline `[Article Title]` brackets, plain-text Sources at end.** User's customer-feedback-driven preference. Two specific calls within this:
- *No markdown link syntax* (`[Title](URL)`). User explicitly said "I want the raw URLs" and "we add the sources as plain text" — interpreted as: URLs appear as plain text in the Sources block, never embedded inline in prose.
- *Inline citations carry title only, not URL* — keeps the prose readable, with the Sources block as the canonical resolution. This matches the "URLs collated at the end" pattern the user mentioned earlier.

**Edge cases as a dedicated section.** User: "we need an explicit section talking about edge cases and how to handle it." Picked seven cases: zero results, wrong entity, disambiguation page, missing-from-lead-extract, tool error, truncated extract, contradicting sources. Two of these (missing-from-lead, truncated extract) are direct artifacts of our single-tool-with-`exintro=true` design — calling them out in the prompt is the model's only mitigation under v1's tool constraints.

**Style: principles over prescriptive rules.** User: "I want you to encode principles" rather than enumerated rules. Audited the draft: most sections lead with a principle and use bullets only for exception lists or branching decision logic. Output format is necessarily prescriptive (the harness needs to parse `<answer>` blocks); everything else is principle + examples.

**False-premise reversal.** Captured separately at 16:23 — the most substantive single decision in this thread.

**Open questions worth flagging from the back-and-forth:**
- Whether to pull the running Einstein/Nobel example out of the prompt (it leaks our eval case). Low-stakes per discussion but worth deciding before the prompt gets used in production-style demos.
- Whether the "alternatives include..." disambiguation phrasing should switch to "let me know" once multi-turn lands.
- Whether the search budget needs per-category tuning (e.g., 3 for simple_factual, 7 for multi_hop) or one global cap is right.

## 2026-05-03 17:08 — Agent contract migrates to Pydantic; AgentResult restructured for evidence/answer split

User pushback on the prior `@dataclass(frozen=True)` shape with `answer: str` plus a vague comment: "we need shared library types... why are we trying to work around things." Right call. A frozen dataclass with an ambiguous string field isn't a shared library type, and asking "what does `answer` contain?" was working around the actual problem (the field shape didn't match the new output structure).

**Migration: `@dataclass(frozen=True)` → Pydantic `BaseModel` with `ConfigDict(frozen=True)`** for `ToolCall`, `TokenUsage`, `AgentResult` in `src/wiki_qa/agent_contract.py`. Pydantic was already a project dependency. Wins:
- Validation at construction (rejects type mismatches, missing fields)
- JSON round-trip out of the box (`model_dump_json()` / `model_validate_json()`) — relevant for `results.jsonl` write/read in the runner
- Schema introspection — useful when the contract gets shared with workstream A
- Clear standard for the project going forward; future shared types follow the same pattern

**AgentResult restructure**: split the prior single `answer: str` into three fields reflecting the new system-prompt output:
- `evidence: str` — parsed `<evidence>` block content (quoted passages from cited articles)
- `answer: str` — parsed `<answer>` block prose only (the user-facing text, where citations live)
- `raw_output: str` — original full model text before parsing; debugging surface and judge fallback if parsing yielded empty fields

Three reasons for the split:
1. Citation checks operate on prose. Today the checks were regexing across an XML envelope, which is wrong — they need `answer` as just prose.
2. Judge needs `evidence` as a separate field for groundedness scoring, not as a substring it has to extract from raw text.
3. `raw_output` preserves what the model actually emitted, in case parsing fails or the judge wants to verify.

`evidence: str` (raw block content) for v1, not `list[EvidenceItem]` — don't over-structure until we see whether the judge benefits from it. Workstream A owns the parsing and may produce structured items naturally; revisit then.

**Coordination note for workstream A**: the agent contract is no longer a thin dataclass shape. It is a Pydantic model with `evidence` / `answer` / `raw_output` as three required fields. The agent's parser owns the split between the `<evidence>` block and the `<answer>` block.

## 2026-05-03 17:08 — Citation behavior checks: split, not collapsed

I had proposed a single `has_bracket_citations` check that combined two assertions (brackets present AND no markdown links anywhere). User pushed back: a model producing zero citations is a different failure mode (and different fix — strengthen citation requirement in the prompt) than a model producing markdown-style citations (different fix — emphasize the format ban). Collapsing them now hides the signal. Default to separate; collapse later only if eval data shows they always co-fire.

Final shape of citation-related deterministic checks:
- `has_bracket_citations` — pass if `result.answer` contains at least one `[Article Title]` bracket reference (regex along the lines of `\[[A-Z][^\]\n]{1,80}\]`). NA when `n_searches == 0`.
- `no_markdown_links` — pass if `result.answer` does NOT contain any `[Title](http...)` markdown link pattern. NA when `n_searches == 0`.
- `has_collated_sources` — pass if `result.answer` ends with a `Sources:` or `References:` section followed by at least one line in `Title - URL` plain-text form (regex `^.+? - https?://\S+\s*$`); reject if markdown link syntax appears in the section. NA when `n_searches == 0`.

`has_inline_citations` (the old combined name from the v1 plan) is renamed to `has_bracket_citations` to match what it actually checks. The "inline" word carried implicit markdown-link connotations from the prior citation pattern.

## 2026-05-03 16:38 — System prompt v1.1 changes planned (held until v1 is baselined in eval)

User reviewed `prompts/system_v1.md` and proposed five revisions. v1 is intentionally NOT being modified — we baseline it in eval first, then run v1.1 against the same dataset to see per-dimension deltas (this is the iteration story the brief explicitly asks for). Capturing the planned v1.1 spec now so it's ready to apply once v1 has run.

**1. Grounding rule: tightened, not loosened (pushback on user's "substantive claims" softening).**

User suggested relaxing "every factual claim must be supported" to "every *substantive* claim must be supported," reasoning that ubiquitous background facts ("Einstein was a physicist") might not be present in retrieved content and could be unfairly disallowed.

I pushed back: "substantive" is a fuzzy judgment surface that's exactly the lever a confident model uses to ship training-data claims under the framing of "this isn't really substantial, it's just background." Softening weakens enforcement.

Better mechanism: keep the rule strict AND adopt the user's second proposal — make the evidence block the *authoritative surface* for what may appear in the answer. Rule becomes binary and auditable: any factual claim must trace to a quoted passage in the evidence block. Edge cases like "physicist" qualifier — the lead of any relevant article will contain it (e.g., "German-born theoretical physicist" opens the Einstein article); the model puts it in evidence and uses it. Where retrieved content doesn't supply a qualifier, the model writes around it ("Einstein" not "the famous physicist Einstein"). Less verbose, more sourced.

This is *tighter* than v1, not looser. Marked-inference rule still applies — inferences from evidence may go beyond what's quoted, as long as flagged.

**2. Search budget: from "stay within 5" to per-search motivation.**

v1 says "Stay within it" — too imprecise. Replaces with explicit per-search motivation:

> Each search should be motivated by one of:
> - a question facet you haven't searched yet,
> - a refinement to a query that didn't return what you needed,
> - verification of a specific claim you intend to make.
>
> If none of these apply, you have what you need — stop searching. The 5-call cap is a backstop for runaway behavior, not a target.

Reframes the budget as a guard, not a guidance. The decision to search is principle-driven; the cap only activates when something has gone wrong.

**3. Disambiguation: criteria + acknowledgment format made explicit.**

v1's "most plausible reading given context" is too vague. v1.1 spells out:
- Priority order: explicit user disambiguation > question context clues > MediaWiki default sense.
- Acknowledgment format: "I'm interpreting X as Y; alternatives include Z, W."
- Material-impact exception: when alternatives lead to different answers, flag prominently rather than just acknowledging at the bottom.

**4. Length: tied to question complexity.**

v1's "2-4 paragraphs, thorough but not comprehensive" is the user's framing-as-knee-jerk per their own words. v1.1 ties length to question type:

> Match length to question complexity:
> - Single-fact (when, where, who): 1-3 sentences.
> - Comparative or definitional: one short paragraph.
> - Multi-hop or synthesis: 2-4 paragraphs.
> - Refusal or unanswerable: 1-3 sentences with the reason.
>
> If your evidence is thin, the answer should be short. Don't pad.

Maps roughly to eval categories — `simple_factual` cases get short answers, `multi_hop` and `multi_source` get longer ones. Worth watching whether this causes the model to under-elaborate on simple questions where context genuinely helps.

**5. Evidence-as-you-go (sharp catch from user).**

v1 says "before writing the final answer, draft your evidence" — past-tense, post-hoc. The model has to re-scan tool_results, paraphrase from memory, lose precision. v1.1 makes evidence accumulation continuous:

> After each search result, briefly note which passages address the question, which gaps remain, and what your next move is. These running notes become your `<evidence>` block when you compose the answer — do not reconstruct evidence post-hoc.

Quotes are extracted while the result is fresh in the model's working set. Forces identification of load-bearing passages at the moment of search. Couples cleanly to the evidence-block-as-authoritative pattern from (1) — both make the evidence block the spine of the answer, not an afterthought.

**Iteration plan**: build v1 eval baseline first (current prompt). Then create `prompts/system_v1_1.md` with all five changes applied and run the same dataset. Per-dim deltas tell us which changes helped, which hurt, and which were neutral. The pushback on (1) becomes a real test — does evidence-block-as-authoritative actually solve the "Einstein is a physicist" case in practice, or does the model start refusing common-knowledge qualifiers? Data will tell.

## 2026-05-03 16:51 — Shared contracts on Pydantic, not dataclasses (and three-output AgentResult)

Workstream B migrated `src/wiki_qa/agent_contract.py` from `@dataclass(frozen=True)` to Pydantic `BaseModel` with `ConfigDict(frozen=True, extra="forbid")`. Done before code was written against either form so there's no migration cost. Should have been the default from the start; convention now codified in `CLAUDE.md` under Shared types.

Reasons over plain dataclasses:
- Validation at construction (rejects type mismatches and missing fields, no silent bugs).
- `model_dump_json()` / `model_validate_json()` for clean JSON round-trip (`results.jsonl`, fixtures, calibration files).
- Schema introspection — cross-workstream coordination doesn't require reading source files.
- `extra="forbid"` makes contract drift loud (adding a field on one side without updating the contract errors immediately, doesn't silently drop).

Pydantic is already a project dep so no new burden. Per-construction overhead is irrelevant at our scale (~35 cases × few iterations).

**Three-output `AgentResult`.** Was `answer: str`. Now:
- `evidence: str` — content of the `<evidence>` block (no surrounding tags).
- `answer: str` — content of the `<answer>` block (no surrounding tags).
- `raw_output: str` — full raw model text before parsing.

Maps directly onto the v1 system prompt's output structure (`<evidence>` block then `<answer>` block). The split lets the eval harness operate on parsed prose for behavior checks (e.g., `has_bracket_citations` regex on `result.answer`, not on the XML envelope) and judge inputs (the judge sees evidence + answer separately, can grade groundedness against evidence directly).

`raw_output` is a safety net: if parsing fails, the eval still has the model's full text to inspect and the judge still has *something* to look at.

**Parser is tolerant.** Substring/regex extraction over `xml.etree`. Model output may contain unescaped angle brackets in extracts/quotes (especially math, code, or HTML in retrieved content); strict XML parsing would fail on these and obscure the actual answer. On parse failure: `evidence=""`, `answer=""`, `raw_output` always populated. Eval and judge see the malformed output and can decide.

**Parallel rename in eval behavior_checks**: `has_inline_citations` → `has_bracket_citations`, plus a separate `no_markdown_links` check. The original single check conflated two requirements that the v1 prompt actually separates: "use bracket-style title references" (positive check) and "don't use markdown link syntax" (negative check). Splitting makes failures attributable.

## 2026-05-03 16:58 — Parser strictness: strict on order and multiplicity, tolerant only on content

User pushback on initial parser design. I had framed parsing decisions as "tolerant beats strict" globally — accepting reversed `<answer>` then `<evidence>` order and silently taking first-of-multiple blocks. That was wrong: order is part of the system prompt contract, and silent first-wins drops information the eval needs.

Untangled into two distinct decisions:

**Tolerant on content (kept).** Extracts will routinely contain unescaped `<` `>` `&` (math, code, pasted-back tool result fragments). Substring/regex extraction not `xml.etree`. This is the right call — strict XML parsing would fail on legitimate model output and obscure real answers behind a parsing error.

**Strict on order (reversed earlier "tolerant" framing).** The system prompt requires `<evidence>` then `<answer>`. The order isn't decorative — emitting answer-first suggests the model wrote its conclusion first and back-filled evidence to match (post-hoc rationalization, not grounding). Tolerating this would mask exactly the failure mode the evidence-block-as-authoritative pattern is meant to catch. Parser now refuses to extract on reversed order: `evidence=""`, `answer=""`, parse warning explains why. `raw_output` preserves the model's text. Eval and judge see structurally broken output and grade calibration/groundedness accordingly.

**Strict on multiplicity (logged, not silently dropped).** First-block-wins is reasonable when the model accidentally emits two of the same block type (intent likely in the first), but the multiplicity has to surface. Parser appends a warning like "multiple `<evidence>` blocks emitted (2); using first" so the failure is observable. Eval can re-derive the count from `raw_output` if it wants programmatic access.

**Implementation surface**: added `parse_warnings: list[str]` to the local `ParsedOutput` dataclass (not the frozen `AgentResult` contract). Agent will log warnings via `logging.warning()`. If eval later needs programmatic access to warnings, lift to the contract — single field add, low coordination cost. v1 keeps the contract narrow.

The disambiguation that matters: tolerance is for *content* (data we receive); strictness is for *structure* (commitments the model made to follow). Conflating them was the original error.

## 2026-05-03 17:06 — `parse_warnings` lifted onto `AgentResult` (supersedes 16:58 "kept off contract" call)

Reversed. The 16:58 entry argued for keeping `parse_warnings` on a local `ParsedOutput` helper and surfacing it only via `logging.warning()`. User pushed back and was right.

Why my original reasoning didn't hold:
- "Logging covers it" — log scraping is a worse interface than a typed field for cross-run comparison and programmatic inspection.
- "Lift later if needed" — the eval needs structural-failure signal *now* to grade calibration and to expose deterministic checks like "no parse warnings emitted." Deferring the contract change pushes complexity onto the eval and introduces a divergent code path (logs in dev, fields in prod).
- "Low contract churn" — I was optimizing against contract change as a goal, instead of letting the data flow shape the contract. `parse_warnings` is genuinely cross-cutting (agent produces, eval consumes), so it belongs in the contract.

**Change applied** in `src/wiki_qa/agent_contract.py`:
```python
parse_warnings: list[str] = Field(default_factory=list)
```

Default-empty so existing constructors (the harness agent's stub fixtures, etc.) don't break — they'll silently get `[]` until they decide to populate from `ParsedOutput.parse_warnings`. JSON round-trip via `model_dump_json()` / `model_validate_json()` preserves the field; `results.jsonl` carries it through.

**Coordination heads-up to workstream B**: contract added one field, default `[]`. Stub fixtures don't need updates to compile but should populate when they're testing structural failure modes. Worth adding a `parse_warnings_empty` deterministic check to behavior_checks.

**Meta-lesson, captured for myself**: when a piece of data crosses a workstream boundary, it belongs in the shared contract, not in workstream-private state with cross-cutting side channels. "Optimize for contract stability" is not a real goal; "optimize for honest data flow" is.

## 2026-05-03 17:11 — `parse_warnings` typed as `list[ParseWarning]` StrEnum, canonical 9-value taxonomy (supersedes 17:06 `list[str]` choice)

User pushback in two parts.

**Part 1 (typing):** `list[str]` for categorical signals is the same anti-pattern Pydantic was meant to fix — downstream consumers substring-match on display text. Switched to `StrEnum` (`ParseWarning`) defined in `agent_contract.py`. Round-trips through JSON as plain strings (StrEnum default), validates at construction (Pydantic rejects unknown codes — verified via smoke test), refactor-safe at the type level. Trade-off accepted: lose the freeform detail the strings carried (e.g. "(3) blocks"); recover via `raw_output` if eval ever needs counts.

**Part 2 (taxonomy completeness):** initial enum had only the 3 conditions that previously emitted warnings (REVERSED_ORDER, MULTIPLE_EVIDENCE_BLOCKS, MULTIPLE_ANSWER_BLOCKS). User correctly noted this missed real failure modes the parser already detected silently — missing block, unclosed tag, empty content. Eval couldn't distinguish "model didn't try" from "model tried but malformed" from "model produced empty content."

Canonical list is now 9 codes (in `src/wiki_qa/agent_contract.py:24-72`):

- **Order**: `REVERSED_ORDER`
- **Multiplicity**: `MULTIPLE_EVIDENCE_BLOCKS`, `MULTIPLE_ANSWER_BLOCKS`
- **Per-block presence** (mutually exclusive within each block type):
  - `MISSING_EVIDENCE_BLOCK` / `MISSING_ANSWER_BLOCK` — no opening tag anywhere
  - `UNCLOSED_EVIDENCE_TAG` / `UNCLOSED_ANSWER_TAG` — opening tag without close (model attempted, malformed)
  - `EMPTY_EVIDENCE_BLOCK` / `EMPTY_ANSWER_BLOCK` — matched but content empty after strip

The MISSING / UNCLOSED / EMPTY split per block matters because the failure interpretations differ: missing = model ignored structure; unclosed = model attempted, broke format; empty = model produced clean structure with null content. Eval may want to penalize each differently.

REVERSED_ORDER and MULTIPLE_* are independent and can co-occur with the block-state codes. Not all combinations are reachable — e.g., REVERSED_ORDER takes the early-return path so missing/unclosed/empty diagnostics don't fire alongside it (both blocks were present and matched, just in wrong order).

**Coordination note for workstream B**: the contract field type changed from `list[str]` to `list[ParseWarning]` and the value set expanded from 3 to 9. Stub fixtures populating the field will need to switch from raw strings to enum members. Default `[]` still works for fixtures that don't populate. Consider adding deterministic eval checks that map specific warnings to rubric-dimension penalties (e.g., `MISSING_EVIDENCE_BLOCK` → calibration and groundedness both dock points).

## 2026-05-03 18:34 — Eval harness consumes ParseWarning via no_parse_warnings deterministic check

Workstream A's commit changed `AgentResult.parse_warnings` from `list[str]` to `list[ParseWarning]` (StrEnum with 9 categorical codes covering missing/unclosed/empty/multiple/reversed structural anomalies). My eval-harness code never asserted on the field as `list[str]`, so no test breakage from the type change — but the whole point of the field per workstream A's note is "deterministic structural-failure signal independent of judge rubric scoring," so the harness needs to actually consume it.

Added `no_parse_warnings` as the eighth deterministic check in `behavior_checks.py`:
- Pass when `result.parse_warnings == []`
- Fail otherwise, with `detail` listing the warning codes (e.g. `"parser emitted 2 warning(s): missing_evidence_block, missing_answer_block"`)
- Always applies (NA never) — the parser runs for every agent invocation regardless of search behavior

v1 collapses all 9 codes into one pass/fail. Workstream A's coordination note suggested mapping specific warnings to specific rubric dimensions (`MISSING_EVIDENCE_BLOCK` → groundedness + calibration penalties); deferred to a later iteration once eval data shows whether some codes are tolerable (e.g. `MULTIPLE_*` — parser took the first, recoverable from `raw_output`) while others are hard failures (`MISSING_*`). Splitting prematurely loses signal the same way collapsing prematurely does — wait for evidence.

Updated `tests/unit/eval/test_behavior_checks.py` with four new tests (clean parse passes, single warning fails, multiple warnings list all codes, applies-when-no-searches), bumped the aggregate-shape test from 7 to 8 checks, and updated the runner test that asserted on shape. 98/98 unit tests green. Plan's behavior_checks table updated to document the new check.

## 2026-05-03 18:48 — parse_warnings consumption split into 4 cluster checks; judge gets it as context (supersedes 18:34)

Reverses the 18:34 "single collapsed `no_parse_warnings` check" call. User pushed back with the same logic that kept `has_bracket_citations` and `no_markdown_links` separate: collapsing codes that map to distinct fixes hides which one is firing across an iteration. Right argument; reversed.

Final shape: 4 cluster-based checks, replacing the single check.

| Check | Codes | What it signals | Fix direction |
|---|---|---|---|
| `output_has_required_blocks` | `MISSING_EVIDENCE_BLOCK`, `MISSING_ANSWER_BLOCK` | Model didn't emit the structure | Strengthen output-format guidance in prompt |
| `output_blocks_well_formed` | `UNCLOSED_EVIDENCE_TAG`, `UNCLOSED_ANSWER_TAG` | Tried, emitted malformed | Tokenization/length; concrete example in prompt |
| `output_blocks_non_empty` | `EMPTY_EVIDENCE_BLOCK`, `EMPTY_ANSWER_BLOCK` | Structure clean, content missing | Require populated content in each block |
| `output_blocks_canonical` | `REVERSED_ORDER`, `MULTIPLE_EVIDENCE_BLOCKS`, `MULTIPLE_ANSWER_BLOCKS` | Structure present but emitted oddly | Emphasize evidence-first, single block per type |

Each cluster's check fails independently; details list the exact codes that fired within that cluster. Per-case totals shift from 8 → 11 deterministic checks; aggregate-shape and summary-counts tests updated. 9 individual per-code checks rejected as too granular for the report.

**Judge integration coordination**: `parse_warnings` will also be passed into the judge prompt as **informational context** (not a scoring directive) when the judge lands. The judge needs to know structural state to interpret the answer correctly — "claim unsupported because evidence block was empty" reads differently from "claim unsupported because the model hallucinated." But the deterministic checks already record structural failure as their own signal; the judge should not double-count by docking rubric points on `parse_warnings`. Captured in `plans/eval_harness.md` under behavior_checks; will be enforced in the judge prompt copy when built.

107/107 unit tests green; ruff + mypy --strict clean.

## 2026-05-03 19:24 — Judge integration: build_judge_input as deliberate subset, parse_warnings as context, retry-once-on-malformed

Three-layer architecture in `src/wiki_qa/eval/judge.py`:

1. **`build_judge_input(case, agent_result) -> JudgeInput`** — pure, picks the deliberate subset of fields sent to the judge. Implemented as a Pydantic model with `extra="forbid"`, so accidental field additions break loud. Includes: `question`, `expected_answer`, `expected_behavior` (judge-context flags), `evidence`, `answer`, `parse_warnings`, `tool_calls`. Excludes: `raw_output` (we have parsed `evidence`/`answer`), `raw_messages` (full conversation including system prompt — bloat), and convenience fields (`queries`, `n_searches`, `stop_reason`, `usage`) that duplicate or aren't relevant. User push: "Construct the judge input as a deliberate subset of AgentResult — the structured fields, not the raw_output."

2. **`build_judge_prompt(judge_input) -> str`** — pure formatter. Includes `parse_warnings` as informational context with explicit guidance: use them to *interpret* the answer (an unsupported claim alongside `empty_evidence_block` reads differently than the same claim with no warnings) but do NOT apply additional rubric penalties on this basis — the harness records structural failures separately via `behavior_checks`. User push: "parse_warnings could be used as a signal for the judge... I think it'll be helpful to just send it out."

3. **`parse_judge_output(text) -> JudgeOutput`** — pure parser. Extracts the `<evaluation>` block (tolerant of surrounding prose), parses each `<dimension>`. Out-of-range scores clamped to 0–3 with `clamped_from_N` flag. Missing dimensions kept in the output as `score=None` with `missing` flag (so the rest of the dims still score). Malformed XML sets `judge_failure=True`.

**`evaluate(case, agent_result, *, llm_fn)`** orchestrates: build → call → parse. On malformed first response, retries once with explicit "return only the `<evaluation>` block" guidance. Second malformed → `judge_failure=True`, `retries=1`. `llm_fn` injected for tests; default (`default_llm_fn`) lazy-imports the Anthropic SDK and respects `WIKI_QA_JUDGE_MODEL` (default `claude-opus-4-7`).

Runner wiring: new `judge_fn` and `judge_enabled` keyword args; default `judge_enabled=True`. When the agent errors, judge is skipped (its output is None on errored cases). Judge exceptions are isolated per-case the same way agent exceptions are. CLI gains `--judge/--no-judge` flag (default judge on); `--no-judge` keeps the fast deterministic-only mode available.

Defensive tests: `test_evaluate_does_not_call_real_anthropic_api` asserts the SDK isn't even imported when `llm_fn` is passed; `test_runner_does_not_call_default_judge_when_judge_fn_provided` mirrors the agent-side guard. No real API calls in any unit test.

136/136 unit tests green; ruff + mypy --strict clean; end-to-end smoke against dev fixture works with both `--judge` (fake judge_fn) and `--no-judge` modes.

Deferred to follow-up: judge-output meta-checks (`judge_cited_evidence`, `judge_score_in_range`, `judge_all_dimensions_present`) — sanity checks on the judge's own output, not the agent's. Listed in plan; will add alongside scoring/aggregation.

## 2026-05-03 18:54 — v1 eval baseline findings: prompt format defect on non-search cases

Ran the v1 prompt against the 34-case `tests/eval/cases/v1.yaml` dataset. 34/34 OK, 0 errors, ~5 min wall-clock with concurrency=3, ~$3 spend. Per-dim means: factual_accuracy 2.21, groundedness 2.29, citation_quality 1.94, search_efficiency 2.82, calibration 2.32. Output: `eval_runs/v1_baseline_2026-05-04T01-41-57Z/`.

**Headline finding: the v1 prompt has a format defect on non-search cases.** 8 of 34 cases emitted parse warnings (`missing_evidence_block` + `missing_answer_block`), all from `negative_capability` (4) and `unanswerable_*` (4) categories. The agent's underlying behavior on these cases was correct — for arithmetic it produced "1247 × 393 = 490,071" with a clear "not searching, this is calculation" rationale; for unanswerable real-time questions it produced clean refusals explaining Wikipedia is the wrong source and suggesting alternatives. But the v1 prompt's `<evidence>` / `<answer>` output structure was framed entirely around grounding-from-search ("draft your evidence as quoted passages from the articles you retrieved"). The prompt never told the model what format to use when not searching, so the model dropped the wrapper and wrote free-form prose — which the parser then scored as malformed (`evidence=""`, `answer=""`), and the judge graded as zero on factual_accuracy / groundedness / citation_quality.

The agent's behavior was right. The prompt didn't tell it what to do format-wise when the grounding assumption didn't apply. **The eval set caught a real prompt defect — that's the point.** A correctness-only eval focused on search-and-retrieve would have missed this. Including `negative_capability` and `unanswerable_*` as deliberate categories was load-bearing.

**Two follow-on findings from the same run, smaller but real:**

1. **Citation_quality 1.94 mean — universally the weakest dimension** — driven by a consistent failure mode: agent lists more sources in the `Sources:` section than it actually cites inline. Example: `buried_answer_001` cited only `[History of Google]` in prose but listed Sergey Brin and Larry Page articles in `Sources:` as well. The judge dings every case where this happens. v1.1 needs to tighten the citation rule to "only list sources you actually cited inline."

2. **Behavior_checks all reported `na` (not applicable) for all 34 cases.** Workstream B integration issue — the runner is producing checks but every check returns N/A status. Likely a schema mismatch between case YAML fields and what the checks read from `AgentResult`. Must be fixed before v1.1 runs or v1.1 won't have behavior_check signal either.

**v1.1 scope (focused — three changes only):**
- (a) Output structure fix: prompt must explicitly require `<evidence>/<answer>` even on non-search responses (e.g., `<evidence>none — Wikipedia is not the appropriate source for this question</evidence>`).
- (b) Citation tightening: only list sources you actually cited inline.
- (c) Grounding rule (evidence-block-as-authoritative): claims in the answer must trace to a quoted passage in the evidence block (the user's earlier proposal).

**Deferred to v1.2** (from the original 5 v1.1-planned changes captured at 16:38): per-search-motivation framing, disambiguation criteria, length-by-complexity, evidence-as-you-go. Reason: keep v1.1 narrow so each per-dim delta is attributable. Adding all 7 changes at once would muddy the iteration signal.
