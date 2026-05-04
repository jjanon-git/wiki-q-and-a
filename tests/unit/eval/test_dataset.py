"""Tests for the EvalCase YAML loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from wiki_qa.eval.dataset import load_cases
from wiki_qa.eval.schema import EvalCase, ExpectedBehavior

MINIMAL_CASE_YAML = """\
- id: factual_001
  category: simple_factual
  difficulty: easy
  question: "When was the Battle of Hastings?"
  expected_answer: "1066"
  expected_behavior:
    must_search: true
  notes: "Baseline factual lookup."
"""

MULTI_CASE_YAML = """\
- id: factual_001
  category: simple_factual
  difficulty: easy
  question: "When was the Battle of Hastings?"
  expected_answer: "1066"
  expected_behavior:
    must_search: true

- id: negcap_001
  category: negative_capability
  difficulty: easy
  question: "What is 17 * 23?"
  expected_answer: "Arithmetic; should not search Wikipedia."
  expected_behavior:
    must_not_search: true
"""


def write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_load_single_case_from_yaml(tmp_path: Path) -> None:
    write_yaml(tmp_path, "factual.yaml", MINIMAL_CASE_YAML)

    cases = load_cases(tmp_path / "*.yaml")

    assert len(cases) == 1
    case = cases[0]
    assert isinstance(case, EvalCase)
    assert case.id == "factual_001"
    assert case.category == "simple_factual"
    assert case.difficulty == "easy"
    assert case.question == "When was the Battle of Hastings?"
    assert case.expected_answer == "1066"
    assert case.notes == "Baseline factual lookup."


def test_load_case_defaults_all_expected_behavior_flags_to_false(tmp_path: Path) -> None:
    yaml_no_behavior = """\
- id: factual_002
  category: simple_factual
  difficulty: easy
  question: "Q?"
  expected_answer: "A"
"""
    write_yaml(tmp_path, "f.yaml", yaml_no_behavior)

    cases = load_cases(tmp_path / "*.yaml")

    assert cases[0].expected_behavior == ExpectedBehavior()
    assert cases[0].expected_behavior.must_search is False
    assert cases[0].expected_behavior.must_not_search is False
    assert cases[0].expected_behavior.must_surface_premise_discrepancy is False
    assert cases[0].expected_behavior.must_refuse is False


def test_load_case_partial_expected_behavior(tmp_path: Path) -> None:
    yaml_partial = """\
- id: fp_001
  category: false_premise
  difficulty: medium
  question: "When did Einstein win the Nobel for relativity?"
  expected_answer: "Photoelectric effect, 1921, not relativity."
  expected_behavior:
    must_search: true
    must_surface_premise_discrepancy: true
"""
    write_yaml(tmp_path, "fp.yaml", yaml_partial)

    cases = load_cases(tmp_path / "*.yaml")

    eb = cases[0].expected_behavior
    assert eb.must_search is True
    assert eb.must_surface_premise_discrepancy is True
    assert eb.must_not_search is False
    assert eb.must_refuse is False


def test_load_multiple_cases_from_one_file(tmp_path: Path) -> None:
    write_yaml(tmp_path, "many.yaml", MULTI_CASE_YAML)

    cases = load_cases(tmp_path / "*.yaml")

    assert len(cases) == 2
    assert {c.id for c in cases} == {"factual_001", "negcap_001"}


def test_load_glob_across_multiple_files(tmp_path: Path) -> None:
    write_yaml(tmp_path, "a.yaml", MINIMAL_CASE_YAML)
    write_yaml(
        tmp_path,
        "b.yaml",
        """\
- id: negcap_001
  category: negative_capability
  difficulty: easy
  question: "What is 17 * 23?"
  expected_answer: "Arithmetic."
  expected_behavior:
    must_not_search: true
""",
    )

    cases = load_cases(tmp_path / "*.yaml")

    assert {c.id for c in cases} == {"factual_001", "negcap_001"}


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    write_yaml(tmp_path, "a.yaml", MINIMAL_CASE_YAML)
    write_yaml(tmp_path, "b.yaml", MINIMAL_CASE_YAML)

    with pytest.raises(ValueError, match="duplicate"):
        load_cases(tmp_path / "*.yaml")


def test_load_rejects_unknown_category(tmp_path: Path) -> None:
    yaml_bad_category = """\
- id: bad_001
  category: not_a_real_category
  difficulty: easy
  question: "Q?"
  expected_answer: "A"
"""
    write_yaml(tmp_path, "bad.yaml", yaml_bad_category)

    with pytest.raises(ValueError, match="category"):
        load_cases(tmp_path / "*.yaml")


def test_load_rejects_unknown_difficulty(tmp_path: Path) -> None:
    yaml_bad_diff = """\
- id: bad_002
  category: simple_factual
  difficulty: extreme
  question: "Q?"
  expected_answer: "A"
"""
    write_yaml(tmp_path, "bad.yaml", yaml_bad_diff)

    with pytest.raises(ValueError, match="difficulty"):
        load_cases(tmp_path / "*.yaml")


def test_load_rejects_missing_required_field(tmp_path: Path) -> None:
    yaml_missing_question = """\
- id: bad_003
  category: simple_factual
  difficulty: easy
  expected_answer: "A"
"""
    write_yaml(tmp_path, "bad.yaml", yaml_missing_question)

    with pytest.raises(ValueError, match="question"):
        load_cases(tmp_path / "*.yaml")


def test_load_rejects_unknown_expected_behavior_flag(tmp_path: Path) -> None:
    yaml_bad_flag = """\
- id: bad_004
  category: simple_factual
  difficulty: easy
  question: "Q?"
  expected_answer: "A"
  expected_behavior:
    must_juggle: true
"""
    write_yaml(tmp_path, "bad.yaml", yaml_bad_flag)

    with pytest.raises(ValueError, match=r"must_juggle|expected_behavior"):
        load_cases(tmp_path / "*.yaml")


def test_load_empty_glob_returns_empty_list(tmp_path: Path) -> None:
    cases = load_cases(tmp_path / "*.yaml")
    assert cases == []


def test_load_sorts_cases_by_id_for_determinism(tmp_path: Path) -> None:
    yaml_unsorted = """\
- id: zeta_001
  category: simple_factual
  difficulty: easy
  question: "Z?"
  expected_answer: "Z"
- id: alpha_001
  category: simple_factual
  difficulty: easy
  question: "A?"
  expected_answer: "A"
"""
    write_yaml(tmp_path, "u.yaml", yaml_unsorted)

    cases = load_cases(tmp_path / "*.yaml")

    assert [c.id for c in cases] == ["alpha_001", "zeta_001"]
