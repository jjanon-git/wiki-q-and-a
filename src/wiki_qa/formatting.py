"""XML formatters for Wikipedia search tool results passed to the model."""

from __future__ import annotations

from xml.sax.saxutils import escape

from wiki_qa.wikipedia import SearchResult


def _attr(value: str) -> str:
    """Render an XML attribute value, always double-quoted with `&quot;` escapes.

    `xml.sax.saxutils.quoteattr` picks single quotes when the value contains a
    double quote; both are valid XML but mixed quoting is harder to read and
    test against. Force one form.
    """
    return '"' + escape(value, {'"': "&quot;"}) + '"'


def format_results_for_model(query: str, results: list[SearchResult]) -> str:
    """Render search results as XML for inclusion in a tool_result content block.

    The model attends well to XML structure; tagged results also make it natural
    for the model to reference specific items in its reasoning. Special chars in
    titles, URLs, and extracts are HTML-escaped so the model receives parseable XML.
    """
    if not results:
        return (
            f'<search_results query={_attr(query)} count="0">\n'
            "<message>No Wikipedia results found. Try a different query.</message>\n"
            "</search_results>"
        )

    lines: list[str] = [f'<search_results query={_attr(query)} count="{len(results)}">']
    for index, result in enumerate(results, start=1):
        lines.append(f'<result index="{index}">')
        lines.append(f"<title>{escape(result.title)}</title>")
        lines.append(f"<url>{escape(result.url)}</url>")
        extract_text = escape(result.extract) if result.extract else "(no extract available)"
        if result.extract_truncated:
            lines.append(f'<extract truncated="true">{extract_text}</extract>')
        else:
            lines.append(f"<extract>{extract_text}</extract>")
        lines.append("</result>")
    lines.append("</search_results>")
    return "\n".join(lines)


def format_error_for_model(*, query: str, reason: str) -> str:
    """Render a tool_result content block for a failed search call.

    The recovery hint tells the model how to proceed without the tool result it
    was expecting; this keeps the agent loop from collapsing on a transient error.
    """
    return (
        f"<search_error query={_attr(query)}>\n"
        f"<reason>{escape(reason)}</reason>\n"
        "<recovery>Try again with a different query, or answer without this tool"
        " if you can do so honestly.</recovery>\n"
        "</search_error>"
    )
