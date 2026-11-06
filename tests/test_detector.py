from pathlib import Path

import numpy as np
import pytest
import torch

from tracking.detector import (
    CONFIDENCE_FLOOR,
    DetectorSettings,
    detect,
    letterbox,
    load_detector,
    select_persons,
)

REPOSITORY = Path(__file__).resolve().parents[1]
WEIGHTS_PATH = REPOSITORY / "models" / "yolox_x.pth"
SEQUENCE_09 = REPOSITORY / "data" / "MOT17" / "train" / "MOT17-09-FRCNN"
CHANNELS = 85
PERSON, CAR = 0, 2


def candidate(center_x: float, center_y: float, width: float, height: float,
              objectness: float, class_id: int, class_probability: float) -> list[float]:
    row = [center_x, center_y, width, height, objectness] + [0.0] * (CHANNELS - 5)
    row[5 + class_id] = class_probability
    return row


def raw_of(*rows: list[float]) -> torch.Tensor:
    return torch.tensor(rows, dtype=torch.float32).reshape(-1, CHANNELS)


def test_letterbox_keeps_aspect_ratio_and_pads_bottom_right() -> None:
    image = np.full((1080, 1920, 3), 200, dtype=np.uint8)
    canvas, scale = letterbox(image, height=800, width=1440)
    assert canvas.shape == (800, 1440, 3) and canvas.dtype == np.uint8
    assert scale == pytest.approx(min(800 / 1080, 1440 / 1920))
    resized_height, resized_width = round(1080 * scale), round(1920 * scale)
    assert np.all(canvas[: resized_height - 1, : resized_width - 1] == 200)
    assert np.all(canvas[resized_height + 1 :, :] == 114)


def test_letterbox_handles_portrait_images() -> None:
    image = np.zeros((1920, 1080, 3), dtype=np.uint8)
    canvas, scale = letterbox(image, height=800, width=1440)
    assert canvas.shape == (800, 1440, 3)
    assert scale == pytest.approx(800 / 1920)
    assert np.all(canvas[:, int(1080 * scale) + 2 :] == 114)


def test_only_person_candidates_are_kept() -> None:
    raw = raw_of(
        candidate(100, 100, 40, 100, 0.9, PERSON, 0.9),
        candidate(400, 100, 80, 50, 0.9, CAR, 0.95),
    )
    kept = select_persons(raw)
    assert kept.shape == (1, 5)
    assert kept[0, 0].item() == pytest.approx(80.0)


def test_candidate_whose_best_class_is_not_person_is_dropped() -> None:
    row = candidate(100, 100, 40, 100, 0.9, PERSON, 0.4)
    row[5 + CAR] = 0.8
    assert select_persons(raw_of(row)).shape == (0, 5)


def test_score_is_objectness_times_class_probability_and_floor_applies() -> None:
    raw = raw_of(
        candidate(100, 100, 40, 100, 0.5, PERSON, 0.8),
        candidate(300, 100, 40, 100, 0.3, PERSON, 0.3),
    )
    kept = select_persons(raw)
    assert kept.shape == (1, 5)
    assert kept[0, 4].item() == pytest.approx(0.4)
    assert CONFIDENCE_FLOOR == 0.1


def test_overlapping_people_are_suppressed_and_the_best_survives() -> None:
    raw = raw_of(
        candidate(100, 100, 40, 100, 0.9, PERSON, 0.9),
        candidate(102, 101, 40, 100, 0.8, PERSON, 0.8),
        candidate(500, 100, 40, 100, 0.7, PERSON, 0.7),
    )
    kept = select_persons(raw)
    assert kept.shape == (2, 5)
    assert kept[0, 4].item() == pytest.approx(0.81)


def test_empty_input_gives_empty_detections() -> None:
    assert select_persons(raw_of()).shape == (0, 5)


def test_settings_are_comparable_and_hashable_for_cache_keys() -> None:
    assert DetectorSettings() == DetectorSettings()
    assert hash(DetectorSettings()) == hash(DetectorSettings())
    assert DetectorSettings(precision="fp32") != DetectorSettings()


def test_fp16_requires_a_cuda_device() -> None:
    with pytest.raises(ValueError, match="fp16"):
        load_detector(WEIGHTS_PATH, DetectorSettings(precision="fp16"), device="cpu")


@pytest.mark.gpu
def test_detect_returns_well_formed_array_for_a_synthetic_image() -> None:
    detector = load_detector(WEIGHTS_PATH, DetectorSettings(), device="cuda")
    image = np.random.default_rng(0).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    detections = detect(detector, image)
    assert detections.ndim == 2 and detections.shape[1] == 5
    assert detections.dtype == np.float64
    if len(detections):
        assert detections[:, 4].min() >= CONFIDENCE_FLOOR and detections[:, 4].max() <= 1.0


@pytest.mark.gpu
@pytest.mark.data
def test_detect_finds_the_labelled_pedestrians_in_a_real_frame() -> None:
    import cv2

    detector = load_detector(WEIGHTS_PATH, DetectorSettings(), device="cuda")
    detections = detect(detector, cv2.imread(str(SEQUENCE_09 / "img1" / "000001.jpg")))
    labelled = [(260, 450, 102, 262), (1686, 387, 171, 345)]
    for left, top, width, height in labelled:
        box = np.array([left, top, left + width, top + height], dtype=np.float64)
        top_left = np.maximum(detections[:, :2], box[:2])
        bottom_right = np.minimum(detections[:, 2:4], box[2:])
        overlap = np.clip(bottom_right - top_left, 0, None).prod(axis=1)
        union = (detections[:, 2] - detections[:, 0]) * (detections[:, 3] - detections[:, 1]) + width * height - overlap
        assert (overlap / union).max() > 0.7
