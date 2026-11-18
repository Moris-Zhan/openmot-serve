import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eval.run import DEFAULT_SEQUENCES
from eval.split import split_sequence
from inference.calibration import (
    CALIBRATION_SEQUENCES,
    DEFAULT_CALIBRATION_FRAMES,
    CalibrationError,
    select_calibration_frames,
)

REPOSITORY = Path(__file__).resolve().parent.parent


def make_sequences(data_root: Path, lengths: dict[str, int]) -> None:
    for name, length in lengths.items():
        frames_dir = data_root / "train" / name / "img1"
        frames_dir.mkdir(parents=True)
        for number in range(1, length + 1):
            (frames_dir / f"{number:06d}.jpg").write_bytes(b"")


def location(data_root: Path, path: Path) -> tuple[str, int]:
    relative = path.relative_to(data_root / "train")
    return relative.parts[0], int(path.stem)


def test_the_default_sequences_and_count_match_the_benchmark() -> None:
    assert CALIBRATION_SEQUENCES == DEFAULT_SEQUENCES
    assert DEFAULT_CALIBRATION_FRAMES == 512


def test_every_selected_frame_is_inside_its_sequence_tuning_half(tmp_path: Path) -> None:
    lengths = {"A": 41, "B": 30, "C": 25}
    make_sequences(tmp_path, lengths)
    selection = select_calibration_frames(tmp_path, count=20, sequences=tuple(lengths))
    assert len(selection.frames) == 20
    for path in selection.frames:
        name, number = location(tmp_path, path)
        tuning, _ = split_sequence(lengths[name])
        assert tuning.first <= number <= tuning.last


def test_validation_half_frames_are_never_selected_even_when_asked_for_everything(tmp_path: Path) -> None:
    make_sequences(tmp_path, {"A": 20, "B": 20})
    selection = select_calibration_frames(tmp_path, count=20, sequences=("A", "B"))
    assert {location(tmp_path, path) for path in selection.frames} == {
        (name, number) for name in ("A", "B") for number in range(1, 11)
    }


def test_frames_are_spread_evenly_across_the_ordered_tuning_pool(tmp_path: Path) -> None:
    make_sequences(tmp_path, {"A": 20, "B": 20})
    selection = select_calibration_frames(tmp_path, count=5, sequences=("B", "A"))
    assert [location(tmp_path, path) for path in selection.frames] == [
        ("A", 1),
        ("A", 5),
        ("A", 9),
        ("B", 3),
        ("B", 7),
    ]


def test_selection_is_identical_across_calls_and_data_locations(tmp_path: Path) -> None:
    lengths = {"A": 41, "B": 30}
    make_sequences(tmp_path / "one", lengths)
    shutil.copytree(tmp_path / "one", tmp_path / "two")
    first = select_calibration_frames(tmp_path / "one", count=12, sequences=("A", "B"))
    again = select_calibration_frames(tmp_path / "one", count=12, sequences=("A", "B"))
    elsewhere = select_calibration_frames(tmp_path / "two", count=12, sequences=("A", "B"))
    assert first == again
    assert re.fullmatch(r"[0-9a-f]{64}", first.sha256)
    assert elsewhere.sha256 == first.sha256


def test_the_hash_changes_with_the_count(tmp_path: Path) -> None:
    make_sequences(tmp_path, {"A": 40})
    small = select_calibration_frames(tmp_path, count=5, sequences=("A",))
    large = select_calibration_frames(tmp_path, count=10, sequences=("A",))
    assert small.sha256 != large.sha256


def test_fewer_available_frames_than_requested_names_both_numbers(tmp_path: Path) -> None:
    make_sequences(tmp_path, {"A": 20})
    with pytest.raises(CalibrationError, match=r"10.*11|11.*10"):
        select_calibration_frames(tmp_path, count=11, sequences=("A",))


def test_a_missing_sequence_is_named(tmp_path: Path) -> None:
    make_sequences(tmp_path, {"A": 20})
    with pytest.raises(CalibrationError, match="B"):
        select_calibration_frames(tmp_path, count=2, sequences=("A", "B"))


def test_a_missing_data_root_is_reported(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="MOT17-02-FRCNN"):
        select_calibration_frames(tmp_path / "absent", count=4)


@pytest.mark.parametrize("count", [0, -3])
def test_a_non_positive_count_is_rejected(tmp_path: Path, count: int) -> None:
    make_sequences(tmp_path, {"A": 20})
    with pytest.raises(CalibrationError, match="count"):
        select_calibration_frames(tmp_path, count=count, sequences=("A",))


def test_the_selection_can_be_imported_without_torch_or_tensorrt() -> None:
    script = (
        "import sys; sys.modules['torch'] = None; sys.modules['tensorrt'] = None; "
        "from inference.calibration import select_calibration_frames"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPOSITORY, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
