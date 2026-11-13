import numpy as np
import pytest
import torch

from tracking.detector import (
    Detector,
    DetectorSettings,
    detect,
    letterbox,
    select_persons,
    torch_staged_detector,
)

CHANNELS = 85
IMAGE_HEIGHT, IMAGE_WIDTH = 360, 640


def _raw() -> torch.Tensor:
    rows = []
    for center_x, objectness in ((100.0, 0.9), (400.0, 0.8)):
        row = [center_x, 100.0, 40.0, 100.0, objectness] + [0.0] * (CHANNELS - 5)
        row[5] = 0.9
        rows.append(row)
    return torch.tensor([rows], dtype=torch.float32)


class FakeModel:
    def __init__(self) -> None:
        self.batches: list[torch.Tensor] = []

    def __call__(self, batch: torch.Tensor) -> torch.Tensor:
        self.batches.append(batch)
        return _raw()


def _detector(model: FakeModel, precision: str = "fp32") -> Detector:
    return Detector(model, DetectorSettings(precision=precision), "cpu")  # type: ignore[arg-type]


def _image() -> np.ndarray:
    return np.random.default_rng(0).integers(0, 255, (IMAGE_HEIGHT, IMAGE_WIDTH, 3), dtype=np.uint8)


def _expected_detections(image: np.ndarray, settings: DetectorSettings) -> np.ndarray:
    _, scale = letterbox(image, settings.input_height, settings.input_width)
    persons = select_persons(_raw()[0], settings.confidence_floor, settings.nms_threshold)
    expected = persons.numpy().astype(np.float64)
    expected[:, :4] /= scale
    return expected


def test_detect_equals_the_hand_computed_pipeline() -> None:
    detector = _detector(FakeModel())
    image = _image()
    assert np.array_equal(detect(detector, image), _expected_detections(image, detector.settings))


def test_the_stages_compose_to_the_same_detections_as_detect() -> None:
    detector = _detector(FakeModel())
    staged = torch_staged_detector(detector)
    image = _image()
    prepared = staged.prepare(image)
    composed = staged.finish(staged.infer(prepared), prepared)
    assert np.array_equal(composed, detect(detector, image))


def test_prepare_builds_a_float_batch_and_the_letterbox_scale() -> None:
    staged = torch_staged_detector(_detector(FakeModel()))
    prepared = staged.prepare(_image())
    assert prepared.batch.shape == (1, 3, 800, 1440)
    assert prepared.batch.dtype == torch.float32
    assert prepared.scale == pytest.approx(min(800 / IMAGE_HEIGHT, 1440 / IMAGE_WIDTH))


def test_infer_runs_only_the_model_and_returns_the_first_batch_item() -> None:
    model = FakeModel()
    staged = torch_staged_detector(_detector(model))
    prepared = staged.prepare(_image())
    raw = staged.infer(prepared)
    assert len(model.batches) == 1 and model.batches[0] is prepared.batch
    assert raw.shape == (2, CHANNELS)


def test_finish_scales_boxes_back_to_the_original_image() -> None:
    staged = torch_staged_detector(_detector(FakeModel()))
    prepared = staged.prepare(_image())
    detections = staged.finish(_raw()[0], prepared)
    assert detections.dtype == np.float64 and detections.shape == (2, 5)
    assert detections[0, 0] == pytest.approx((100.0 - 20.0) / prepared.scale)


def test_synchronize_does_nothing_on_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("torch.cuda.synchronize must not be called for a CPU detector")

    monkeypatch.setattr(torch.cuda, "synchronize", forbidden)
    torch_staged_detector(_detector(FakeModel())).synchronize()


def test_synchronize_waits_for_the_gpu_on_a_cuda_device(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(torch.cuda, "synchronize", lambda *_: calls.append("sync"))
    detector = Detector(FakeModel(), DetectorSettings(), "cuda")  # type: ignore[arg-type]
    torch_staged_detector(detector).synchronize()
    assert calls == ["sync"]
