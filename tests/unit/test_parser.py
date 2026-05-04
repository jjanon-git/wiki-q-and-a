"""Tests for parsing the agent's final assistant text into evidence/answer.

The model's output structure (per system_v1.md) is:
  <evidence>...</evidence>
  <answer>...</answer>

Substring-based extraction (not xml.etree) so unescaped angle brackets inside
extracts or quotes don't break parsing. Behavior on malformed input: the
matching field is empty; raw_output always carries the full text.
"""

from __future__ import annotations

from wiki_qa.agent_contract import ParseWarning
from wiki_qa.parser import parse_evidence_and_answer


class TestHappyPath:
    def test_extracts_both_blocks(self) -> None:
        text = (
            "<evidence>\n"
            '[Source: Albert Einstein] "physicist..."\n'
            "</evidence>\n"
            "<answer>\n"
            "Einstein won the 1921 Nobel Prize.\n"
            "Sources:\nAlbert Einstein - https://en.wikipedia.org/wiki/Albert_Einstein\n"
            "</answer>"
        )
        result = parse_evidence_and_answer(text)
        assert "Albert Einstein" in result.evidence
        assert "physicist" in result.evidence
        assert "1921 Nobel Prize" in result.answer
        assert "Sources:" in result.answer
        assert result.raw_output == text

    def test_strips_surrounding_whitespace_in_blocks(self) -> None:
        text = (
            "<evidence>\n   evidence content   \n</evidence>\n"
            "<answer>\n   answer content   \n</answer>"
        )
        result = parse_evidence_and_answer(text)
        assert result.evidence == "evidence content"
        assert result.answer == "answer content"

    def test_blocks_can_be_separated_by_arbitrary_whitespace(self) -> None:
        text = "<evidence>e</evidence>\n\n\n<answer>a</answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == "e"
        assert result.answer == "a"

    def test_handles_preamble_and_postamble(self) -> None:
        # The model might add reasoning before or text after the blocks
        text = (
            "Let me organize my findings.\n\n"
            "<evidence>quote</evidence>\n\n"
            "<answer>final</answer>\n\n"
            "Hope that helps."
        )
        result = parse_evidence_and_answer(text)
        assert result.evidence == "quote"
        assert result.answer == "final"
        assert result.raw_output == text


class TestTolerantToContent:
    def test_unescaped_angle_brackets_inside_evidence_dont_break_parsing(self) -> None:
        # Extracts may legitimately contain '<' or '>' (math, code, etc.);
        # strict XML parsing would fail. Substring extraction shouldn't.
        text = (
            "<evidence>\n"
            "[Source: Inequalities] x < 5 and y > 3\n"
            "</evidence>\n"
            "<answer>If x < 5, then...</answer>"
        )
        result = parse_evidence_and_answer(text)
        assert "x < 5" in result.evidence
        assert "y > 3" in result.evidence
        assert "x < 5" in result.answer

    def test_nested_lookalike_tags_in_content_dont_confuse_parser(self) -> None:
        # Model might paste a <search_results> block from tool output into evidence
        text = (
            "<evidence>\n"
            '[Source: Foo] <search_results query="foo">stuff</search_results>\n'
            "</evidence>\n"
            "<answer>The answer.</answer>"
        )
        result = parse_evidence_and_answer(text)
        assert "<search_results" in result.evidence
        assert result.answer == "The answer."

    def test_evidence_block_with_multiple_sources(self) -> None:
        text = (
            "<evidence>\n"
            '[Source: Article A] "fact one"\n'
            '[Source: Article B] "fact two"\n'
            '[Source: Article C] "fact three"\n'
            "</evidence>\n"
            "<answer>Combined.</answer>"
        )
        result = parse_evidence_and_answer(text)
        assert "Article A" in result.evidence
        assert "Article B" in result.evidence
        assert "Article C" in result.evidence


class TestMalformedInput:
    def test_missing_evidence_block_leaves_evidence_empty(self) -> None:
        text = "<answer>Just an answer, no evidence block.</answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert result.answer == "Just an answer, no evidence block."
        assert result.raw_output == text
        assert ParseWarning.MISSING_EVIDENCE_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_ANSWER_BLOCK not in result.parse_warnings

    def test_missing_answer_block_leaves_answer_empty(self) -> None:
        text = "<evidence>just evidence, no answer</evidence>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == "just evidence, no answer"
        assert result.answer == ""
        assert ParseWarning.MISSING_ANSWER_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_EVIDENCE_BLOCK not in result.parse_warnings

    def test_no_blocks_at_all_emits_both_missing_warnings(self) -> None:
        text = "I'm sorry, I can't help with that."
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert result.answer == ""
        assert result.raw_output == text
        assert ParseWarning.MISSING_EVIDENCE_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_ANSWER_BLOCK in result.parse_warnings

    def test_empty_input_emits_both_missing_warnings(self) -> None:
        result = parse_evidence_and_answer("")
        assert result.evidence == ""
        assert result.answer == ""
        assert result.raw_output == ""
        assert ParseWarning.MISSING_EVIDENCE_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_ANSWER_BLOCK in result.parse_warnings

    def test_unclosed_evidence_tag_emits_unclosed_warning(self) -> None:
        # If evidence opens but never closes: don't swallow the answer block,
        # AND distinguish from MISSING (model attempted the structure).
        text = "<evidence>oops no closing\n<answer>real answer</answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert result.answer == "real answer"
        assert ParseWarning.UNCLOSED_EVIDENCE_TAG in result.parse_warnings
        assert ParseWarning.MISSING_EVIDENCE_BLOCK not in result.parse_warnings

    def test_unclosed_answer_tag_emits_unclosed_warning(self) -> None:
        text = "<evidence>fine</evidence>\n<answer>oops no close"
        result = parse_evidence_and_answer(text)
        assert result.evidence == "fine"
        assert result.answer == ""
        assert ParseWarning.UNCLOSED_ANSWER_TAG in result.parse_warnings
        assert ParseWarning.MISSING_ANSWER_BLOCK not in result.parse_warnings

    def test_only_opening_tags_emits_both_unclosed_warnings(self) -> None:
        text = "<evidence><answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert result.answer == ""
        assert ParseWarning.UNCLOSED_EVIDENCE_TAG in result.parse_warnings
        assert ParseWarning.UNCLOSED_ANSWER_TAG in result.parse_warnings

    def test_empty_evidence_block_emits_empty_warning(self) -> None:
        # Structure clean, content null
        text = "<evidence></evidence>\n<answer>fine</answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert result.answer == "fine"
        assert ParseWarning.EMPTY_EVIDENCE_BLOCK in result.parse_warnings
        assert ParseWarning.MISSING_EVIDENCE_BLOCK not in result.parse_warnings
        assert ParseWarning.UNCLOSED_EVIDENCE_TAG not in result.parse_warnings

    def test_empty_answer_block_emits_empty_warning(self) -> None:
        text = "<evidence>fine</evidence>\n<answer></answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == "fine"
        assert result.answer == ""
        assert ParseWarning.EMPTY_ANSWER_BLOCK in result.parse_warnings

    def test_whitespace_only_block_treated_as_empty(self) -> None:
        text = "<evidence>   \n  </evidence>\n<answer>fine</answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert ParseWarning.EMPTY_EVIDENCE_BLOCK in result.parse_warnings

    def test_block_order_reversed_treated_as_malformed(self) -> None:
        # Reversed order suggests post-hoc rationalization (model wrote conclusion
        # first, back-filled evidence). Violates the system prompt's "draft evidence
        # then compose answer" instruction. Both fields empty; warning surfaces it.
        text = "<answer>answer first</answer>\n<evidence>then evidence</evidence>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == ""
        assert result.answer == ""
        assert result.raw_output == text
        assert ParseWarning.REVERSED_ORDER in result.parse_warnings

    def test_repeated_evidence_blocks_takes_first_with_warning(self) -> None:
        # If model emits two evidence blocks (shouldn't, but might), take the first
        # and surface the multiplicity as a warning so it's not silently dropped.
        text = (
            "<evidence>first evidence</evidence>\n"
            "<answer>middle</answer>\n"
            "<evidence>second evidence</evidence>"
        )
        result = parse_evidence_and_answer(text)
        assert result.evidence == "first evidence"
        assert ParseWarning.MULTIPLE_EVIDENCE_BLOCKS in result.parse_warnings

    def test_repeated_answer_blocks_takes_first_with_warning(self) -> None:
        text = (
            "<evidence>e</evidence>\n<answer>first answer</answer>\n<answer>second answer</answer>"
        )
        result = parse_evidence_and_answer(text)
        assert result.answer == "first answer"
        assert ParseWarning.MULTIPLE_ANSWER_BLOCKS in result.parse_warnings

    def test_clean_parse_has_no_warnings(self) -> None:
        text = "<evidence>e</evidence><answer>a</answer>"
        result = parse_evidence_and_answer(text)
        assert result.parse_warnings == []


class TestRawOutputAlwaysPopulated:
    def test_raw_output_preserved_on_success(self) -> None:
        text = "<evidence>e</evidence><answer>a</answer>"
        assert parse_evidence_and_answer(text).raw_output == text

    def test_raw_output_preserved_on_failure(self) -> None:
        text = "garbage that doesn't parse"
        assert parse_evidence_and_answer(text).raw_output == text

    def test_raw_output_preserved_with_unicode(self) -> None:
        text = "<evidence>café — résumé</evidence><answer>naïve</answer>"
        result = parse_evidence_and_answer(text)
        assert result.evidence == "café — résumé"
        assert result.answer == "naïve"
        assert result.raw_output == text
