# Design rationale

The hardest part of getting an LLM to answer questions reliably from Wikipedia is not writing the prompt; it is knowing what to iterate the prompt toward. I built the eval before I built the prompt and let it drive every other design decision. 

The system under evaluation is a search-by-default Claude Opus 4.7 agent with a single `search_wikipedia(query)` tool. The eval has 34 hand-curated cases across 10 failure-mode categories, a 5-dimension rubric scored by an LLM judge, and 11 deterministic behavior checks running alongside.

The bet dictated the order of work. I locked the agent-harness contract first, then designed the failure-mode taxonomy, then wrote the rubric, all before finalizing the system prompt. The prompt iterated against eval output, not intuition.

v1.1 is in production. It lifts every rubric dimension over a v1 baseline to between 2.74 and 2.94 on the 0-3 scale, up from a baseline range of 1.94 to 2.82. Parse warnings dropped from 8 of 34 cases to 0. A second iteration (v1.2) confirmed a specific local hypothesis but did not justify shipping. The eval does not measure voice or tone, and that gap is deliberate. The chronological design log is in `DECISIONS.md`; this document is the consolidated rationale.

## Iterations and what they showed

I ran the eval against three prompt versions: a v1 baseline and two iterations on top of it. v1 to v1.1 was a big lift driven by two obvious failure modes (parse warnings 8/34 to 0/34, factual_accuracy +0.74, citation_quality +0.91). v1.1 to v1.2 confirmed a specific local hypothesis on `buried_answer` (groundedness 2.33 to 3.00) but moved nothing else outside judge-variance, so I kept v1.1 in production. The eval surfaced its own limit at this dataset size. With n=34 and mostly-ceiling performance, the eval finds big failures and big wins but cannot statistically distinguish two near-ceiling prompts.

### v1 baseline

v1 produced per-dimension means of factual 2.21, grounded 2.29, citation 1.94 (the weakest), search 2.82, and calibration 2.32. Two failure modes dominated.

The first was a parse-warning blowout on non-search cases. All 8 non-search cases (4 `negative_capability` and 4 `unanswerable_*`) emitted `missing_evidence_block` and `missing_answer_block`. The agent's behavior was correct. It did not search arithmetic and refused real-time questions cleanly. But the v1 prompt's `<evidence>` and `<answer>` structure was framed entirely around grounding-from-search. With no search, the model dropped the wrappers, the parser scored them malformed, and the judge gave 0s on factual, grounded, and citation.

The second failure mode was citation over-listing. The `Sources:` section listed more articles than the prose actually cited inline.

### v1.1: the big lift

Three focused changes addressed the v1 failures. First, I required wrappers even on non-search cases (e.g., `<evidence>none, Wikipedia is not the appropriate source</evidence>`). Second, I tightened the prompt to "only list sources you cited inline." Third, I added an evidence-block-as-authoritative rule that requires every claim to trace to a quoted passage. Every dimension improved, parse warnings dropped to 0/34, and every previously-failing behavior check went to 0 fails. The 0.91 jump on citation_quality is the largest single delta in the iteration story.

Every category improved over v1. The largest moves were on `negative_capability` and `unanswerable_*`, which had been at 0 on factual, grounded, and citation due to the parse failures, and lifted to 2.0-3.0 across those dimensions in v1.1. Grounded categories (`simple_factual`, `multi_hop`, `multi_source`, `false_premise`) also moved up across the board, mostly to ceiling.

### v1.2: confirmed the local hypothesis, did not justify shipping

I tightened the marked-inference rule to a binary in v1.2. Every claim either traces to evidence, is explicitly marked as inference, or does not appear. This targeted the v1.1 `buried_answer` groundedness regression (3.00 to 2.33). The change worked locally. On `buried_answer`, groundedness recovered to 3.00 (+0.67). Every other delta was within judge-noise (global Δ between -0.09 and +0.03). I kept v1.1 in production and preserved v1.2 as `prompts/system_v1_2.md` for reference.

v1.2 surfaced a methodology limit. At this n with mostly-ceiling performance, deltas under ~0.30 are inside judge-stochasticity. The eval cannot tell a real prompt regression apart from judge noise at this dataset size. The eval was designed for surfacing big failures (v1's 8/34 parse warnings) and big wins (v1 to v1.1), not for ranking similar prompts at near-ceiling. The right next investment is not another prompt iteration. It is bigger n and multi-run averaging, both discussed below.

## Six choices

### 1. Stress tests over random sampling

I picked 34 hand-curated stress tests over a stratified random sample of ~200 from HotpotQA or SimpleQA. Public-dataset random samples are mostly easy and do not stress the behaviors that drive prompt-engineering decisions. Hand-curation buys signal density. Every case is a hypothesis about a specific failure mode, and a fail points at a specific fix. The trade-off is no statistical confidence on average performance at this scale, only pattern detection. I considered and rejected adversarial generation (red-team Claude to produce hard cases) for v1 because it adds setup time and yields cases without curation. 34 deliberate cases beats 100 unvalidated.

### 2. Don't collapse signals that map to different fixes

I split citation conventions into two checks (`has_bracket_citations`, `no_markdown_links`) instead of one "citation format" check, because zero citations and forbidden markdown citations are different failure modes with different fixes. I clustered parse warnings into four buckets (missing-block, unclosed-tag, empty-block, non-canonical) rather than collapsing into one or splitting nine ways, because the four clusters map to four prompt-fix directions. The rubric forbids cross-dimension aggregation entirely; averaging factual_accuracy and citation_quality together is meaningless and obscures failures. The rule is not split everything. It is keep two failures visible when they suggest different fixes.

### 3. Deterministic checks alongside the LLM judge

I picked the LLM judge and 11 deterministic `behavior_checks` together, not one or the other. Each catches what the other cannot. The judge scores groundedness or calibration with judgment that no pattern-match could replicate. The deterministic checks catch format failures that the judge might score around if the answer happens to read well. The two signals stay in separate blocks of the result so a hard format-fail does not get conflated with rubric scores. The judge gets `parse_warnings` as informational context with explicit guidance to use them for interpretation (an unsupported claim alongside `empty_evidence_block` reads as a populating failure, not a hallucination) and not to dock rubric points on top of the deterministic checks already capturing that signal.

### 4. Surface premise discrepancies, don't correct them

The original `false_premise` framing had the model "correct" wrong premises ("Einstein actually won the 1921 Nobel for the photoelectric effect, not relativity"). I reversed this mid-design on customer-tone grounds. The model is not the arbiter of truth. Its job is to make the disagreement legible ("Wikipedia indicates X; the question's premise was Y") and let the user reconcile. The reversal rippled through three places: the rubric's `calibration` dimension now penalizes assertive correction even if the fact is right, the dataset flag was renamed `must_correct_premise` to `must_surface_premise_discrepancy`, and the system prompt's edge-cases section documents the descriptive-surfacing behavior. The eval can grade this kind of choice because I rewrote the rubric alongside the prompt change. Without the joint update, the judge would have kept rewarding "correction."

### 5. Two-block evidence/answer output for auditability

I picked a two-block output structure (`<evidence>` then `<answer>`) over a single-block answer because the judge needs to score groundedness independently of factual_accuracy. A separate evidence block makes "the answer is right but ungrounded" a visible failure mode. Without it, the model can answer correctly from priors and the eval cannot tell. Every response emits an evidence block (quoted passages from cited articles) followed by an answer block (prose with inline `[Article Title]` brackets and a plain-text `Sources:` section). v1.1 sharpened the structure by adding an evidence-block-as-authoritative rule. Every claim in the answer must trace to a quoted passage in evidence. That rule earned the 0.91 jump on citation_quality.

### 6. Two parallel agents, one Pydantic contract

I split the implementation into two parallel workstreams under my direction. Workstream A built the Wikipedia integration, agent loop, parser, and prompt iterations. Workstream B built the eval harness (dataset, runner, judge, behavior_checks). Both coded against `src/wiki_qa/agent_contract.py`, a Pydantic `BaseModel` with `frozen=True, extra="forbid"`. I picked Pydantic over `@dataclass(frozen=True)` because Pydantic validates at construction (typed errors instead of silent drops), `extra="forbid"` makes contract drift loud rather than silent, JSON round-trip works out of the box for `results.jsonl`, and the schema is introspectable so cross-workstream coordination does not require reading source files. The contract earned its keep when workstream A added the `ParseWarning` enum and `parse_warnings` field. Workstream B picked the change up without breakage, and I added four cluster-based deterministic checks on the new signal in the same session.

## What this eval does not measure

The v1 eval measures correctness: factual_accuracy, groundedness, citation_quality, search_efficiency, calibration. These are the dimensions that determine whether the answer is right. They do not measure voice or tone, whether uncertainty is expressed with confidence or apologetically, whether surfacing a false premise feels respectful or corrective, whether the agent's prose has the warmth that distinguishes a thoughtful response from a merely competent one. This gap is real and deliberate. Voice failures do not surface cleanly in Wikipedia QA categories. They live in conversational shapes (frustrated users, ambiguous emotional contexts, situations requiring graceful uncertainty) that are not represented in the dataset. Adding a voice dimension would require both case expansion and judge calibration against human-scored examples, since voice scoring is more subjective than correctness scoring and the LLM judge would need a more calibrated rubric to score it reliably. v2 would address this. For v1, the calibration dimension partially captures voice-adjacent behavior ("surface the discrepancy without correcting" is partly a voice question), but the rubric is not designed to score voice as a first-class concern.

## What I would do with more time

I would invest in eval depth and external validity first. 34 hand-curated cases gives pattern detection, not statistical confidence, and v1.2 made the limit concrete. The three highest-impact additions are a stratified random sample from HotpotQA or SimpleQA to catch failure modes I did not taxonomize, multi-run averaging on each prompt to separate prompt-effect from judge-stochasticity, and SME calibration on the existing rubric so the judge's scores can be trusted before adding more subjective dimensions like voice.

The next set of moves is structural rather than prompt-only. v1.2 hit diminishing returns on prompt-only changes for `buried_answer`. The fix is `fetch_wikipedia_article(title)` as a second tool, or dropping `exintro=true` to return more of the article body. Either fixes the ceiling that prompt iteration alone cannot reach under the single-tool, lead-extract design.

Voice as a first-class dimension is v2 territory, per the gap section above. It requires case expansion (conversational shapes, not Wikipedia-QA shapes) and judge calibration against human voice-scored examples.

## How I directed the AI

The structural decision was the two-workstream parallel agent split with a shared Pydantic contract, described in choice 6 above. Two agents worked in parallel under my direction, both coding against `src/wiki_qa/agent_contract.py`. The contract was the seam that let them evolve independently. I treated `DECISIONS.md` as the primary surface for my judgment. Every design proposal was logged there with alternatives considered and reasoning, before the change landed in code.

The tactical pushbacks were where most of my judgment showed up day to day. I rejected collapsing parse-warning checks into one binary signal, splitting them into four clusters that map to four prompt-fix directions. I rejected a single citation-format check, splitting it into bracket-presence and no-markdown-links because the failure modes need different fixes. I rejected a single-string `answer` field on `AgentResult`, splitting it into `evidence`, `answer`, and `raw_output` because the prior shape was hiding the output structure. Every such call was logged in `DECISIONS.md` before the change shipped.

For the eval cases, I designed the failure-mode taxonomy, the per-category counts and difficulty mix, and the `expected_behavior` flag semantics. An agent drafted individual cases (questions, gold answers, notes) within those constraints. I reviewed each case and edited where the draft missed the failure mode it was supposed to test. My judgment was taxonomic. I decided which behaviors are worth testing (and which are not, see the voice gap above) and how many cases each one warrants.

For the system prompt, I drafted the initial version, the agent suggested edits, and I accepted or rejected each. The reversed false-premise framing in choice 4 above is the representative example of where I rejected an AI suggestion on customer-tone grounds and the change rippled through the rubric, the dataset flag name, and the prompt's edge-cases section.

The Claude Code transcripts from development are rendered at https://htmlpreview.github.io/?https://github.com/jjanon-git/wiki-q-and-a/blob/main/transcripts/redacted/combined-html/index.html, three sessions concatenated chronologically into 105 prompts across 21 pages. The redaction pass scrubbed 850 local path prefixes and 20 email addresses, and verified no API key bodies were present.

## Time spent

[TODO: fill in once v1 is cut.]
