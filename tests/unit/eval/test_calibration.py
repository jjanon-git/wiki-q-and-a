"""Tests for the calibration workflow.

Focus: the sampling stratification (deterministic given a seed; covers
score buckets across dimensions); the analyzer's agreement counting; and
the YAML round-trip.
"""

from __future__ import annotations

import random
from typing import Any

import yaml

from wiki_qa.agent_contract import AgentResult, TokenUsage
from wiki_qa.eval.behavior_checks import BehaviorChecks
from wiki_qa.eval.calibration import (
    DISAGREEMENT_THRESHOLD,
    RUBRIC_DIMS,
    analyze_scores,
    render_scores_yaml,
    sample_for_calibration,
)
from wiki_qa.eval.judge import DimensionScore, JudgeOutput
from wiki_qa.eval.results import EvalResult


def _agent_result(question: str = "Q?") -> AgentResult:
    return AgentResult(
        question=question,
        evidence="ev",
        answer="ans",
        raw_output="<evidence>ev</evidence><answer>ans</answer>",
        tool_calls=[],
        n_searches=0,
        queries=[],
        stop_reason="end_turn",
        usage=TokenUsage(
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        ),
        raw_messages=[],
        parse_warnings=[],
    )


def _judge_output(scores: dict[str, int]) -> JudgeOutput:
    return JudgeOutput(
        dimensions=tuple(
            DimensionScore(name=dim, score=scores.get(dim), reasoning="r") for dim in RUBRIC_DIMS
        ),
        raw_response="",
        judge_failure=False,
        retries=0,
    )


def _result(case_id: str, scores: dict[str, int], category: str = "simple_factual") -> EvalResult:
    return EvalResult(
        case_id=case_id,
        category=category,
        difficulty="easy",
        question=f"Q for {case_id}?",
        status="ok",
        agent_result=_agent_result(),
        behavior_checks=BehaviorChecks(case_id=case_id, checks=[]),
        judge_output=_judge_output(scores),
        error=None,
        duration_ms=100,
    )


class TestSampleForCalibration:
    def test_returns_n_cases_when_pool_large_enough(self) -> None:
        results = [
            _result(f"case_{i:02d}", scores={dim: 3 for dim in RUBRIC_DIMS}) for i in range(20)
        ]
        sampled = sample_for_calibration(results, n=8, rng=random.Random(0))
        assert len(sampled) == 8

    def test_skips_judge_failures(self) -> None:
        good = _result("good", scores={dim: 3 for dim in RUBRIC_DIMS})
        bad_jo = JudgeOutput(
            dimensions=(),
            raw_response="malformed",
            judge_failure=True,
            retries=2,
        )
        bad = good.model_copy(update={"case_id": "bad", "judge_output": bad_jo})
        sampled = sample_for_calibration([good, bad], n=2, rng=random.Random(0))
        assert all(r.case_id == "good" for r in sampled)

    def test_prefers_low_scoring_cases_for_low_bucket(self) -> None:
        # Half the cases score 0 on factual_accuracy; half score 3 on it.
        # Sampler should pick at least one low + one high for that dim.
        low_cases = [
            _result(
                f"low_{i}",
                scores={
                    "factual_accuracy": 0,
                    "groundedness": 3,
                    "citation_quality": 3,
                    "search_efficiency": 3,
                    "calibration": 3,
                },
            )
            for i in range(5)
        ]
        high_cases = [
            _result(
                f"high_{i}",
                scores={dim: 3 for dim in RUBRIC_DIMS},
            )
            for i in range(5)
        ]
        sampled = sample_for_calibration(low_cases + high_cases, n=10, rng=random.Random(0))
        ids = {r.case_id for r in sampled}
        assert any(cid.startswith("low_") for cid in ids), (
            "should include at least one low-scoring case"
        )
        assert any(cid.startswith("high_") for cid in ids), (
            "should include at least one high-scoring case"
        )

    def test_deterministic_under_same_seed(self) -> None:
        results = [
            _result(f"c_{i:02d}", scores={dim: (i % 4) for dim in RUBRIC_DIMS}) for i in range(20)
        ]
        a = sample_for_calibration(results, n=6, rng=random.Random(123))
        b = sample_for_calibration(results, n=6, rng=random.Random(123))
        assert [r.case_id for r in a] == [r.case_id for r in b]

    def test_handles_undersized_pool_gracefully(self) -> None:
        results = [_result("only_one", scores={dim: 3 for dim in RUBRIC_DIMS})]
        sampled = sample_for_calibration(results, n=8, rng=random.Random(0))
        assert len(sampled) == 1

    def test_returns_empty_when_no_valid_results(self) -> None:
        sampled = sample_for_calibration([], n=8, rng=random.Random(0))
        assert sampled == []


class TestAnalyzeScores:
    def _make_yaml(self, samples: list[dict[str, Any]]) -> str:
        return yaml.safe_dump({"samples": samples}, sort_keys=False)

    def test_perfect_agreement(self) -> None:
        scores = {dim: 3 for dim in RUBRIC_DIMS}
        sample = {
            "case_id": "c1",
            "category": "simple_factual",
            "judge": scores,
            "human": scores,
            "notes": "",
        }
        report = analyze_scores(self._make_yaml([sample]))
        for dim in RUBRIC_DIMS:
            agg = report.per_dim[dim]
            assert agg.agree == 1
            assert agg.disagree == 0
            assert agg.agree_rate == 1.0

    def test_within_one_counts_as_agreement(self) -> None:
        sample = {
            "case_id": "c1",
            "category": "simple_factual",
            "judge": {dim: 3 for dim in RUBRIC_DIMS},
            "human": {dim: 2 for dim in RUBRIC_DIMS},  # Δ = 1
            "notes": "",
        }
        report = analyze_scores(self._make_yaml([sample]))
        for dim in RUBRIC_DIMS:
            assert report.per_dim[dim].agree == 1

    def test_delta_greater_than_one_is_disagreement(self) -> None:
        sample = {
            "case_id": "c1",
            "category": "simple_factual",
            "judge": {
                "factual_accuracy": 3,
                "groundedness": 3,
                "citation_quality": 3,
                "search_efficiency": 3,
                "calibration": 3,
            },
            "human": {
                "factual_accuracy": 1,
                "groundedness": 3,
                "citation_quality": 3,
                "search_efficiency": 3,
                "calibration": 3,
            },
            "notes": "judge over-credited",
        }
        report = analyze_scores(self._make_yaml([sample]))
        # factual_accuracy: 1 vs 3 → Δ = 2 → disagreement
        fa = report.per_dim["factual_accuracy"]
        assert fa.disagree == 1
        assert fa.agree == 0
        assert fa.disagreements[0]["case_id"] == "c1"
        assert fa.disagreements[0]["delta"] == -2
        assert fa.disagreements[0]["note"] == "judge over-credited"
        # other dims agree
        for dim in ("groundedness", "citation_quality", "search_efficiency", "calibration"):
            assert report.per_dim[dim].agree == 1

    def test_null_human_score_is_skipped_not_disagreement(self) -> None:
        sample = {
            "case_id": "c1",
            "category": "simple_factual",
            "judge": {dim: 3 for dim in RUBRIC_DIMS},
            "human": {dim: None for dim in RUBRIC_DIMS},
            "notes": "",
        }
        report = analyze_scores(self._make_yaml([sample]))
        for dim in RUBRIC_DIMS:
            agg = report.per_dim[dim]
            assert agg.skipped == 1
            assert agg.applicable == 0
            assert agg.agree_rate is None

    def test_disagreement_threshold_constant_is_25_percent(self) -> None:
        # The design rule (DECISIONS 2026-05-03 15:34) puts the bar at 25%.
        assert DISAGREEMENT_THRESHOLD == 0.25

    def test_n_samples_counted(self) -> None:
        samples = [
            {
                "case_id": f"c{i}",
                "category": "simple_factual",
                "judge": {dim: 3 for dim in RUBRIC_DIMS},
                "human": {dim: 3 for dim in RUBRIC_DIMS},
                "notes": "",
            }
            for i in range(7)
        ]
        report = analyze_scores(self._make_yaml(samples))
        assert report.n_samples == 7


class TestRenderScoresYaml:
    def test_yaml_round_trips(self) -> None:
        results = [
            _result("case_a", scores={dim: 3 for dim in RUBRIC_DIMS}),
            _result("case_b", scores={dim: 1 for dim in RUBRIC_DIMS}),
        ]
        text = render_scores_yaml(results)
        data = yaml.safe_load(text)
        assert "samples" in data
        assert len(data["samples"]) == 2
        assert data["samples"][0]["case_id"] == "case_a"
        # All judge scores populated
        assert data["samples"][0]["judge"]["factual_accuracy"] == 3
        # All human scores null (to be filled)
        assert all(v is None for v in data["samples"][0]["human"].values())
        # Notes field present
        assert "notes" in data["samples"][0]

    def test_yaml_includes_helpful_header_comment(self) -> None:
        results = [_result("c1", scores={dim: 3 for dim in RUBRIC_DIMS})]
        text = render_scores_yaml(results)
        assert text.startswith("#")  # has a leading comment block
        assert "0-3" in text
        assert "analyze" in text
