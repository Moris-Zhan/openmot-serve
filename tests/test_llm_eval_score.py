from dataclasses import asdict

from llm_eval.questions import Question
from llm_eval.score import ToolCall, Trial, aggregate, score_trial


def _answerable(tool: str = "count_unique_ids", gold: object = 3, qid: str = "q01") -> Question:
    return Question(qid, "real", "text", True, tool, {}, gold)


def _unanswerable(category: str = "image_content", alternative: int | None = None, qid: str = "q02") -> Question:
    return Question(qid, "real", "text", False, category=category, frames_alternative=alternative)


def _call(name: str = "count_unique_ids", valid: bool = True, result: dict | None = None) -> ToolCall:
    return ToolCall(name, {}, result if valid else None, valid, [] if valid else ["bad"])


def _trial(
    final: dict | None,
    calls: tuple[ToolCall, ...] = (),
    qid: str = "q01",
    run: int = 0,
    latency: float = 1.0,
    error: str | None = None,
    warmup: bool = False,
) -> Trial:
    return Trial(qid, run, calls, final, error, latency, warmup)


def _answer(value: object, refused: bool = False, category: str | None = None, unit: str | None = None) -> dict:
    return {"refused": refused, "category": category, "value": value, "unit": unit, "definition": None}


def test_valid_and_invalid_tool_calls_are_counted() -> None:
    score = score_trial(_answerable(), _trial(_answer(3), (_call(valid=True, result={"count": 3}), _call(valid=False))))
    assert (score.tool_calls, score.valid_tool_calls) == (2, 1)


def test_a_numeric_answer_must_equal_the_gold_value() -> None:
    assert score_trial(_answerable(gold=3), _trial(_answer(3))).correct
    assert score_trial(_answerable(gold=3), _trial(_answer(3.0))).correct
    assert not score_trial(_answerable(gold=3), _trial(_answer(4))).correct
    assert not score_trial(_answerable(gold=1), _trial(_answer(True))).correct


def test_a_span_answer_is_order_sensitive_and_a_region_answer_is_a_set() -> None:
    assert score_trial(_answerable("track_span", [1, 5]), _trial(_answer([1, 5]))).correct
    assert not score_trial(_answerable("track_span", [1, 5]), _trial(_answer([5, 1]))).correct
    assert score_trial(_answerable("ids_in_region", [1, 2, 3]), _trial(_answer([3, 1, 2]))).correct
    assert not score_trial(_answerable("ids_in_region", [1, 2, 3]), _trial(_answer([1, 2]))).correct
    assert score_trial(_answerable("ids_in_region", []), _trial(_answer([]))).correct


def test_refusing_an_answerable_question_is_an_incorrect_answer() -> None:
    score = score_trial(_answerable(), _trial(_answer(None, refused=True, category="image_content")))
    assert not score.correct and score.kind == "answer"


def test_a_trial_that_failed_has_no_answer_and_is_incorrect() -> None:
    assert not score_trial(_answerable(), _trial(None, error="timeout")).correct
    assert not score_trial(_unanswerable(), _trial(None, error="timeout")).correct


def test_a_refusal_needs_the_right_category() -> None:
    question = _unanswerable("prediction")
    assert score_trial(question, _trial(_answer(None, True, "prediction"))).correct
    assert not score_trial(question, _trial(_answer(None, True, "image_content"))).correct
    assert not score_trial(question, _trial(_answer(None, True, None))).correct
    assert not score_trial(question, _trial(_answer(7, refused=False))).correct
    assert score_trial(question, _trial(_answer(None, True, "prediction"))).kind == "refusal"


def test_the_seconds_question_accepts_a_frame_count_with_a_unit() -> None:
    question = _unanswerable("missing_sequence_info", alternative=42)
    assert score_trial(question, _trial(_answer(None, True, "missing_sequence_info"))).correct
    assert score_trial(question, _trial(_answer(42, unit="frames"))).correct
    assert score_trial(question, _trial(_answer(42, unit="影格"))).correct
    assert not score_trial(question, _trial(_answer(42, unit="seconds"))).correct
    assert not score_trial(question, _trial(_answer(42))).correct
    assert not score_trial(question, _trial(_answer(41, unit="frames"))).correct


def test_a_value_that_differs_from_the_tool_output_is_flagged() -> None:
    calls = (_call(result={"count": 3}),)
    assert score_trial(_answerable(gold=3), _trial(_answer(4), calls)).inconsistent_with_tool
    assert not score_trial(_answerable(gold=3), _trial(_answer(3), calls)).inconsistent_with_tool


def test_an_answer_without_any_tool_call_is_recorded_as_not_using_a_tool() -> None:
    assert not score_trial(_answerable(gold=3), _trial(_answer(3))).used_tool
    assert score_trial(_answerable(gold=3), _trial(_answer(3), (_call(result={"count": 3}),))).used_tool


def test_the_four_metrics_are_computed_over_all_trials() -> None:
    questions = [_answerable(gold=3, qid="q01"), _unanswerable("prediction", qid="q02")]
    good = (_call(result={"count": 3}),)
    trials = [
        _trial(_answer(3), good, "q01", 0, 1.0),
        _trial(_answer(3), good, "q01", 1, 2.0),
        _trial(_answer(9), (_call(valid=False),), "q01", 2, 3.0),
        _trial(_answer(None, True, "prediction"), (), "q02", 0, 4.0),
        _trial(_answer(None, True, "prediction"), (), "q02", 1, 5.0),
        _trial(_answer(5), (), "q02", 2, 6.0),
    ]
    metrics = aggregate(questions, trials)
    assert metrics.tool_format_correctness == 2 / 3
    assert metrics.answer_correctness == 2 / 3
    assert metrics.refusal_correctness == 2 / 3
    assert metrics.latency_median_seconds == 3.5
    assert metrics.unstable_questions == 2
    assert metrics.trials == 6


def test_a_warmup_request_is_not_counted_in_the_latency_median() -> None:
    questions = [_answerable(gold=3)]
    trials = [
        _trial(_answer(3), (), latency=30.0, warmup=True),
        _trial(_answer(3), (), run=0, latency=1.0),
        _trial(_answer(3), (), run=1, latency=3.0),
    ]
    metrics = aggregate(questions, trials)
    assert metrics.latency_median_seconds == 2.0 and metrics.trials == 2


def test_a_model_that_never_calls_a_tool_has_zero_format_correctness() -> None:
    metrics = aggregate([_answerable(gold=3)], [_trial(_answer(3))])
    assert metrics.tool_format_correctness == 0.0


def test_stable_questions_are_not_counted_as_unstable() -> None:
    questions = [_answerable(gold=3)]
    trials = [_trial(_answer(3), (), run=run) for run in range(3)]
    assert aggregate(questions, trials).unstable_questions == 0


def test_there_is_no_single_combined_score() -> None:
    metrics = aggregate([_answerable(gold=3)], [_trial(_answer(3), (_call(result={"count": 3}),))])
    keys = set(asdict(metrics))
    assert {"tool_format_correctness", "answer_correctness", "refusal_correctness", "latency_median_seconds"} <= keys
    assert not [key for key in keys if any(word in key for word in ("overall", "combined", "total_score", "weighted"))]
