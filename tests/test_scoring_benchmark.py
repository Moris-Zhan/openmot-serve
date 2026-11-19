from pathlib import Path

import pytest

from eval.scoring import ScoringError, score_sequences
from tests.test_scoring import FRAME_COUNT, PEDESTRIANS, _box, _rows, _write_result, _write_sequence

NON_MOT_VEHICLE = 6
VEHICLE_OFFSET = 900.0


def _write_sequence_with_vehicle(root: Path, name: str) -> Path:
    sequence = _write_sequence(root, name)
    lines = []
    for frame in range(1, FRAME_COUNT + 1):
        left, top, width, height = _box(frame, VEHICLE_OFFSET)
        lines.append(f"{frame},3,{left},{top},{width},{height},1,{NON_MOT_VEHICLE},1")
    gt = sequence / "gt" / "gt.txt"
    gt.write_text(gt.read_text() + "\n".join(lines) + "\n")
    return sequence


def _mota_when_the_tracker_boxes_the_vehicle(case_dir: Path, **options: str) -> float:
    sequence = _write_sequence_with_vehicle(case_dir / "halves", "SYN-01")
    rows = _rows(range(1, FRAME_COUNT + 1), (*PEDESTRIANS, (3, VEHICLE_OFFSET)))
    result = _write_result(case_dir / "results", "SYN-01", rows)
    report = score_sequences({"SYN-01": sequence}, {"SYN-01": result}, case_dir / "work", **options)
    return report.combined.mota


def test_mot20_ignores_a_prediction_on_a_non_mot_vehicle(tmp_path: Path) -> None:
    assert _mota_when_the_tracker_boxes_the_vehicle(tmp_path, benchmark="MOT20") == pytest.approx(100.0, abs=0.01)


def test_mot17_counts_the_same_prediction_as_a_false_positive(tmp_path: Path) -> None:
    assert _mota_when_the_tracker_boxes_the_vehicle(tmp_path / "explicit", benchmark="MOT17") < 100.0


def test_the_default_benchmark_is_mot17(tmp_path: Path) -> None:
    assert _mota_when_the_tracker_boxes_the_vehicle(tmp_path) < 100.0


def test_an_unsupported_benchmark_is_rejected_by_name(tmp_path: Path) -> None:
    with pytest.raises(ScoringError, match="MOT16"):
        _mota_when_the_tracker_boxes_the_vehicle(tmp_path, benchmark="MOT16")
