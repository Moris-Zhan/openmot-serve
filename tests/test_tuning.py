import json
from pathlib import Path

import numpy as np
import pytest

from eval.split import FrameRange, TuningHalf, ValidationHalf
from eval.tuning import (
    GRID,
    TuningError,
    TuningSequence,
    read_params,
    tune_parameters,
    write_params,
)
from tracking.bytetrack import TrackerParams

FRAME_COUNT = 24
WIDTH, HEIGHT = 40.0, 100.0
CONFIDENT_SCORE = 0.9
HESITANT_SCORE = 0.65


def _left(frame: int, offset: float) -> float:
    return 100.0 + 3.0 * frame + offset


def _write_sequence(root: Path, name: str, offsets: tuple[float, ...]) -> Path:
    sequence = root / name
    (sequence / "gt").mkdir(parents=True)
    (sequence / "seqinfo.ini").write_text(
        f"[Sequence]\nname={name}\nimDir=img1\nframeRate=30\nseqLength={FRAME_COUNT}\n"
        "imWidth=1920\nimHeight=1080\nimExt=.jpg\n"
    )
    lines = [
        f"{frame},{identity},{_left(frame, offset)},200.0,{WIDTH},{HEIGHT},1,1,1"
        for frame in range(1, FRAME_COUNT + 1)
        for identity, offset in enumerate(offsets, start=1)
    ]
    (sequence / "gt" / "gt.txt").write_text("\n".join(lines) + "\n")
    return sequence


def _detections(offsets_and_scores: tuple[tuple[float, float], ...]) -> tuple[np.ndarray, ...]:
    return tuple(
        np.array(
            [[_left(frame, offset), 200.0, _left(frame, offset) + WIDTH, 200.0 + HEIGHT, score]
             for offset, score in offsets_and_scores],
            dtype=np.float64,
        ).reshape(-1, 5)
        for frame in range(1, FRAME_COUNT + 1)
    )


def _sequence(root: Path, name: str, offsets_and_scores: tuple[tuple[float, float], ...]) -> TuningSequence:
    directory = _write_sequence(root, name, tuple(offset for offset, _ in offsets_and_scores))
    return TuningSequence(
        name=name,
        half=TuningHalf(1, FRAME_COUNT),
        sequence_dir=directory,
        detections=_detections(offsets_and_scores),
    )


def test_default_grid_has_24_distinct_combinations_around_the_defaults() -> None:
    assert len(GRID) == 24
    assert len(set(GRID)) == 24
    assert TrackerParams() in GRID


def test_the_combination_with_the_best_combined_hota_is_chosen(tmp_path: Path) -> None:
    sequence = _sequence(
        tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE), (400.0, HESITANT_SCORE))
    )
    result = tune_parameters([sequence], tmp_path / "work")
    assert result.best.detection_threshold == 0.5
    assert result.best_hota == max(trial.hota for trial in result.trials)
    worse = [trial.hota for trial in result.trials if trial.params.detection_threshold > 0.5]
    assert result.best_hota > max(worse)
    assert len(result.trials) == 24


def test_ties_go_to_the_first_combination_in_grid_order(tmp_path: Path) -> None:
    sequence = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    grid = (TrackerParams(0.6, 0.9, 30), TrackerParams(0.7, 0.8, 15), TrackerParams(0.5, 0.9, 60))
    result = tune_parameters([sequence], tmp_path / "work", grid)
    assert len({trial.hota for trial in result.trials}) == 1
    assert result.best == grid[0]


def test_trials_are_reported_in_grid_order(tmp_path: Path) -> None:
    sequence = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    grid = (TrackerParams(0.7, 0.8, 15), TrackerParams(0.5, 0.9, 60))
    result = tune_parameters([sequence], tmp_path / "work", grid)
    assert tuple(trial.params for trial in result.trials) == grid


def test_hota_is_combined_over_all_sequences(tmp_path: Path) -> None:
    perfect = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    hesitant = _sequence(tmp_path / "halves", "SYN-02", ((0.0, HESITANT_SCORE),))
    result = tune_parameters([perfect, hesitant], tmp_path / "work", (TrackerParams(0.5, 0.9, 30),))
    assert 99.0 < result.best_hota <= 100.0
    both_low = tune_parameters([perfect, hesitant], tmp_path / "work-2", (TrackerParams(0.7, 0.9, 30),))
    assert both_low.best_hota < result.best_hota


@pytest.mark.parametrize("wrong", [ValidationHalf(13, 24), FrameRange(1, 12)])
def test_only_a_tuning_half_is_accepted(tmp_path: Path, wrong: FrameRange) -> None:
    directory = _write_sequence(tmp_path / "halves", "SYN-01", (0.0,))
    with pytest.raises(TypeError, match="TuningHalf"):
        TuningSequence("SYN-01", wrong, directory, _detections(((0.0, CONFIDENT_SCORE),)))


def test_detections_must_cover_exactly_the_half(tmp_path: Path) -> None:
    directory = _write_sequence(tmp_path / "halves", "SYN-01", (0.0,))
    short = _detections(((0.0, CONFIDENT_SCORE),))[:-1]
    with pytest.raises(ValueError, match="detections"):
        TuningSequence("SYN-01", TuningHalf(1, FRAME_COUNT), directory, short)


def test_tuning_leaves_no_validation_directory_anywhere(tmp_path: Path) -> None:
    sequence = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    tune_parameters([sequence], tmp_path / "work", GRID[:3])
    names = {path.name.lower() for path in tmp_path.rglob("*")}
    assert not any("validation" in name for name in names)


def test_tuning_writes_only_inside_its_workspace(tmp_path: Path) -> None:
    sequence = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    before = {path for path in tmp_path.rglob("*")}
    tune_parameters([sequence], tmp_path / "work", GRID[:2])
    created = {path for path in tmp_path.rglob("*")} - before
    assert all(tmp_path / "work" in path.parents or path == tmp_path / "work" for path in created)


def test_a_non_empty_workspace_is_refused(tmp_path: Path) -> None:
    sequence = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "leftover").write_text("x")
    with pytest.raises(TuningError, match="workspace"):
        tune_parameters([sequence], workspace, GRID[:1])


def test_nothing_to_tune_is_refused(tmp_path: Path) -> None:
    sequence = _sequence(tmp_path / "halves", "SYN-01", ((0.0, CONFIDENT_SCORE),))
    with pytest.raises(TuningError, match="sequence"):
        tune_parameters([], tmp_path / "work-a")
    with pytest.raises(TuningError, match="grid"):
        tune_parameters([sequence], tmp_path / "work-b", ())


def test_params_round_trip_through_params_json(tmp_path: Path) -> None:
    path = tmp_path / "out" / "params.json"
    params = TrackerParams(0.7, 0.8, 45)
    write_params(path, params)
    assert json.loads(path.read_text()) == {
        "detection_threshold": 0.7,
        "match_threshold": 0.8,
        "track_buffer": 45,
    }
    assert read_params(path) == params
    assert [entry.name for entry in path.parent.iterdir()] == ["params.json"]


def test_a_malformed_params_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "params.json"
    path.write_text('{"detection_threshold": 0.7}')
    with pytest.raises(TuningError, match="params"):
        read_params(path)
    path.write_text('{"detection_threshold": 0.7, "match_threshold": 0.8, "track_buffer": 45, "extra": 1}')
    with pytest.raises(TuningError, match="params"):
        read_params(path)
    path.write_text("not json")
    with pytest.raises(TuningError, match="params"):
        read_params(path)
