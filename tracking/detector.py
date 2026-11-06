from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision

from tracking.config import SUPPORTED_PRECISIONS
from tracking.yolox import YOLOX, load_yolox_x

PERSON_CLASS = 0
CONFIDENCE_FLOOR = 0.1
NMS_THRESHOLD = 0.65
PAD_VALUE = 114


@dataclass(frozen=True)
class DetectorSettings:
    input_height: int = 800
    input_width: int = 1440
    precision: str = "fp16"
    nms_threshold: float = NMS_THRESHOLD
    confidence_floor: float = CONFIDENCE_FLOOR

    def __post_init__(self) -> None:
        if self.precision not in SUPPORTED_PRECISIONS:
            raise ValueError(
                f"unsupported precision {self.precision!r}, expected one of {SUPPORTED_PRECISIONS}"
            )


@dataclass(frozen=True)
class Detector:
    model: YOLOX
    settings: DetectorSettings
    device: str


def letterbox(image: np.ndarray, height: int, width: int) -> tuple[np.ndarray, float]:
    scale = min(height / image.shape[0], width / image.shape[1])
    resized_size = (round(image.shape[1] * scale), round(image.shape[0] * scale))
    resized = cv2.resize(image, resized_size, interpolation=cv2.INTER_LINEAR)
    canvas = np.full((height, width, 3), PAD_VALUE, dtype=np.uint8)
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    return canvas, scale


def select_persons(
    raw: torch.Tensor,
    confidence_floor: float = CONFIDENCE_FLOOR,
    nms_threshold: float = NMS_THRESHOLD,
) -> torch.Tensor:
    class_scores, class_ids = raw[:, 5:].max(dim=1)
    scores = raw[:, 4] * class_scores
    keep = (class_ids == PERSON_CLASS) & (scores >= confidence_floor)
    centers_and_sizes, scores = raw[keep, :4], scores[keep]
    corners = torch.cat(
        [
            centers_and_sizes[:, :2] - centers_and_sizes[:, 2:] / 2,
            centers_and_sizes[:, :2] + centers_and_sizes[:, 2:] / 2,
        ],
        dim=1,
    )
    order = torchvision.ops.nms(corners, scores, nms_threshold)
    return torch.cat([corners[order], scores[order, None]], dim=1)


def load_detector(weights_path: Path, settings: DetectorSettings, device: str) -> Detector:
    if settings.precision == "fp16" and not device.startswith("cuda"):
        raise ValueError("fp16 precision needs a CUDA device; use precision 'fp32' on the CPU")
    return Detector(load_yolox_x(weights_path, device), settings, device)


def detect(detector: Detector, image_bgr: np.ndarray) -> np.ndarray:
    settings = detector.settings
    canvas, scale = letterbox(image_bgr, settings.input_height, settings.input_width)
    batch = torch.from_numpy(canvas).permute(2, 0, 1)[None].float().to(detector.device)
    with torch.no_grad(), torch.autocast(
        device_type="cuda", dtype=torch.float16, enabled=settings.precision == "fp16"
    ):
        raw = detector.model(batch)[0]
    persons = select_persons(raw.float(), settings.confidence_floor, settings.nms_threshold)
    detections = persons.cpu().numpy().astype(np.float64)
    detections[:, :4] /= scale
    return detections
