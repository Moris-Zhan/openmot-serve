import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from inference.calibration import CalibrationError, CalibrationSelection
from inference.int8_calibrator import calibration_cache_sha256, create_entropy_calibrator

REPOSITORY = Path(__file__).resolve().parent.parent
INPUT_NAME = "images"


class FakeCalibratorBase:
    def __init__(self) -> None:
        self.base_initialised = True


RUNTIME = SimpleNamespace(IInt8EntropyCalibrator2=FakeCalibratorBase)


class Recorder:
    def __init__(self, frames: tuple[Path, ...], unreadable: Path | None = None) -> None:
        self.frames = frames
        self.unreadable = unreadable
        self.read: list[Path] = []
        self.prepared: list[np.ndarray] = []
        self.batches: list[torch.Tensor] = []

    def read_image(self, path: Path) -> np.ndarray | None:
        self.read.append(path)
        if path == self.unreadable:
            return None
        return np.full((4, 6, 3), len(self.read), np.uint8)

    def prepare(self, image: np.ndarray) -> SimpleNamespace:
        self.prepared.append(image)
        batch = torch.full((1, 3, 8, 8), float(len(self.prepared)), dtype=torch.float32)
        self.batches.append(batch)
        return SimpleNamespace(batch=batch, scale=1.0)


def build(tmp_path: Path, count: int = 3, unreadable_index: int | None = None):
    frames = tuple(tmp_path / f"{index:06d}.jpg" for index in range(1, count + 1))
    recorder = Recorder(frames, None if unreadable_index is None else frames[unreadable_index])
    calibrator = create_entropy_calibrator(
        RUNTIME,
        CalibrationSelection(frames, "f" * 64),
        recorder.prepare,
        tmp_path / "model.engine.calib",
        recorder.read_image,
    )
    return calibrator, recorder


def test_the_calibrator_extends_the_tensorrt_entropy_calibrator(tmp_path: Path) -> None:
    calibrator, _ = build(tmp_path)
    assert isinstance(calibrator, FakeCalibratorBase) and calibrator.base_initialised


def test_the_batch_size_is_one(tmp_path: Path) -> None:
    calibrator, _ = build(tmp_path)
    assert calibrator.get_batch_size() == 1


def test_each_call_hands_over_the_prepared_tensor_address_until_the_list_is_used_up(tmp_path: Path) -> None:
    calibrator, recorder = build(tmp_path, count=3)
    addresses = [calibrator.get_batch([INPUT_NAME]) for _ in range(3)]
    assert addresses == [[batch.data_ptr()] for batch in recorder.batches]
    assert recorder.read == list(recorder.frames)
    assert calibrator.get_batch([INPUT_NAME]) is None
    assert len(recorder.read) == 3 and calibrator.error is None


def test_the_tensor_is_kept_alive_until_the_next_call(tmp_path: Path) -> None:
    calibrator, recorder = build(tmp_path, count=2)
    first = calibrator.get_batch([INPUT_NAME])
    assert first == [recorder.batches[0].data_ptr()]
    recorder.batches.clear()
    assert calibrator.get_batch([INPUT_NAME]) is not None


def test_more_than_one_input_name_ends_the_calibration_with_an_error(tmp_path: Path) -> None:
    calibrator, _ = build(tmp_path)
    assert calibrator.get_batch(["images", "extra"]) is None
    assert isinstance(calibrator.error, CalibrationError) and "input" in str(calibrator.error)


def test_an_unreadable_frame_ends_the_calibration_and_names_the_frame(tmp_path: Path) -> None:
    calibrator, recorder = build(tmp_path, count=3, unreadable_index=1)
    assert calibrator.get_batch([INPUT_NAME]) is not None
    assert calibrator.get_batch([INPUT_NAME]) is None
    assert isinstance(calibrator.error, CalibrationError)
    assert str(recorder.frames[1]) in str(calibrator.error)


def test_a_missing_cache_reads_as_nothing_and_is_written_after_calibration(tmp_path: Path) -> None:
    calibrator, _ = build(tmp_path)
    assert calibrator.read_calibration_cache() is None
    calibrator.write_calibration_cache(b"scales")
    assert (tmp_path / "model.engine.calib").read_bytes() == b"scales"


def test_an_existing_cache_is_read_back(tmp_path: Path) -> None:
    (tmp_path / "model.engine.calib").write_bytes(b"stored")
    calibrator, _ = build(tmp_path)
    assert calibrator.read_calibration_cache() == b"stored"


def test_the_cache_hash_is_the_sha256_of_the_file(tmp_path: Path) -> None:
    path = tmp_path / "model.engine.calib"
    path.write_bytes(b"scales")
    assert calibration_cache_sha256(path) == hashlib.sha256(b"scales").hexdigest()


def test_the_cache_hash_of_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="calibration cache"):
        calibration_cache_sha256(tmp_path / "absent.calib")


def test_the_module_imports_without_torch_or_tensorrt() -> None:
    script = (
        "import sys; sys.modules['torch'] = None; sys.modules['tensorrt'] = None; "
        "from inference.int8_calibrator import create_entropy_calibrator"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=REPOSITORY, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
