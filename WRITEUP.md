# Design rationale

v1.1 of the prompt is in production. Three iteration cycles against a 34-case eval lifted every rubric dimension over v1, with factual_accuracy improving 0.74, citation_quality improving 0.91, and parse warnings dropping from 8 of 34 cases to 0. v1.2 confirmed a specific buried_answer hypothesis (groundedness 2.33 to 3.00) but moved nothing else outside judge-variance, so I kept v1.1. The eval surfaced its own limit at this dataset size. With n=34 and mostly-ceiling performance, this design detects big failures and big wins but cannot statistically distinguish two near-ceiling prompts. The eval also does not score voice or tone, and that gap is deliberate.

The system is a search-by-default agent on Claude Opus 4.7 with a single `search_wikipedia(query)` tool. An LLM-as-judge scores five quality dimensions on a 0-3 scale. Eleven deterministic `behavior_checks` run alongside the judge. The chronological design log is in `DECISIONS.md`; this document is the consolidated rationale.

## Contents

- [The bet: eval-first design](#the-bet-eval-first-design)
- [Six choices that earned the time](#six-choices-that-earned-the-time)
- [What this eval does not measure](#what-this-eval-does-not-measure)
- [Iterations and what they showed](#iterations-and-what-they-showed)
- [What I would do with more time](#what-i-would-do-with-more-time)
- [Use of AI](#use-of-ai)
- [Time spent](#time-spent)

## The bet: eval-first design

The hardest part of this brief is not writing the prompt. It is knowing what to iterate the prompt toward. Most prompt-engineering effort is wasted because the iteration target is fuzzy ("make it better") and without a calibrated way to localize a regression, every change is a guess.

I bet on building the eval first and letting it shape everything that followed. The prompt is in service of the eval, not the other way around.

The two-block `<evidence>...</evidence>` / `<answer>...</answer>` output structure is the clearest example. It exists because the judge needs to score groundedness independently of factual_accuracy. A separate evidence block makes "the answer is right but ungrounded" a visible failure mode. Without it, the model can answer correctly from priors and the eval cannot tell. The structure was added to the prompt to make a specific eval dimension scorable, not because the user-facing output needed it.

This bet also dictated the order of work. I locked the agent-harness contract first, then designed the dataset taxonomy, then wrote the rubric, all before finalizing the system prompt. The prompt iterated against eval output, not intuition.

My prior prompt-engineering experience is mostly with Opus 4.6, not 4.7. Some choices below reflect 4.6-tuned intuition, particularly anything about how firm the search-by-default guidance needs to be or how the model handles structured output. Read the v1 baseline numbers knowing that a chunk of v2's headroom is probably learning where 4.7's defaults differ from 4.6's.

## Six choices that earned the time

### 1. Stress tests over random sampling

v1 uses 34 hand-curated cases across 10 failure-mode categories rather than ~200 from HotpotQA, SimpleQA, or TriviaQA. The trade-off is no statistical confidence on average performance, only pattern detection at this scale. Public-dataset random samples are mostly easy and do not stress the behaviors that drive prompt-engineering decisions. Hand-curation buys signal density. Every case is a hypothesis about a specific failure mode, and a fail points at a specific fix. More time would buy a public-dataset sample as a complement, not a replacement (see "What I would do with more time"). I rejected adversarial generation for v1 because it adds setup time and yields cases without curation. I would rather have 34 deliberate cases than 100 unvalidated.

### 2. Don't collapse signals that map to different fixes

This is a recurring pattern across the design. Citation conventions split into two checks (`has_bracket_citations`, `no_markdown_links`) instead of one "citation format" check, because zero citations and forbidden markdown citations are different failure modes with different fixes. Parse warnings cluster into four buckets (missing-block, unclosed-tag, empty-block, non-canonical) rather than collapsing into one or splitting nine ways, because the four clusters map to four prompt-fix directions. The rubric forbids cross-dimension aggregation entirely. Averaging factual_accuracy and citation_quality together is meaningless and obscures failures. The rule is not "split everything." It is "if two fails would suggest different remediations, keep them visible."

### 3. Deterministic checks alongside the LLM judge

The harness runs 11 deterministic `behavior_checks` (did the model search when it should have? does the answer have inline citations? did the parser emit any warnings?) for every case, separately from the 5-dim rubric. Each catches what the other cannot. The judge scores groundedness or calibration with judgment that no pattern-match could replicate. The deterministic checks catch format failures that the judge might score around if the answer happens to read well. The two signals stay in separate blocks of the result so a hard format-fail does not get conflated with rubric scores. The judge also gets `parse_warnings` as informational context, with explicit guidance to use them to interpret the answer (an unsupported claim alongside `empty_evidence_block` reads as a populating failure, not a hallucination) and not to double-count by docking rubric points, since the deterministic checks already handle that signal.

### 4. Surface premise discrepancies, don't correct them

The original framing for the `false_premise` category had the model "correct" wrong premises ("Einstein actually won the 1921 Nobel for the photoelectric effect, not relativity"). I reversed this mid-design on customer-tone grounds. The model is not the arbiter of truth and should not position itself as correcting the user. Its job is to make the disagreement legible ("Wikipedia indicates X; the question's premise was Y") and let the user reconcile it. The reversal rippled through three places. The rubric's `calibration` dimension now penalizes assertive correction even if the fact is right. The dataset flag was renamed `must_correct_premise` to `must_surface_premise_discrepancy`. The system prompt's edge-cases section documents the descriptive-surfacing behavior. The eval can grade this kind of choice because the rubric was rewritten alongside the prompt change. Without the joint update, the judge would have kept rewarding "correction."

### 5. Two-block evidence/answer output for auditability

Every response emits an evidence block (quoted passages from cited articles) followed by an answer block (prose with inline `[Article Title]` brackets and a plain-text `Sources:` section). The structure was chosen for auditability. The judge can compare claims in the answer to passages in evidence rather than parsing claim-citation pairs out of prose. The "evidence-block-as-authoritative" rule that landed in v1.1 makes this load-bearing. Every claim in the answer must trace to a quoted passage in evidence. That rule is what made `citation_quality` jump 0.91 and let the judge's groundedness score actually mean something.

### 6. Two parallel agents, one Pydantic contract

I split the implementation into two parallel workstreams under my direction. Workstream A built the Wikipedia integration, agent loop, parser, and prompt iterations. Workstream B built the eval harness (dataset, runner, judge, behavior_checks). Both coded against `src/wiki_qa/agent_contract.py`, a Pydantic `BaseModel` with `frozen=True, extra="forbid"`. The choice of Pydantic over `@dataclass(frozen=True)` was deliberate. Pydantic gives validation at construction (typed errors instead of silent drops), `extra="forbid"` makes contract drift loud rather than silent, JSON round-trip out of the box for `results.jsonl`, and schema introspection so cross-workstream coordination does not require reading source files. The contract earned its keep when workstream A added the `ParseWarning` enum and `parse_warnings` field. Workstream B picked the change up without breakage, and I added four cluster-based deterministic checks on the new signal in the same session.

## What this eval does not measure

The v1 eval measures correctness: factual_accuracy, groundedness, citation_quality, search_efficiency, calibration. These are the dimensions that determine whether the answer is right. They do not measure voice or tone, whether uncertainty is expressed with confidence or apologetically, whether surfacing a false premise feels respectful or corrective, whether the agent's prose has the warmth that distinguishes a thoughtful response from a merely competent one. This gap is real and deliberate. Voice failures do not surface cleanly in Wikipedia QA categories. They live in conversational shapes (frustrated users, ambiguous emotional contexts, situations requiring graceful uncertainty) that are not represented in the dataset. Adding a voice dimension would require both case expansion and judge calibration against human-scored examples, since voice scoring is more subjective than correctness scoring and the LLM judge would need a more calibrated rubric to score it reliably. v2 would address this. For v1, the calibration dimension partially captures voice-adjacent behavior ("surface the discrepancy without correcting" is partly a voice question), but the rubric is not designed to score voice as a first-class concern.

## Iterations and what they showed

Three runs against the same 34-case eval. v1 to v1.1 was a big lift driven by two obvious failure modes (parse warnings 8/34 to 0/34, factual_accuracy +0.74, citation_quality +0.91). v1.1 to v1.2 confirmed a specific local hypothesis on `buried_answer` (groundedness 2.33 to 3.00) but moved nothing else outside judge-variance, so I kept v1.1 in production. The eval surfaced its own limit at this dataset size. At n=34 with mostly-ceiling performance, the eval can find big failures and big wins but cannot statistically distinguish two near-ceiling prompts.

### v1 baseline

v1 produced per-dimension means of factual 2.21, grounded 2.29, citation 1.94 (the weakest), search 2.82, and calibration 2.32. Two failure modes dominated.

The first was a parse-warning blowout on non-search cases. All 8 non-search cases (4 `negative_capability` + 4 `unanswerable_*`) emitted `missing_evidence_block` + `missing_answer_block`. The agent's behavior was correct. It did not search arithmetic and refused real-time questions cleanly. But the v1 prompt's `<evidence>` / `<answer>` structure was framed entirely around grounding-from-search. With no search, the model dropped the wrappers, the parser scored them malformed, and the judge gave 0s on factual, grounded, and citation.

The second was citation over-listing. The `Sources:` section listed more articles than the prose actually cited inline.

### v1.1: the big lift

Three focused changes addressed the v1 failures. First, wrappers were required even on non-search cases (e.g., `<evidence>none, Wikipedia is not the appropriate source</evidence>`). Second, the prompt was tightened to "only list sources you cited inline." Third, an evidence-block-as-authoritative rule was added: claims must trace to a quoted passage. Every dimension improved, parse warnings dropped to 0/34, and every previously-failing behavior check went to 0 fails. The 0.91 jump on citation_quality is the largest single delta in the iteration story.

### v1.2: confirmed the local hypothesis, did not justify shipping

I tightened the marked-inference rule to a binary in v1.2. Every claim either traces to evidence, is explicitly marked as inference, or does not appear. This targeted the v1.1 `buried_answer` groundedness regression (3.00 to 2.33). The change worked locally. buried_answer groundedness recovered to 3.00 (+0.67). Every other delta was within judge-noise (global Δ between -0.09 and +0.03). I kept v1.1 in production and preserved v1.2 as `prompts/system_v1_2.md` for reference.

v1.2 surfaced a methodology limit. At this n with mostly-ceiling performance, deltas under ~0.30 are inside judge-stochasticity, meaning a real v1.2 prompt regression and a v1.1 prompt regression are indistinguishable at this dataset size. The eval was designed for surfacing big failures (v1's 8/34 parse warnings) and big wins (v1 to v1.1), not for ranking similar prompts at near-ceiling. The right next investment is not another prompt iteration. It is bigger n and multi-run averaging, both discussed below.

### Judge calibration: spot-checked against my own reading

I ran the calibration workflow against the v1.1 baseline before declaring the eval numbers trustworthy. The sampler stratified 8 cases across rubric dimensions and score buckets (low ≤1, high =3) so the sample wasn't all near-ceiling cases. I scored each case independently and then ran `calibrate analyze`.

Result: 100% agreement (within ±1) on factual_accuracy, groundedness, citation_quality, and search_efficiency. 88% on calibration with one disagreement, where I scored `false_premise_002` (Curie chemistry Nobel) two points higher than the judge — the judge was the more conservative party on a subtle joint-discovery nuance, which is the direction I want when grounding-quality is the goal. Every dimension sits well below the 25% disagreement threshold I set as the rubric-revision trigger, so no rubric or judge-prompt change is indicated. The per-dim means in `tests/eval/iterations.md` are trustworthy at this calibration depth.

Caveats are real and load-bearing. 8 cases is small relative to the 34, the judge is a single Opus 4.7 instance with no ensemble check, and I am the only human in the loop. The result says "the judge agrees with my reading at this depth", not "the judge is correct" or "the rubric is well-defined for novel cases". Real validation would mean multiple SMEs scoring 50+ cases each with inter-rater agreement on the human side, discussed below.

## What I would do with more time

The most valuable next investment is eval depth and external validity. 34 hand-curated cases gives pattern detection, not statistical confidence, and v1.2 made the limit concrete. With more time I would add a stratified random sample from HotpotQA or SimpleQA (~200 cases) as a complement to the curated set. This catches failure modes I did not think to taxonomize and gives a generalization baseline. I would also bump the iteration-signal categories to 20+ cases each so per-dimension means could carry confidence intervals. The calibration workflow exists today (`calibrate sample` and `calibrate analyze` subcommands on the CLI) and was run as the spot-check described above. The right run is multiple SMEs scoring 50+ cases each with per-dimension agreement statistics. Multi-run averaging on the same dataset (run each prompt 3-5 times and average) would separate prompt-effect from judge-stochasticity and is the only way to distinguish v1.1 from v1.2 at all. Diverse gold sources, including cases written by domain experts and from de-identified customer logs, would escape the hand-curation echo chamber.

The next set of moves is more iteration cycles, with structural moves rather than prompt-only changes. v1.2 hit diminishing returns on prompt-only changes for `buried_answer`. The structural fix is to add `fetch_wikipedia_article(title)` as a second tool, or to drop `exintro=true` and return more of the article body. Either fixes the ceiling that prompt iteration alone cannot reach under the single-tool, lead-extract design. New failure-mode categories worth adding include `controversial_topic` (where Wikipedia itself is contested), `policy_advice` (legal, medical, financial topics where Wikipedia could answer but the system should not), and `fresh_search` (where the cutoff caveat needs more care). Per-category search budgets are also worth experimenting with, since 5 calls is one global cap and multi-hop probably wants more while simple_factual probably wants less.

Voice as a first-class dimension is v2 territory. As the gap section above notes, this requires case expansion (conversational shapes, not Wikipedia-QA shapes) and judge calibration against human voice-scored examples. I would not attempt voice scoring without first running the SME calibration loop on the existing correctness rubric, since voice is more subjective and the LLM judge needs a stronger calibration baseline before adding subjective dimensions.

Smaller items that would improve the system without changing its bones include multi-turn for clarifying-question disambiguation, a judge ensemble that runs multiple judges per case and takes the median while flagging disagreement, cost and latency dashboards, transcript redaction at extract time, and per-version prompt diffs in `iterations.md` so the prompt-evolution trail is queryable.

## Use of AI

The brief expects AI tooling during development and asks for transcripts alongside the code. The two-workstream split described in choice 6 above put one agent on the Wikipedia, agent loop, and parser side and another on the eval harness, both coding against the shared Pydantic contract. I directed both, which meant defining the contract, specifying the architecture, making the design calls, and pushing back on proposals when needed. Representative pushbacks included splitting collapsed parse-warning checks into four clusters, splitting a single citation check into bracket-presence and no-markdown-links, and splitting the single-string `answer` field into `evidence`, `answer`, and `raw_output`. Every design decision in `DECISIONS.md` was either mine or run by me before the change landed. The agents implemented them.

For the eval cases, I designed the failure-mode taxonomy, the per-category counts and difficulty mix, and the `expected_behavior` flag semantics. An agent drafted individual cases (questions, gold answers, notes) within those constraints. I reviewed each case before it entered the dataset and edited where the draft missed the failure mode it was supposed to test. The judgment that mattered here was taxonomic. I decided which behaviors are worth testing (and which are not, see the voice gap section above) and how many cases each one warrants.

For the system prompt, I drafted the initial version, the agent suggested edits, and I accepted or rejected each. The reversed false-premise framing (correct to surface) is the representative example of where I rejected an AI suggestion on customer-tone grounds and the change rippled through the rubric, the dataset flag name, and the prompt's edge-cases section.

I did not let the AI work unsupervised on three things. I defined the rubric dimensions, set the agent-harness contract, and required every design proposal to be written into `DECISIONS.md` before the change landed. The decision log is the surface where my judgment is auditable.

Transcripts: [TODO: link to the Claude Code transcript artifact when generated.]

## Time spent

[TODO: fill in once v1 is cut.]
