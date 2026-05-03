"""Tests for the XML formatter that builds tool_result content for the model."""

from __future__ import annotations

from wiki_qa.formatting import (
    format_error_for_model,
    format_results_for_model,
)
from wiki_qa.wikipedia import SearchResult


def _result(
    *,
    title: str = "Albert Einstein",
    url: str = "https://en.wikipedia.org/wiki/Albert_Einstein",
    extract: str = "Albert Einstein was a physicist.",
    page_id: int = 736,
    extract_truncated: bool = False,
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        extract=extract,
        page_id=page_id,
        extract_truncated=extract_truncated,
    )


class TestFormatResults:
    def test_renders_xml_structure(self) -> None:
        results = [
            _result(title="Albert Einstein", page_id=1),
            _result(title="Niels Bohr", page_id=2, url="https://en.wikipedia.org/wiki/Niels_Bohr"),
        ]
        output = format_results_for_model("physics", results)

        assert output.startswith('<search_results query="physics" count="2">')
        assert output.rstrip().endswith("</search_results>")
        assert '<result index="1">' in output
        assert '<result index="2">' in output
        assert "<title>Albert Einstein</title>" in output
        assert "<title>Niels Bohr</title>" in output
        assert "<url>https://en.wikipedia.org/wiki/Albert_Einstein</url>" in output
        assert "<url>https://en.wikipedia.org/wiki/Niels_Bohr</url>" in output

    def test_escapes_xml_special_chars_in_extract(self) -> None:
        result = _result(extract="x < 5 and y > 3 & z = 1")
        output = format_results_for_model("inequalities", [result])
        assert "x &lt; 5 and y &gt; 3 &amp; z = 1" in output
        # Raw < or > inside extract content would break XML parsing
        assert "x < 5" not in output
        assert "y > 3" not in output

    def test_escapes_xml_special_chars_in_title(self) -> None:
        result = _result(title="A & B <thing>")
        output = format_results_for_model("test", [result])
        assert "<title>A &amp; B &lt;thing&gt;</title>" in output

    def test_escapes_quotes_in_query_attribute(self) -> None:
        # Query with double quotes must be escaped in the attribute
        output = format_results_for_model('say "hello"', [_result()])
        assert 'query="say &quot;hello&quot;"' in output

    def test_marks_truncated_extracts(self) -> None:
        results = [
            _result(title="Short", extract="brief", extract_truncated=False),
            _result(title="Long", extract="lots of content...", extract_truncated=True),
        ]
        output = format_results_for_model("q", results)
        # Only the truncated one carries the attribute
        assert '<extract truncated="true">lots of content...</extract>' in output
        assert "<extract>brief</extract>" in output

    def test_handles_empty_extract(self) -> None:
        result = _result(extract="")
        output = format_results_for_model("q", [result])
        assert "<extract>(no extract available)</extract>" in output

    def test_zero_results(self) -> None:
        output = format_results_for_model("nonexistent", [])
        assert '<search_results query="nonexistent" count="0">' in output
        assert "<message>" in output
        # Recovery hint for the model
        assert "different query" in output.lower()
        assert output.rstrip().endswith("</search_results>")

    def test_count_attribute_matches_result_count(self) -> None:
        results = [_result(page_id=i) for i in range(3)]
        output = format_results_for_model("q", results)
        assert 'count="3"' in output


class TestFormatError:
    def test_renders_error_xml(self) -> None:
        output = format_error_for_model(query="batman", reason="Wikipedia rate limit exceeded")
        assert output.startswith('<search_error query="batman">')
        assert "<reason>Wikipedia rate limit exceeded</reason>" in output
        assert "<recovery>" in output
        assert output.rstrip().endswith("</search_error>")

    def test_escapes_special_chars_in_reason(self) -> None:
        output = format_error_for_model(query="q", reason="connection failed: <bad> & broken")
        assert "&lt;bad&gt;" in output
        assert "&amp;" in output

    def test_escapes_query_attribute(self) -> None:
        output = format_error_for_model(query='odd "query"', reason="boom")
        assert 'query="odd &quot;query&quot;"' in output
