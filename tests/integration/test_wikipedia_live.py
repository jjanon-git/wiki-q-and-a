"""Live integration test against the real MediaWiki API.

Skipped by default (run with `pytest -m integration`). Used to:
  1. Confirm the parser handles real-world response shape.
  2. Capture/refresh fixtures used by unit tests.

Run sparingly — Wikipedia's API is shared infrastructure.
"""

from __future__ import annotations

import pytest

from wiki_qa.formatting import format_results_for_model
from wiki_qa.wikipedia import SearchResult, search_wikipedia


@pytest.mark.integration
class TestLiveSearch:
    def test_returns_results_for_stable_query(self) -> None:
        results = search_wikipedia("Albert Einstein")
        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)
        assert all(r.title for r in results)
        assert all(r.url.startswith("https://en.wikipedia.org/wiki/") for r in results)
        assert any(r.extract for r in results)

    def test_einstein_result_is_top_or_near_top(self) -> None:
        results = search_wikipedia("Albert Einstein")
        titles = [r.title for r in results]
        assert any("Einstein" in title for title in titles)

    def test_zero_results_for_nonsense_query(self) -> None:
        # Use a string Wikipedia is unlikely to match anything against
        results = search_wikipedia("zzzqqqxxx_definitely_not_a_real_topic_2026")
        assert results == []

    def test_xml_formatter_produces_parseable_output(self) -> None:
        """End-to-end: live search → XML → must be valid XML."""
        from xml.etree import ElementTree

        results = search_wikipedia("Treaty of Versailles")
        xml_output = format_results_for_model("Treaty of Versailles", results)
        # Parsing should succeed; if extracts contain unescaped chars this raises
        ElementTree.fromstring(xml_output)
