"""Wikipedia search via the MediaWiki action API.

Single-tool design: one HTTP call per search, using `generator=search` with
`prop=extracts` so we get search hits and lead-section extracts in one round
trip. Retries on 429 with `Retry-After` if present, otherwise exponential
backoff (1s/2s/4s, max 3 retries). Other HTTP errors and timeouts raise
`WikipediaSearchError` so the agent dispatcher can surface them as a
`<search_error>` tool result and let the model recover.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

MEDIAWIKI_API_URL: Final = "https://en.wikipedia.org/w/api.php"
USER_AGENT: Final = "wiki-qa-takehome/0.1 (contact via repo issues)"

_DEFAULT_TIMEOUT: Final = 10.0
_MAX_429_RETRIES: Final = 3
_BACKOFF_SCHEDULE: Final = (1.0, 2.0, 4.0)


class WikipediaSearchError(Exception):
    """Raised when a Wikipedia search request fails after any retries."""


@dataclass(frozen=True)
class SearchResult:
    """One result from a Wikipedia search."""

    title: str
    url: str
    extract: str
    page_id: int
    extract_truncated: bool


def search_wikipedia(
    query: str,
    *,
    limit: int = 3,
    extract_chars: int = 2000,
) -> list[SearchResult]:
    """Search English Wikipedia and return up to `limit` results with extracts.

    Each result includes the lead section truncated to ~`extract_chars` characters.
    `extract_truncated` is True when the returned extract reached the limit, which
    is the model's signal that more content exists in the article body.
    """
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": str(limit),
        "prop": "extracts",
        "exintro": "1",
        "explaintext": "1",
        "exchars": str(extract_chars),
        "redirects": "1",
    }
    headers = {"User-Agent": USER_AGENT}

    payload = _request_with_retries(params, headers)
    pages = _extract_pages(payload)
    return [_to_search_result(page, extract_chars) for page in pages]


def _request_with_retries(params: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    """Issue the HTTP request, retrying on 429 up to `_MAX_429_RETRIES` times."""
    retries = 0
    while True:
        try:
            response = httpx.get(
                MEDIAWIKI_API_URL,
                params=params,
                headers=headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        except httpx.TimeoutException as e:
            raise WikipediaSearchError(f"Wikipedia request timed out: {e}") from e
        except httpx.HTTPError as e:
            raise WikipediaSearchError(f"Wikipedia request failed: {e}") from e

        if response.status_code == 429:
            if retries >= _MAX_429_RETRIES:
                raise WikipediaSearchError(
                    f"Wikipedia rate limit exceeded after {_MAX_429_RETRIES} retries"
                )
            wait = _retry_wait(response, retries)
            time.sleep(wait)
            retries += 1
            continue

        if response.status_code >= 400:
            raise WikipediaSearchError(f"Wikipedia returned HTTP {response.status_code}")

        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise WikipediaSearchError(f"Wikipedia returned invalid JSON: {e}") from e
        return data


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    """Use Retry-After header if present, else exponential backoff."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]


def _extract_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the page list from a MediaWiki action-API response and sort by index.

    MediaWiki returns pages keyed by pageid in a dict; their `index` field is the
    search-rank order. We sort by index so result[0] is the top hit.
    """
    query_block = payload.get("query")
    if not query_block:
        return []
    pages_dict = query_block.get("pages")
    if not pages_dict:
        return []
    pages = list(pages_dict.values())
    pages.sort(key=lambda p: p.get("index", 0))
    return pages


def _to_search_result(page: dict[str, Any], extract_chars: int) -> SearchResult:
    title = str(page.get("title", ""))
    extract = str(page.get("extract", ""))
    return SearchResult(
        title=title,
        url=_title_to_url(title),
        extract=extract,
        page_id=int(page.get("pageid", 0)),
        # MediaWiki doesn't return an explicit truncation flag for `exchars`. We
        # treat any extract whose length equals the requested cap as truncated;
        # this errs slightly toward over-marking (an article whose lead is exactly
        # N chars long would be flagged too) but the false-positive cost is just
        # a hint to the model that it could re-search — cheap.
        extract_truncated=len(extract) >= extract_chars and extract_chars > 0,
    )


def _title_to_url(title: str) -> str:
    """Build the canonical en.wikipedia.org URL for a page title."""
    slug = title.replace(" ", "_")
    return f"https://en.wikipedia.org/wiki/{slug}"
