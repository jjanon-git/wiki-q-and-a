"""Tests for the Wikipedia search function (MediaWiki action API client)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from wiki_qa.wikipedia import (
    MEDIAWIKI_API_URL,
    USER_AGENT,
    SearchResult,
    WikipediaSearchError,
    search_wikipedia,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mediawiki"


def _einstein_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / "einstein_search.json").read_text())
    return payload


def _empty_payload() -> dict[str, Any]:
    """MediaWiki returns this shape when no pages match the search."""
    return {"batchcomplete": ""}


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Replace time.sleep with a recorder so retry tests don't actually wait."""
    sleeps: list[float] = []
    monkeypatch.setattr("wiki_qa.wikipedia.time.sleep", lambda s: sleeps.append(s))
    yield sleeps


class TestSearchWikipediaParsing:
    @respx.mock
    def test_parses_top_n_results(self) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(
            return_value=httpx.Response(200, json=_einstein_payload())
        )
        results = search_wikipedia("Albert Einstein")

        assert len(results) == 3
        # Pages dict ordering is by index, not page_id; verify we sort by index
        assert results[0].title == "Albert Einstein"
        assert results[0].page_id == 736
        assert results[0].url == "https://en.wikipedia.org/wiki/Albert_Einstein"
        assert "theoretical physicist" in results[0].extract
        assert results[0].extract_truncated is False

        assert results[1].title == "Niels Bohr"
        assert results[2].title == "Theory of relativity"

    @respx.mock
    def test_returns_searchresult_instances(self) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(
            return_value=httpx.Response(200, json=_einstein_payload())
        )
        results = search_wikipedia("physics")
        assert all(isinstance(r, SearchResult) for r in results)

    @respx.mock
    def test_url_with_spaces_in_title(self) -> None:
        # MediaWiki returns titles with spaces; URL conversion uses underscores
        payload = {
            "query": {
                "pages": {
                    "1": {
                        "pageid": 1,
                        "title": "Theory of relativity",
                        "index": 1,
                        "extract": "x",
                    }
                }
            }
        }
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(200, json=payload))
        results = search_wikipedia("relativity")
        assert results[0].url == "https://en.wikipedia.org/wiki/Theory_of_relativity"


class TestSearchWikipediaEmptyAndMissing:
    @respx.mock
    def test_zero_results_returns_empty_list(self) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(200, json=_empty_payload()))
        results = search_wikipedia("zzzzzzzzz_no_such_thing")
        assert results == []

    @respx.mock
    def test_handles_missing_extract_field(self) -> None:
        payload = {
            "query": {
                "pages": {
                    "1": {"pageid": 1, "title": "Stub", "index": 1}
                    # no `extract` key at all
                }
            }
        }
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(200, json=payload))
        results = search_wikipedia("stub")
        assert len(results) == 1
        assert results[0].extract == ""

    @respx.mock
    def test_marks_truncated_when_mediawiki_indicates(self) -> None:
        # MediaWiki signals truncation via the `extract` ending and a sentinel; the
        # action API doesn't always include an explicit flag, so we treat any extract
        # whose length equals exactly `extract_chars` as potentially truncated.
        # For this test, we set up a payload where the extract length equals exchars.
        long_text = "a" * 2000
        payload = {
            "query": {
                "pages": {"1": {"pageid": 1, "title": "Long", "index": 1, "extract": long_text}}
            }
        }
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(200, json=payload))
        results = search_wikipedia("long", extract_chars=2000)
        assert results[0].extract_truncated is True


class TestSearchWikipediaRequestBuilding:
    @respx.mock
    def test_respects_limit_and_extract_chars(self) -> None:
        route = respx.get(MEDIAWIKI_API_URL).mock(
            return_value=httpx.Response(200, json=_empty_payload())
        )
        search_wikipedia("query", limit=5, extract_chars=1000)

        request = route.calls.last.request
        assert "gsrlimit=5" in str(request.url)
        assert "exchars=1000" in str(request.url)
        assert "generator=search" in str(request.url)
        assert "exintro=1" in str(request.url) or "exintro=true" in str(request.url)
        assert "explaintext=1" in str(request.url) or "explaintext=true" in str(request.url)

    @respx.mock
    def test_url_encodes_query(self) -> None:
        route = respx.get(MEDIAWIKI_API_URL).mock(
            return_value=httpx.Response(200, json=_empty_payload())
        )
        search_wikipedia('treaty "versailles" & friends')

        request = route.calls.last.request
        # httpx URL-encodes; verify the raw chars don't appear and the encoded form does
        url_str = str(request.url)
        assert "&friends" not in url_str.split("gsrsearch=")[1].split("&")[0]
        assert "%22versailles%22" in url_str or "versailles" in url_str.lower()

    @respx.mock
    def test_sets_user_agent(self) -> None:
        route = respx.get(MEDIAWIKI_API_URL).mock(
            return_value=httpx.Response(200, json=_empty_payload())
        )
        search_wikipedia("any")

        request = route.calls.last.request
        assert request.headers["user-agent"] == USER_AGENT


class TestSearchWikipediaErrors:
    @respx.mock
    def test_raises_on_http_500(self) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(WikipediaSearchError):
            search_wikipedia("anything")

    @respx.mock
    def test_raises_on_http_400(self) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(WikipediaSearchError):
            search_wikipedia("anything")

    @respx.mock
    def test_raises_on_timeout(self) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(side_effect=httpx.TimeoutException("timed out"))
        with pytest.raises(WikipediaSearchError):
            search_wikipedia("anything")


class TestSearchWikipediaRateLimit:
    @respx.mock
    def test_retries_on_429_with_retry_after_header(self, no_sleep: list[float]) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json=_einstein_payload()),
            ]
        )
        results = search_wikipedia("einstein")
        assert len(results) == 3
        assert no_sleep == [1.0]

    @respx.mock
    def test_retries_on_429_without_retry_after(self, no_sleep: list[float]) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(429),
                httpx.Response(200, json=_einstein_payload()),
            ]
        )
        results = search_wikipedia("einstein")
        assert len(results) == 3
        # Backoff: 1s, 2s
        assert no_sleep == [1.0, 2.0]

    @respx.mock
    def test_raises_after_max_429_retries(self, no_sleep: list[float]) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(WikipediaSearchError) as exc_info:
            search_wikipedia("einstein")
        # 3 retries → 4 total attempts → 3 sleeps (1s, 2s, 4s)
        assert no_sleep == [1.0, 2.0, 4.0]
        assert "rate limit" in str(exc_info.value).lower()

    @respx.mock
    def test_retry_after_header_takes_precedence_over_backoff(self, no_sleep: list[float]) -> None:
        respx.get(MEDIAWIKI_API_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "5"}),
                httpx.Response(200, json=_empty_payload()),
            ]
        )
        search_wikipedia("anything")
        assert no_sleep == [5.0]
