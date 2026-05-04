"""Load EvalCase YAML files.

Cases are loaded from a glob pattern (e.g. `tests/eval/cases/*.yaml`).
Validation rules:
- required fields: id, category, difficulty, question, expected_answer
- category must be in ALLOWED_CATEGORIES
- difficulty must be in ALLOWED_DIFFICULTIES
- expected_behavior keys must be a subset of ExpectedBehavior fields
- ids must be unique across the loaded set
- output is sorted by id for deterministic iteration order
"""

from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Any

import yaml

from wiki_qa.eval.schema import (
    ALLOWED_CATEGORIES,
    ALLOWED_DIFFICULTIES,
    EvalCase,
    ExpectedBehavior,
)

_REQUIRED_FIELDS = ("id", "category", "difficulty", "question", "expected_answer")
_VALID_BEHAVIOR_KEYS = frozenset(ExpectedBehavior.model_fields.keys())


def load_cases(pattern: str | Path) -> list[EvalCase]:
    paths = sorted(_glob.glob(str(pattern)))
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()

    for path in paths:
        for raw in _read_yaml_list(Path(path)):
            case = _build_case(raw, source=path)
            if case.id in seen_ids:
                raise ValueError(f"duplicate case id {case.id!r} (second occurrence in {path})")
            seen_ids.add(case.id)
            cases.append(case)

    cases.sort(key=lambda c: c.id)
    return cases


def _read_yaml_list(path: Path) -> list[dict[str, Any]]:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected top-level YAML list, got {type(data).__name__}")
    return data


def _build_case(raw: dict[str, Any], *, source: str) -> EvalCase:
    for required in _REQUIRED_FIELDS:
        if required not in raw:
            raise ValueError(f"{source}: case missing required field {required!r}: {raw!r}")

    category = raw["category"]
    if category not in ALLOWED_CATEGORIES:
        raise ValueError(
            f"{source}: case {raw['id']!r} has unknown category {category!r}; "
            f"allowed: {sorted(ALLOWED_CATEGORIES)}"
        )

    difficulty = raw["difficulty"]
    if difficulty not in ALLOWED_DIFFICULTIES:
        raise ValueError(
            f"{source}: case {raw['id']!r} has unknown difficulty {difficulty!r}; "
            f"allowed: {sorted(ALLOWED_DIFFICULTIES)}"
        )

    eb = _build_expected_behavior(raw.get("expected_behavior") or {}, case_id=raw["id"])

    return EvalCase(
        id=str(raw["id"]),
        category=str(category),
        difficulty=str(difficulty),
        question=str(raw["question"]),
        expected_answer=str(raw["expected_answer"]),
        expected_behavior=eb,
        notes=str(raw.get("notes", "")),
    )


def _build_expected_behavior(raw: dict[str, Any], *, case_id: str) -> ExpectedBehavior:
    unknown = set(raw) - _VALID_BEHAVIOR_KEYS
    if unknown:
        raise ValueError(
            f"case {case_id!r}: unknown expected_behavior flag(s) {sorted(unknown)}; "
            f"valid: {sorted(_VALID_BEHAVIOR_KEYS)}"
        )
    return ExpectedBehavior(**{k: bool(v) for k, v in raw.items()})
