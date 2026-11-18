import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from inference.artifacts import (
    BUILD_COMMAND,
    DEFAULT_ARTIFACT_DIR,
    Observed,
    engine_name,
    read_sidecar,
    resolve_artifact,
    sidecar_path,
)
from inference.backends import installed_tensorrt_version, load_staged_detector
from inference.calibration import DEFAULT_CALIBRATION_FRAMES, select_calibration_frames
from tracking.detector import DetectorSettings, run_staged
from tracking.yolox import WEIGHTS_SHA256

pytestmark = [pytest.mark.gpu, pytest.mark.data]

REPOSITORY = Path(__file__).resolve().parents[1]
WEIGHTS = REPOSITORY / "models" / "yolox_x.pth"
DATA_ROOT = REPOSITORY / "data" / "MOT17"
ENGINE_DIRECTORY = REPOSITORY / DEFAULT_ARTIFACT_DIR
FRAMES = sorted((DATA_ROOT / "train" / "MOT17-09-FRCNN" / "img1").glob("*.jpg"))[:30]
REPEATS = 3


def _require_environment() -> str:
    if not torch.cuda.is_available():
        pytest.skip("a CUDA GPU is required")
    pytest.importorskip("tensorrt")
    version = installed_tensorrt_version()
    if version is None:
        pytest.skip("the tensorrt package is not installed")
    for needed in (WEIGHTS, DATA_ROOT / "train"):
        if not needed.exists():
            pytest.skip(f"{needed} is required")
    return version


@pytest.fixture(scope="module")
def engine_path() -> Path:
    version = _require_environment()
    path = ENGINE_DIRECTORY / engine_name(
        WEIGHTS_SHA256, 800, 1440, version, torch.cuda.get_device_name(), "int8"
    )
    if not path.is_file():
        pytest.skip(f"build the INT8 engine first: {BUILD_COMMAND} --precision int8 ({path.name} is missing)")
    return path


@pytest.fixture(scope="module")
def staged(engine_path: Path):
    settings = DetectorSettings(backend="tensorrt", precision="int8")
    observed = Observed(WEIGHTS_SHA256, torch.cuda.get_device_name(), installed_tensorrt_version())
    artifact = resolve_artifact("tensorrt", settings, observed, ENGINE_DIRECTORY, None)
    return load_staged_detector("tensorrt", WEIGHTS, settings, "cuda", artifact)


@pytest.fixture(scope="module")
def images() -> list[np.ndarray]:
    assert len(FRAMES) == 30
    return [cv2.imread(str(path)) for path in FRAMES]


def test_the_engine_and_its_sidecar_record_an_int8_build_with_fp16_fallback(engine_path: Path) -> None:
    sidecar = read_sidecar(sidecar_path(engine_path))
    assert sidecar["precision"] == "int8"
    assert sidecar["builder_flags"] == ["INT8", "FP16"]
    assert sidecar["weights_sha256"] == WEIGHTS_SHA256


def test_the_calibration_record_matches_the_tuning_half_selection_and_the_cache(engine_path: Path) -> None:
    calibration = read_sidecar(sidecar_path(engine_path))["calibration"]
    selection = select_calibration_frames(DATA_ROOT, int(calibration["frames"]))
    assert calibration["calibrator"] == "IInt8EntropyCalibrator2"
    assert calibration["frames"] == DEFAULT_CALIBRATION_FRAMES
    assert calibration["selection_sha256"] == selection.sha256
    cache = engine_path.with_name(calibration["cache"])
    assert hashlib.sha256(cache.read_bytes()).hexdigest() == calibration["cache_sha256"]


def test_the_engine_passes_the_load_time_verification(engine_path: Path) -> None:
    settings = DetectorSettings(backend="tensorrt", precision="int8")
    observed = Observed(WEIGHTS_SHA256, torch.cuda.get_device_name(), installed_tensorrt_version())
    artifact = resolve_artifact("tensorrt", settings, observed, ENGINE_DIRECTORY, None)
    assert artifact is not None and artifact.path == engine_path


def test_the_int8_engine_detects_people_on_real_frames(staged, images: list[np.ndarray]) -> None:
    detections = [run_staged(staged, image) for image in images]
    for found in detections:
        assert found.dtype == np.float64 and found.ndim == 2 and found.shape[1] == 5
        if len(found):
            assert found[:, 4].min() >= 0.1 and found[:, 4].max() <= 1.0
    counts = [len(found) for found in detections]
    print(f"int8 detections per frame over {len(images)} frames: min {min(counts)}, max {max(counts)}")
    assert sum(counts) > 0 and sum(1 for count in counts if count > 0) >= len(images) * 0.9


def test_repeated_runs_of_the_same_engine_give_identical_detections(staged, images: list[np.ndarray]) -> None:
    runs = [[run_staged(staged, image).tobytes() for image in images] for _ in range(REPEATS)]
    identical = all(run == runs[0] for run in runs[1:])
    print(f"{REPEATS} repeated runs over {len(images)} frames byte-identical: {identical}")
    assert identical
