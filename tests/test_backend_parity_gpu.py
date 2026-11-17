from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from inference.artifacts import DEFAULT_ARTIFACT_DIR, Observed, resolve_artifact
from inference.backends import installed_tensorrt_version, load_staged_detector
from tracking.detector import DetectorSettings, run_staged
from tracking.yolox import WEIGHTS_SHA256

pytestmark = [pytest.mark.gpu, pytest.mark.data]

REPOSITORY = Path(__file__).resolve().parents[1]
WEIGHTS = REPOSITORY / "models" / "yolox_x.pth"
FRAMES = sorted((REPOSITORY / "data/MOT17/train/MOT17-09-FRCNN/img1").glob("*.jpg"))[:30]
BOX_TOLERANCE_PIXELS = 0.5
SCORE_TOLERANCE = 1e-3


def _detections(staged, images: list[np.ndarray]) -> list[np.ndarray]:
    return [run_staged(staged, image) for image in images]


@pytest.fixture(scope="module")
def images() -> list[np.ndarray]:
    assert len(FRAMES) == 30
    return [cv2.imread(str(path)) for path in FRAMES]


@pytest.fixture(scope="module")
def torch_fp32_detections(images: list[np.ndarray]) -> list[np.ndarray]:
    previous = (torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32)
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    try:
        staged = load_staged_detector("torch", WEIGHTS, DetectorSettings(precision="fp32"), "cuda", None)
        return _detections(staged, images)
    finally:
        torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32 = previous


def _nearest_box_gaps(reference: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    corner_gaps = np.abs(reference[:, None, :4] - candidate[None, :, :4]).max(axis=2)
    return corner_gaps.min(axis=1), corner_gaps.argmin(axis=1)


def _onnx_staged():
    pytest.importorskip("onnxruntime")
    settings = DetectorSettings(backend="onnx", precision="fp32")
    artifact = resolve_artifact(
        "onnx", settings, Observed(WEIGHTS_SHA256, None, None), REPOSITORY / DEFAULT_ARTIFACT_DIR, None
    )
    return load_staged_detector("onnx", WEIGHTS, settings, "cuda", artifact)


def test_onnx_fp32_detections_match_pytorch_fp32_on_real_frames(
    images: list[np.ndarray], torch_fp32_detections: list[np.ndarray]
) -> None:
    candidate = _detections(_onnx_staged(), images)
    counts = [(len(reference), len(other)) for reference, other in zip(torch_fp32_detections, candidate, strict=True)]
    assert all(reference == other for reference, other in counts), f"detection counts differ: {counts}"
    box_gap, score_gap = 0.0, 0.0
    for reference, other in zip(torch_fp32_detections, candidate, strict=True):
        gaps, matched = _nearest_box_gaps(reference, other)
        box_gap = max(box_gap, float(gaps.max(initial=0.0)))
        score_gap = max(score_gap, float(np.abs(reference[:, 4] - other[matched, 4]).max(initial=0.0)))
    print(f"onnx vs torch fp32 (TF32 off on both sides): max box gap {box_gap:.4f} px, max score gap {score_gap:.6f}")
    assert box_gap <= BOX_TOLERANCE_PIXELS
    assert score_gap <= SCORE_TOLERANCE


def test_onnx_runs_on_the_gpu_and_returns_the_pytorch_types(images: list[np.ndarray]) -> None:
    detections = run_staged(_onnx_staged(), images[0])
    assert detections.dtype == np.float64 and detections.ndim == 2 and detections.shape[1] == 5
    assert torch.cuda.is_available()


def _tensorrt_staged():
    pytest.importorskip("tensorrt")
    settings = DetectorSettings(backend="tensorrt", precision="fp16")
    observed = Observed(WEIGHTS_SHA256, torch.cuda.get_device_name(), installed_tensorrt_version())
    artifact = resolve_artifact("tensorrt", settings, observed, REPOSITORY / DEFAULT_ARTIFACT_DIR, None)
    return load_staged_detector("tensorrt", WEIGHTS, settings, "cuda", artifact)


@pytest.fixture(scope="module")
def torch_fp16_detections(images: list[np.ndarray]) -> list[np.ndarray]:
    staged = load_staged_detector("torch", WEIGHTS, DetectorSettings(precision="fp16"), "cuda", None)
    return _detections(staged, images)


def test_tensorrt_detects_on_real_frames_with_the_pytorch_types(images: list[np.ndarray]) -> None:
    for detections in _detections(_tensorrt_staged(), images):
        assert detections.dtype == np.float64 and detections.ndim == 2 and detections.shape[1] == 5
        if len(detections):
            assert detections[:, 4].min() >= 0.1 and detections[:, 4].max() <= 1.0


def test_tensorrt_detection_counts_are_recorded_against_pytorch_fp16(
    images: list[np.ndarray], torch_fp16_detections: list[np.ndarray]
) -> None:
    candidate = _detections(_tensorrt_staged(), images)
    reference_total = sum(len(frame) for frame in torch_fp16_detections)
    candidate_total = sum(len(frame) for frame in candidate)
    differing = [
        (index, len(a), len(b)) for index, (a, b) in enumerate(zip(torch_fp16_detections, candidate, strict=True))
        if len(a) != len(b)
    ]
    print(f"tensorrt fp16 vs torch fp16: total {candidate_total} vs {reference_total}; differing frames {differing}")
    assert candidate_total > 0
