import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from llm_eval.questions import Question

_FRAME_UNITS = ("frame", "影格", "張", "幀")


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    valid: bool
    problems: list[str]


@dataclass(frozen=True)
class Trial:
    question_id: str
    run: int
    calls: tuple[ToolCall, ...]
    final: dict[str, Any] | None
    error: str | None
    latency_seconds: float
    warmup: bool = False
    raw: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class TrialScore:
    kind: str
    correct: bool
    tool_calls: int
    valid_tool_calls: int
    used_tool: bool
    inconsistent_with_tool: bool


@dataclass(frozen=True)
class Metrics:
    tool_format_correctness: float
    answer_correctness: float
    refusal_correctness: float
    latency_median_seconds: float
    trials: int
    unstable_questions: int
    inconsistent_trials: int


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _matches(tool: str | None, value: Any, expected: Any) -> bool:
    if tool == "track_span":
        return (
            isinstance(value, list) and len(value) == len(expected) and all(map(_number, value)) and value == expected
        )
    if tool == "ids_in_region":
        return isinstance(value, list) and all(map(_number, value)) and sorted(value) == sorted(expected)
    return _number(value) and value == expected


def _tool_value(call: ToolCall) -> Any:
    result = call.result or {}
    if call.name == "track_span":
        return [result["first_frame"], result["last_frame"]] if result.get("found") else None
    for key in ("count", "frames", "ids"):
        if key in result:
            return result[key]
    return None


def _is_answered(final: dict[str, Any] | None) -> bool:
    return final is not None and final.get("refused") is False


def _refusal_correct(question: Question, final: dict[str, Any] | None) -> bool:
    if final is None:
        return False
    if final.get("refused") is True:
        return final.get("category") == question.category
    alternative = question.frames_alternative
    unit = str(final.get("unit") or "").lower()
    return (
        alternative is not None
        and _number(final.get("value"))
        and final["value"] == alternative
        and any(word in unit for word in _FRAME_UNITS)
    )


def score_trial(question: Question, trial: Trial) -> TrialScore:
    final = trial.final
    valid_calls = [call for call in trial.calls if call.valid]
    if question.answerable:
        kind = "answer"
        correct = _is_answered(final) and _matches(question.tool, final.get("value"), question.gold)  # type: ignore[union-attr]
    else:
        kind = "refusal"
        correct = _refusal_correct(question, final)
    inconsistent = False
    if question.answerable and _is_answered(final) and valid_calls:
        last = valid_calls[-1]
        inconsistent = not _matches(last.name, final.get("value"), _tool_value(last))  # type: ignore[union-attr]
    return TrialScore(kind, correct, len(trial.calls), len(valid_calls), bool(valid_calls), inconsistent)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def aggregate(questions: Sequence[Question], trials: Sequence[Trial]) -> Metrics:
    by_id = {question.id: question for question in questions}
    scored = [(trial, score_trial(by_id[trial.question_id], trial)) for trial in trials if not trial.warmup]
    answers = [score for _, score in scored if score.kind == "answer"]
    refusals = [score for _, score in scored if score.kind == "refusal"]
    outcomes: dict[str, set[bool]] = {}
    for trial, score in scored:
        outcomes.setdefault(trial.question_id, set()).add(score.correct)
    latencies = [trial.latency_seconds for trial, _ in scored]
    return Metrics(
        tool_format_correctness=_ratio(
            sum(score.valid_tool_calls for _, score in scored), sum(score.tool_calls for _, score in scored)
        ),
        answer_correctness=_ratio(sum(score.correct for score in answers), len(answers)),
        refusal_correctness=_ratio(sum(score.correct for score in refusals), len(refusals)),
        latency_median_seconds=statistics.median(latencies) if latencies else 0.0,
        trials=len(scored),
        unstable_questions=sum(1 for values in outcomes.values() if len(values) > 1),
        inconsistent_trials=sum(score.inconsistent_with_tool for _, score in scored),
    )
