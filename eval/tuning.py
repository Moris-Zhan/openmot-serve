import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from itertools import product
from pathlib import Path

import numpy as np

from eval.scoring import score_sequences
from eval.split import TuningHalf
from tracking.bytetrack import TrackerParams, run_tracker
from tracking.files import write_text_atomic
from tracking.motchallenge import format_result

GRID: tuple[TrackerParams, ...] = tuple(
    TrackerParams(detection_threshold, match_threshold, track_buffer)
    for detection_threshold, match_threshold, track_buffer in product(
        (0.5, 0.6, 0.7), (0.8, 0.9), (15, 30, 45, 60)
    )
)


class TuningError(RuntimeError):
    pass


@dataclass(frozen=True)
class TuningSequence:
    name: str
    half: TuningHalf
    sequence_dir: Path
    detections: tuple[np.ndarray, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.half, TuningHalf):
            raise TypeError(f"tuning only accepts a TuningHalf, got {type(self.half).__name__}")
        if len(self.detections) != self.half.count:
            raise ValueError(
                f"{self.name}: {len(self.detections)} frames of detections for a half of {self.half.count} frames"
            )


@dataclass(frozen=True)
class Trial:
    params: TrackerParams
    hota: float


@dataclass(frozen=True)
class TuningResult:
    best: TrackerParams
    best_hota: float
    trials: tuple[Trial, ...]


def _score_trial(sequences: Sequence[TuningSequence], params: TrackerParams, trial_dir: Path) -> float:
    results_dir = trial_dir / "results"
    results_dir.mkdir(parents=True)
    result_files = {}
    for sequence in sequences:
        result_files[sequence.name] = results_dir / f"{sequence.name}.txt"
        write_text_atomic(
            result_files[sequence.name], format_result(run_tracker(sequence.detections, params))
        )
    report = score_sequences(
        {sequence.name: sequence.sequence_dir for sequence in sequences},
        result_files,
        trial_dir / "score",
    )
    return report.combined.hota


def tune_parameters(
    sequences: Sequence[TuningSequence], workspace: Path, grid: Sequence[TrackerParams] = GRID
) -> TuningResult:
    if not sequences:
        raise TuningError("no sequence given to tune on")
    if not grid:
        raise TuningError("the parameter grid is empty")
    if workspace.exists() and any(workspace.iterdir()):
        raise TuningError(f"tuning workspace is not empty: {workspace}")
    trials = tuple(
        Trial(params, _score_trial(sequences, params, workspace / f"trial-{index:02d}"))
        for index, params in enumerate(grid)
    )
    best = trials[0]
    for trial in trials[1:]:
        if trial.hota > best.hota:
            best = trial
    return TuningResult(best=best.params, best_hota=best.hota, trials=trials)


def write_params(path: Path, params: TrackerParams) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(asdict(params), indent=2, sort_keys=True) + "\n")


def read_params(path: Path) -> TrackerParams:
    expected = {field.name for field in fields(TrackerParams)}
    try:
        content = json.loads(path.read_text())
        if not isinstance(content, dict) or set(content) != expected:
            raise ValueError(f"expected exactly the keys {sorted(expected)}")
        return TrackerParams(**content)
    except (OSError, ValueError, TypeError) as error:
        raise TuningError(f"cannot read params from {path}: {error}") from error
