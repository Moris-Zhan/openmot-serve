from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from tracking.bytetrack import run_tracker
from tracking.config import RunMetadata, metadata_path_for, write_metadata
from tracking.files import write_text_atomic
from tracking.motchallenge import format_result

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

DetectFrame = Callable[[np.ndarray], np.ndarray]


class SequenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SequenceSummary:
    frame_count: int
    row_count: int


def list_frames(frames_dir: Path) -> tuple[Path, ...]:
    if not frames_dir.is_dir():
        raise SequenceError(f"frame folder not found: {frames_dir}")
    images = [path for path in frames_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file()]
    if not images:
        raise SequenceError(f"no frames found in {frames_dir}")
    numbered: dict[int, Path] = {}
    for path in images:
        if not path.stem.isdigit():
            raise SequenceError(f"frame file names must be numbers, got {path.name}")
        number = int(path.stem)
        if number in numbered:
            raise SequenceError(f"two files share frame number {number}: {numbered[number].name}, {path.name}")
        numbered[number] = path
    ordered = sorted(numbered)
    if ordered != list(range(ordered[0], ordered[0] + len(ordered))):
        raise SequenceError(f"frame numbering in {frames_dir} must be consecutive")
    return tuple(numbered[number] for number in ordered)


def detect_sequence(frames_dir: Path, detect_frame: DetectFrame) -> tuple[np.ndarray, ...]:
    detections = []
    for path in list_frames(frames_dir):
        image = cv2.imread(str(path))
        if image is None:
            raise SequenceError(f"cannot read frame {path.name} in {frames_dir}")
        detections.append(detect_frame(image))
    return tuple(detections)


def track_sequence(
    frames_dir: Path, result_path: Path, detect_frame: DetectFrame, metadata: RunMetadata
) -> SequenceSummary:
    detections = detect_sequence(frames_dir, detect_frame)
    rows = run_tracker(detections, metadata.tracker)
    text = format_result(rows)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    write_metadata(metadata_path_for(result_path), metadata)
    write_text_atomic(result_path, text)
    return SequenceSummary(frame_count=len(detections), row_count=len(rows))
