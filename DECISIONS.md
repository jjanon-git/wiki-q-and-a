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
