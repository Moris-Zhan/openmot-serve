import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from tracking.detector import DetectorSettings
from tracking.sequence import DetectFrame, detect_sequence, list_frames

_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DETECTION_COLUMNS = 5


@dataclass(frozen=True)
class CacheKey:
    weights_sha256: str
    input_height: int
    input_width: int
    precision: str
    nms_threshold: float
    confidence_floor: float

    @classmethod
    def from_settings(cls, settings: DetectorSettings, weights_sha256: str) -> "CacheKey":
        return cls(
            weights_sha256=weights_sha256,
            input_height=settings.input_height,
            input_width=settings.input_width,
            precision=settings.precision,
            nms_threshold=settings.nms_threshold,
            confidence_floor=settings.confidence_floor,
        )

    def canonical(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()[:16]


def _entry_path(cache_dir: Path, key: CacheKey, label: str) -> Path:
    if not _LABEL_PATTERN.match(label):
        raise ValueError(f"unsafe cache label {label!r}: use letters, digits, '.', '_' or '-'")
    return cache_dir / f"{label}-{key.digest()}.npz"


def _load(path: Path, key: CacheKey, frame_count: int) -> tuple[np.ndarray, ...] | None:
    try:
        with np.load(path, allow_pickle=False) as entry:
            if str(entry["key"]) != key.canonical():
                return None
            detections = entry["detections"]
            counts = entry["counts"]
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None
    valid = (
        detections.dtype == np.float64
        and detections.ndim == 2
        and detections.shape[1] == _DETECTION_COLUMNS
        and counts.ndim == 1
        and len(counts) == frame_count
        and int(counts.sum()) == len(detections)
    )
    if not valid:
        return None
    boundaries = np.cumsum(counts)[:-1]
    return tuple(np.array(part) for part in np.split(detections, boundaries))


def _store(path: Path, key: CacheKey, detections: tuple[np.ndarray, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stacked = np.concatenate([np.asarray(frame, dtype=np.float64).reshape(-1, _DETECTION_COLUMNS) for frame in detections])
    counts = np.array([len(frame) for frame in detections], dtype=np.int64)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            np.savez(handle, detections=stacked, counts=counts, key=np.array(key.canonical()))
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def detect_with_cache(
    cache_dir: Path, key: CacheKey, label: str, frames_dir: Path, detect_frame: DetectFrame
) -> tuple[np.ndarray, ...]:
    path = _entry_path(cache_dir, key, label)
    frame_count = len(list_frames(frames_dir))
    cached = _load(path, key, frame_count)
    if cached is not None:
        return cached
    detections = tuple(
        np.asarray(frame, dtype=np.float64) for frame in detect_sequence(frames_dir, detect_frame)
    )
    _store(path, key, detections)
    return detections
