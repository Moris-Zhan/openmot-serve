import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata, metadata_path_for
from tracking.motchallenge import parse_result
from tracking.sequence import SequenceError, detect_sequence, list_frames, track_sequence
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256

FRAME_HEIGHT, FRAME_WIDTH = 360, 640


def metadata(params: TrackerParams = TrackerParams()) -> RunMetadata:
    return RunMetadata(params, WEIGHTS_ID, WEIGHTS_SHA256, "fp16")


def white_box_frame(index: int) -> np.ndarray:
    image = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    left = 100 + 3 * index
    image[100:200, left : left + 40] = 255
    return image


def detect_white_box(image: np.ndarray) -> np.ndarray:
    rows, columns = np.where(image[:, :, 0] > 200)
    if len(rows) == 0:
        return np.zeros((0, 5))
    return np.array([[columns.min(), rows.min(), columns.max() + 1, rows.max() + 1, 0.9]])


def make_sequence(directory: Path, frame_count: int = 10) -> Path:
    directory.mkdir(parents=True)
    for index in range(frame_count):
        cv2.imwrite(str(directory / f"{index + 1:06d}.png"), white_box_frame(index))
    return directory


def assert_nothing_written(output_dir: Path) -> None:
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_frames_are_listed_in_numeric_order(tmp_path: Path) -> None:
    directory = tmp_path / "seq"
    directory.mkdir()
    for number in (10, 2, 1, 3, 4, 5, 6, 7, 8, 9):
        cv2.imwrite(str(directory / f"{number}.png"), white_box_frame(0))
    assert [path.name for path in list_frames(directory)] == [f"{n}.png" for n in range(1, 11)]


def test_non_image_files_in_the_folder_are_ignored(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 3)
    (directory / "seqinfo.ini").write_text("[Sequence]\n")
    (directory / "Thumbs.db").write_bytes(b"\x00")
    assert len(list_frames(directory)) == 3


def test_valid_folder_produces_result_and_metadata(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 10)
    result_path = tmp_path / "out" / "SEQ.txt"
    summary = track_sequence(directory, result_path, detect_white_box, metadata())
    rows = parse_result(result_path.read_text())
    assert [row.frame for row in rows] == list(range(1, 11))
    assert {row.identity for row in rows} == {1}
    assert summary.frame_count == 10 and summary.row_count == 10
    recorded = json.loads(metadata_path_for(result_path).read_text())
    assert recorded["tracker"]["detection_threshold"] == TrackerParams().detection_threshold
    assert recorded["weights"]["sha256"] == WEIGHTS_SHA256


def test_custom_parameters_are_used_and_recorded(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 10)
    result_path = tmp_path / "out" / "SEQ.txt"
    strict = TrackerParams(detection_threshold=0.95)
    track_sequence(directory, result_path, detect_white_box, metadata(strict))
    assert result_path.read_text() == ""
    recorded = json.loads(metadata_path_for(result_path).read_text())
    assert recorded["tracker"]["detection_threshold"] == 0.95


def test_detector_receives_frames_in_order(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 5)
    seen: list[int] = []

    def recording_detector(image: np.ndarray) -> np.ndarray:
        seen.append(int(np.where(image[:, :, 0] > 200)[1].min()))
        return detect_white_box(image)

    detect_sequence(directory, recording_detector)
    assert seen == [100, 103, 106, 109, 112]


def test_same_input_gives_identical_result_bytes(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 10)
    first, second = tmp_path / "a" / "SEQ.txt", tmp_path / "b" / "SEQ.txt"
    track_sequence(directory, first, detect_white_box, metadata())
    track_sequence(directory, second, detect_white_box, metadata())
    assert first.read_bytes() == second.read_bytes()
    assert metadata_path_for(first).read_bytes() == metadata_path_for(second).read_bytes()


def test_missing_folder_is_an_error_and_writes_nothing(tmp_path: Path) -> None:
    result_path = tmp_path / "out" / "SEQ.txt"
    with pytest.raises(SequenceError, match="not found"):
        track_sequence(tmp_path / "absent", result_path, detect_white_box, metadata())
    assert_nothing_written(result_path.parent)


def test_empty_folder_is_an_error_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    result_path = tmp_path / "out" / "SEQ.txt"
    with pytest.raises(SequenceError, match="no frames"):
        track_sequence(tmp_path / "empty", result_path, detect_white_box, metadata())
    assert_nothing_written(result_path.parent)


def test_unreadable_frame_is_an_error_naming_the_file(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 6)
    (directory / "000004.png").write_bytes(b"this is not an image")
    result_path = tmp_path / "out" / "SEQ.txt"
    with pytest.raises(SequenceError, match="000004.png"):
        track_sequence(directory, result_path, detect_white_box, metadata())
    assert_nothing_written(result_path.parent)


def test_gap_in_frame_numbering_is_an_error(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 5)
    (directory / "000003.png").unlink()
    with pytest.raises(SequenceError, match="consecutive"):
        list_frames(directory)


def test_failed_run_leaves_an_earlier_result_untouched(tmp_path: Path) -> None:
    directory = make_sequence(tmp_path / "seq", 5)
    result_path = tmp_path / "out" / "SEQ.txt"
    track_sequence(directory, result_path, detect_white_box, metadata())
    before = result_path.read_bytes()
    (directory / "000002.png").write_bytes(b"broken")
    with pytest.raises(SequenceError):
        track_sequence(directory, result_path, detect_white_box, metadata())
    assert result_path.read_bytes() == before
    assert sorted(path.name for path in result_path.parent.iterdir()) == ["SEQ.meta.json", "SEQ.txt"]
