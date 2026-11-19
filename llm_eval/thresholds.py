from collections.abc import Sequence
from dataclasses import dataclass

ANSWER_THRESHOLD = 0.90
REFUSAL_THRESHOLD = 0.90
TOOL_FORMAT_THRESHOLD = 0.95
VRAM_LIMIT_GIB = 16.0
LATENCY_TIE_RATIO = 0.10


@dataclass(frozen=True)
class CandidateMetrics:
    name: str
    answer_correctness: float
    refusal_correctness: float
    tool_format_correctness: float
    latency_median_seconds: float
    vram_gib: float


@dataclass(frozen=True)
class Selection:
    passed: tuple[CandidateMetrics, ...]
    chosen: CandidateMetrics | None


def passes(metrics: CandidateMetrics) -> bool:
    return (
        metrics.answer_correctness >= ANSWER_THRESHOLD
        and metrics.refusal_correctness >= REFUSAL_THRESHOLD
        and metrics.tool_format_correctness >= TOOL_FORMAT_THRESHOLD
        and metrics.vram_gib <= VRAM_LIMIT_GIB
    )


def select_candidate(candidates: Sequence[CandidateMetrics]) -> Selection:
    passed = tuple(candidate for candidate in candidates if passes(candidate))
    if not passed:
        return Selection((), None)
    fastest = min(passed, key=lambda candidate: candidate.latency_median_seconds)
    near_fastest = [
        candidate
        for candidate in passed
        if candidate.latency_median_seconds <= fastest.latency_median_seconds * (1 + LATENCY_TIE_RATIO)
    ]
    chosen = min(near_fastest, key=lambda candidate: (candidate.vram_gib, candidate.latency_median_seconds))
    return Selection(passed, chosen)
