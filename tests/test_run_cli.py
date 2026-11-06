import json
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from tracking.config import metadata_path_for
from tracking.motchallenge import parse_result
from tracking.run import main

REPOSITORY = Path(__file__).resolve().parents[1]
WEIGHTS_PATH = REPOSITORY / "models" / "yolox_x.pth"
SEQUENCE_09 = REPOSITORY / "data" / "MOT17" / "train" / "MOT17-09-FRCNN"


def white_box_frame(index: int) -> np.ndarray:
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[100:200, 100 + 3 * index : 140 + 3 * index] = 255
    return image


def detect_white_box(image: np.ndarray) -> np.ndarray:
    rows, columns = np.where(image[:, :, 0] > 200)
    if len(rows) == 0:
        return np.zeros((0, 5))
    return np.array([[columns.min(), rows.min(), columns.max() + 1, rows.max() + 1, 0.9]])


def fake_loader(weights: Path, precision: str, device: str) -> Callable[[np.ndarray], np.ndarray]:
    return detect_white_box


def make_sequence(directory: Path, frame_count: int = 8) -> Path:
    directory.mkdir(parents=True)
    for index in range(frame_count):
        cv2.imwrite(str(directory / f"{index + 1:06d}.png"), white_box_frame(index))
    return directory


def run(arguments: list[str], loader: Callable = fake_loader) -> int:
    return main(arguments, load_detect_frame=loader)


def assert_nothing_written(output_dir: Path) -> None:
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_successful_run_exits_zero_and_writes_result_and_metadata(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output)]) == 0
    assert len(parse_result(output.read_text())) == 8
    assert json.loads(metadata_path_for(output).read_text())["precision"] == "fp16"


def test_config_file_values_are_used_and_recorded(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    config = tmp_path / "tracking.toml"
    config.write_text("[tracker]\ndetection_threshold = 0.95\ntrack_buffer = 12\n")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output), "--config", str(config)]) == 0
    assert output.read_text() == ""
    recorded = json.loads(metadata_path_for(output).read_text())["tracker"]
    assert recorded["detection_threshold"] == 0.95 and recorded["track_buffer"] == 12


def test_precision_option_is_recorded(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output), "--precision", "fp32", "--device", "cpu"]) == 0
    assert json.loads(metadata_path_for(output).read_text())["precision"] == "fp32"


def test_missing_folder_exits_nonzero_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(tmp_path / "absent"), "--output", str(output)]) != 0
    assert "not found" in capsys.readouterr().err
    assert_nothing_written(output.parent)


def test_empty_folder_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(tmp_path / "empty"), "--output", str(output)]) != 0
    assert_nothing_written(output.parent)


def test_unreadable_frame_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    (frames / "000003.png").write_bytes(b"broken")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output)]) != 0
    assert_nothing_written(output.parent)


def test_invalid_config_exits_nonzero_before_loading_the_detector(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    config = tmp_path / "tracking.toml"
    config.write_text("[tracker]\ndetection_treshold = 0.5\n")
    loaded: list[bool] = []

    def spying_loader(weights: Path, precision: str, device: str) -> Callable:
        loaded.append(True)
        return detect_white_box

    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output), "--config", str(config)], spying_loader) != 0
    assert not loaded
    assert_nothing_written(output.parent)


def test_fp16_on_cpu_is_rejected_by_the_real_loader(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tracking.run import load_default_detect_frame

    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    arguments = [str(frames), "--output", str(output), "--device", "cpu", "--precision", "fp16"]
    assert run(arguments, load_default_detect_frame) != 0
    assert "fp16" in capsys.readouterr().err
    assert_nothing_written(output.parent)


def test_missing_weights_exit_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tracking.run import load_default_detect_frame

    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    arguments = [str(frames), "--output", str(output), "--device", "cpu", "--precision", "fp32",
                 "--weights", str(tmp_path / "no-such.pth")]
    assert run(arguments, load_default_detect_frame) != 0
    assert "no-such.pth" in capsys.readouterr().err
    assert_nothing_written(output.parent)


@pytest.mark.gpu
@pytest.mark.data
def test_real_detector_on_a_real_sequence_end_to_end(tmp_path: Path) -> None:
    from tracking.run import load_default_detect_frame

    frames = tmp_path / "frames"
    frames.mkdir()
    for number in range(1, 41):
        (frames / f"{number:06d}.jpg").symlink_to(SEQUENCE_09 / "img1" / f"{number:06d}.jpg")
    output = tmp_path / "out" / "MOT17-09.txt"
    assert run([str(frames), "--output", str(output)], load_default_detect_frame) == 0
    rows = parse_result(output.read_text())
    assert {row.frame for row in rows} == set(range(1, 41))
    assert len({row.identity for row in rows}) < 40
