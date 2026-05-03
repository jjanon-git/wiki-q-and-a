"""Tests for the Anthropic-API tool definition for search_wikipedia."""

from __future__ import annotations

from wiki_qa.tools import SEARCH_WIKIPEDIA_TOOL


class TestSearchWikipediaToolSchema:
    def test_required_top_level_fields(self) -> None:
        assert SEARCH_WIKIPEDIA_TOOL["name"] == "search_wikipedia"
        assert isinstance(SEARCH_WIKIPEDIA_TOOL["description"], str)
        assert isinstance(SEARCH_WIKIPEDIA_TOOL["input_schema"], dict)

    def test_input_schema_shape(self) -> None:
        schema = SEARCH_WIKIPEDIA_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"
        assert "query" in schema["required"]

    def test_description_steers_toward_default_search(self) -> None:
        # The prompt-engineering choice: search by default for any verifiable
        # question. If this language gets weakened the tool won't get used.
        description = SEARCH_WIKIPEDIA_TOOL["description"].lower()
        assert "by default" in description or "default" in description

    def test_description_lists_exceptions_to_searching(self) -> None:
        # Negative-capability cases: math, code, opinion. These should be named
        # explicitly so the model has a clear non-search rule.
        description = SEARCH_WIKIPEDIA_TOOL["description"].lower()
        assert "arithmetic" in description or "calculation" in description
        assert "code" in description
        assert "opinion" in description

    def test_description_covers_iterative_search(self) -> None:
        # The agent must know it can re-search with refined queries; without this
        # it tends to give up after one weak result. Direct response to an Opus-4.7
        # behavior pattern flagged during design.
        description = SEARCH_WIKIPEDIA_TOOL["description"].lower()
        assert "search again" in description or "refined query" in description

    def test_description_covers_multi_part_decomposition(self) -> None:
        # Synthesis questions need the model to fan out into sub-searches.
        description = SEARCH_WIKIPEDIA_TOOL["description"].lower()
        assert "multi-part" in description or "decompose" in description

    def test_description_documents_return_shape(self) -> None:
        # The model needs to know what it gets back so it can plan around it.
        description = SEARCH_WIKIPEDIA_TOOL["description"].lower()
        assert "title" in description
        assert "url" in description
        assert "extract" in description

    def test_description_addresses_unanswerable_case(self) -> None:
        # Critical for trust: "say so rather than guessing" must be explicit.
        description = SEARCH_WIKIPEDIA_TOOL["description"].lower()
        assert "guess" in description or "rather than" in description

    def test_query_param_description_discourages_full_questions(self) -> None:
        query_desc = SEARCH_WIKIPEDIA_TOOL["input_schema"]["properties"]["query"][
            "description"
        ].lower()
        # Should steer toward noun phrases, not conversational questions
        assert "noun phrase" in query_desc or "specific" in query_desc

    def test_query_param_description_supports_multi_part(self) -> None:
        query_desc = SEARCH_WIKIPEDIA_TOOL["input_schema"]["properties"]["query"][
            "description"
        ].lower()
        assert "multi-part" in query_desc or "facet" in query_desc or "separate" in query_desc
