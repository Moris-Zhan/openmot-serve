import pytest

from llm_eval.thresholds import (
    ANSWER_THRESHOLD,
    REFUSAL_THRESHOLD,
    TOOL_FORMAT_THRESHOLD,
    VRAM_LIMIT_GIB,
    CandidateMetrics,
    passes,
    select_candidate,
)


def _metrics(
    name: str = "m",
    answer: float = 0.95,
    refusal: float = 0.95,
    tool_format: float = 0.99,
    latency: float = 2.0,
    vram: float = 10.0,
) -> CandidateMetrics:
    return CandidateMetrics(name, answer, refusal, tool_format, latency, vram)


def test_the_thresholds_are_locked_before_any_evaluation_result() -> None:
    assert ANSWER_THRESHOLD == 0.90
    assert REFUSAL_THRESHOLD == 0.90
    assert TOOL_FORMAT_THRESHOLD == 0.95
    assert VRAM_LIMIT_GIB == 16.0


def test_a_candidate_exactly_at_every_threshold_passes() -> None:
    assert passes(_metrics(answer=0.90, refusal=0.90, tool_format=0.95, vram=16.0))


@pytest.mark.parametrize(
    "override",
    [{"answer": 0.8999}, {"refusal": 0.8999}, {"tool_format": 0.9499}, {"vram": 16.01}],
)
def test_a_candidate_just_below_any_threshold_fails(override: dict[str, float]) -> None:
    assert not passes(_metrics(**override))


def test_the_passing_candidate_with_the_lowest_median_latency_is_chosen() -> None:
    selection = select_candidate([_metrics("a", latency=3.0), _metrics("b", latency=1.5), _metrics("c", latency=2.0)])
    assert selection.chosen is not None and selection.chosen.name == "b"
    assert [item.name for item in selection.passed] == ["a", "b", "c"]


def test_failing_candidates_are_never_chosen_even_when_faster() -> None:
    selection = select_candidate([_metrics("fast", answer=0.5, latency=0.1), _metrics("ok", latency=4.0)])
    assert selection.chosen is not None and selection.chosen.name == "ok"
    assert [item.name for item in selection.passed] == ["ok"]


def test_latencies_within_ten_percent_pick_the_smaller_model() -> None:
    selection = select_candidate([_metrics("big", latency=2.0, vram=14.0), _metrics("small", latency=2.15, vram=6.0)])
    assert selection.chosen is not None and selection.chosen.name == "small"


def test_latencies_more_than_ten_percent_apart_pick_the_faster_model() -> None:
    selection = select_candidate([_metrics("big", latency=2.0, vram=14.0), _metrics("small", latency=2.3, vram=6.0)])
    assert selection.chosen is not None and selection.chosen.name == "big"


def test_no_passing_candidate_means_nothing_is_chosen_and_the_bar_is_unchanged() -> None:
    selection = select_candidate([_metrics("a", answer=0.7), _metrics("b", refusal=0.2)])
    assert selection.chosen is None and selection.passed == ()
    assert ANSWER_THRESHOLD == 0.90


def test_an_empty_candidate_list_chooses_nothing() -> None:
    assert select_candidate([]).chosen is None
