# Plan: `search_wikipedia` and tool implementation

Scope: the Wikipedia retrieval layer and the tool surface the agent sees. Not the agent loop, not the system prompt, not the eval harness.

## What "the tool" actually is

Two distinct things, easy to conflate:

1. **The Python function** `search_wikipedia(query: str) -> list[SearchResult]` — pure code. Hits MediaWiki, parses, returns structured results.
2. **The tool definition** — a JSON schema (name, description, input_schema) registered with the Anthropic API. The model never sees the function; it sees the schema and the stringified result we return as `tool_result` content.

The Python function is testable code. The tool definition is a prompt — its description and parameter docs steer model behavior.

## MediaWiki strategy

Use the action API with a generator to do search + extract in one HTTP call:

```
GET https://en.wikipedia.org/w/api.php
  ?action=query
  &format=json
  &generator=search
  &gsrsearch=<query>
  &gsrlimit=3
  &prop=extracts
  &exintro=true
  &explaintext=true
  &exchars=2000
  &redirects=1
```

Returns the top-3 search hits as page objects, each with a plain-text lead-section extract truncated to ~2000 chars, redirects followed. One round trip, no per-result fetch.

Alternatives considered:
- Two-step (search → per-title summary fetch via REST `/page/summary`): more requests, more failure modes, no quality gain for v1.
- Local Wikipedia dump or vector index: vastly out of scope for "lightweight v1."
- `wikipedia` PyPI package: thin wrapper, opaque error handling, brings its own opinions. Direct API call is not much more code and is fully ours.

## Return shape (Python)

```python
@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    extract: str
    page_id: int
    extract_truncated: bool   # true if MediaWiki cut off the lead at exchars

def search_wikipedia(
    query: str,
    *,
    limit: int = 3,
    extract_chars: int = 2000,
) -> list[SearchResult]: ...

def format_results_for_model(query: str, results: list[SearchResult]) -> str: ...
```

The agent dispatcher calls `search_wikipedia(query)` and passes `format_results_for_model(query, ...)` as the `tool_result` content (a single string).

## Format for the model — XML

Anthropic's models attend well to XML-tagged structure. XML also makes it natural for the model to reference specific results in its reasoning ("based on result 2..."), and gives error/zero-result responses a clear shape.

**Successful results:**
```xml
<search_results query="Albert Einstein" count="3">
<result index="1">
<title>Albert Einstein</title>
<url>https://en.wikipedia.org/wiki/Albert_Einstein</url>
<extract>Albert Einstein (14 March 1879 – 18 April 1955) was a German-born theoretical physicist...</extract>
</result>
<result index="2">
<title>...</title>
<url>...</url>
<extract truncated="true">...</extract>
</result>
</search_results>
```

**Zero results:**
```xml
<search_results query="..." count="0">
<message>No Wikipedia results found. Try a different query.</message>
</search_results>
```

**Error:**
```xml
<search_error query="...">
<reason>Wikipedia rate limit exceeded after 3 retries.</reason>
<recovery>Try again with a different query, or answer without this tool if you can do so honestly.</recovery>
</search_error>
```

**Escaping:** extracts come back from MediaWiki as plain text (we use `explaintext=true`), but raw `<`, `>`, `&` characters can still appear (math, code, prose). HTML-escape extract content before embedding. Use `xml.sax.saxutils.escape` — small dep, stdlib, well-tested.

**Truncation marker:** `truncated="true"` on `<extract>` tells the model the lead section was longer than `exchars`. Under the single-tool design, this is meaningful signal: "if you need more, re-search with a more specific query."

## Tool definition (the prompt-engineering surface)

```python
SEARCH_WIKIPEDIA_TOOL = {
    "name": "search_wikipedia",
    "description": (
        "Search English Wikipedia and return the top results with extracts.\n\n"
        "Use this tool by default for any user question that benefits from "
        "being grounded in or verified against an external source. The only "
        "exceptions are questions Wikipedia cannot reasonably answer: "
        "arithmetic and calculations, code generation, opinion or preference "
        "questions, and questions about content already provided in the "
        "conversation. When in doubt, search.\n\n"
        "For multi-part or synthesis questions, decompose into sub-searches "
        "(one per facet) and combine the findings.\n\n"
        "Returns up to 3 results, each with title, URL, and a plain-text "
        "extract from the article's lead section (truncated to ~2000 "
        "characters; the response indicates when truncation occurred).\n\n"
        "After receiving results, assess whether they contain what is needed. "
        "If not, search again with a refined query, or with a different "
        "component of a multi-part question. If after several searches the "
        "information is genuinely not available in Wikipedia, say so rather "
        "than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A search query targeting the article(s) most likely to "
                    "contain the needed information. Favor specific noun "
                    "phrases over conversational questions: 'Treaty of "
                    "Versailles signatories' rather than 'who signed the "
                    "Treaty of Versailles?'. For multi-part questions, issue "
                    "separate calls — each query targeting one facet."
                ),
            }
        },
        "required": ["query"],
    },
}
```

This is a v1 draft. The descriptions are themselves a prompt and likely to iterate based on eval results. Hypotheses to watch:
- Does the model still answer from priors when it should search? → strengthen "by default" further.
- Does the model under-decompose multi-part questions? → add a worked example to the multi-part guidance.
- Does the model give up after one failed search instead of refining? → strengthen the assess-and-refine paragraph.
- Does the model conflate truncation with end-of-article? → adjust the truncation copy.

## Edge cases handled in v1

| Case | Behavior |
|---|---|
| Zero results | Return empty list; formatter emits `<search_results count="0">` with a hint |
| Disambiguation page in results | Pass through — the extract typically lists candidates, model handles |
| Empty `extract` field for a hit | Include title + URL with `<extract>(no extract available)</extract>` |
| Truncated extract | `truncated="true"` attribute on `<extract>` |
| HTTP 4xx (non-429) | Raise `WikipediaSearchError`; agent surfaces as `<search_error>` |
| HTTP 5xx | Raise `WikipediaSearchError`; agent surfaces as `<search_error>` |
| HTTP 429 | Respect `Retry-After` header if present; otherwise exponential backoff at 1s/2s/4s; max 3 retries; then raise `WikipediaSearchError` |
| Network timeout | `httpx.TimeoutException` → wrapped in `WikipediaSearchError` |
| Unicode / special chars in query | `httpx` URL-encodes automatically |
| Extract contains `<`, `>`, `&` | HTML-escape before embedding in XML |
| Query > some absurd length | No explicit limit in v1; MediaWiki will reject; we surface the error |

Explicitly **not** in v1: on-disk caching, language selection, redirect-trace exposure, image/media. Add if eval evidence demands.

## HTTP client

`httpx` (sync). Reasons: typed, modern, mockable via `respx`. Sync because the agent is sequential. Easy to migrate to async later for parallel eval runs if needed.

User-Agent header: Wikipedia's policy expects an identifying UA. Set `wiki-qa-takehome/0.1 (jjanon@gmail.com)` — failure to do this can get requests rate-limited or blocked. Confirm email is OK or swap.

## Test plan (TDD)

Order matches red-green-refactor — each test written first, fails, then minimal impl.

**Unit tests** (`tests/unit/test_wikipedia.py`, mocked with `respx`):

1. `test_format_results_for_model_renders_xml_structure` — pure function; easiest entry point. Assert root `<search_results>`, per-result `<title>/<url>/<extract>`, `query` and `count` attrs.
2. `test_format_results_escapes_xml_special_chars_in_extract` — extract containing `<`, `>`, `&` is escaped.
3. `test_format_results_marks_truncated_extracts` — `truncated="true"` attribute appears when set.
4. `test_format_results_zero_results_xml` — emits `<search_results count="0">` with hint.
5. `test_format_results_error_xml` — formatter helper for the error case emits `<search_error>` with reason and recovery hint.
6. `test_search_wikipedia_parses_top_n_results` — mock MediaWiki JSON → assert SearchResult fields populated correctly, including `extract_truncated`.
7. `test_search_wikipedia_zero_results` — empty `pages` from API → empty list, no exception.
8. `test_search_wikipedia_respects_limit_and_extract_chars` — assert query string contains `gsrlimit=N` and `exchars=M`.
9. `test_search_wikipedia_url_encodes_query` — query with spaces/unicode/quotes → URL is correct.
10. `test_search_wikipedia_raises_on_http_5xx` — mock 500 → `WikipediaSearchError`.
11. `test_search_wikipedia_raises_on_timeout` — mock timeout → `WikipediaSearchError`.
12. `test_search_wikipedia_handles_missing_extract_field` — page with no extract → `SearchResult(extract="(no extract available)")`.
13. `test_search_wikipedia_sets_user_agent` — assert outbound request has the UA header.
14. `test_search_wikipedia_retries_on_429_with_retry_after_header` — first response 429 with `Retry-After: 1`, second response 200; verify single retry and final results returned.
15. `test_search_wikipedia_retries_on_429_without_retry_after` — 429 without header → uses 1s/2s/4s backoff; verify retries occur.
16. `test_search_wikipedia_raises_after_max_429_retries` — 4 consecutive 429s → `WikipediaSearchError` with rate-limit message.

(For the backoff tests, inject the sleep function so tests don't actually wait.)

**Tool-definition tests** (`tests/unit/test_tools.py`):

17. `test_search_wikipedia_tool_schema_required_fields` — name, description, input_schema present; `query` is required string. Cheap regression guard.

**Integration test** (`tests/integration/test_wikipedia_live.py`, `@pytest.mark.integration`, skipped by default):

18. `test_live_search_returns_results_for_stable_query` — real call for "Albert Einstein"; assert ≥1 result with non-empty extract. Run once to capture a real response, save as a fixture for the unit tests so they reflect actual API shape; re-run when we suspect the API changed.

## File layout (this scope only)

```
src/wiki_qa/
  __init__.py
  wikipedia.py          # SearchResult, search_wikipedia, WikipediaSearchError
  formatting.py         # format_results_for_model, format_error_for_model
  tools.py              # SEARCH_WIKIPEDIA_TOOL definition
tests/
  unit/
    test_wikipedia.py
    test_formatting.py
    test_tools.py
  integration/
    test_wikipedia_live.py
  fixtures/
    mediawiki/
      einstein_search.json
```

`agent.py` and `cli.py` are deliberately out of this plan — separate doc when we get there.

## What this plan deliberately leaves open

- **N=3 and 2000 chars are starting points, not final.** Real values come from eval failures: too few results → bump N; extracts not enough context → bump chars; too much context bloat → cut. Starting at 2000 (rather than 500) because under-context shows up as wrong answers, while over-context shows up as token cost — wrong answers are the more expensive failure mode in evaluation.
- **Whether to add a separate `fetch_wikipedia_article(title)` tool.** Decision deferred until we see what the single-tool agent struggles with. If it repeatedly issues near-identical re-searches because extracts are still too thin even at 2000 chars, that's the signal to add the second tool.
- **Whether the tool result should expose `page_id` to the model.** Currently no — it's noise unless we add a follow-up tool that takes a page_id. Revisit with `fetch_article` decision.
- **Whether to surface the truncation marker in the tool description copy.** v1 mentions it briefly. If the model misinterprets truncation (e.g., concludes the article ends there), we'll expand.
