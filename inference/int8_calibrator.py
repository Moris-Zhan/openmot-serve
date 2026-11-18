import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from inference.calibration import CalibrationError, CalibrationSelection

CALIBRATOR_NAME = "IInt8EntropyCalibrator2"


def calibration_cache_sha256(cache_path: Path) -> str:
    if not cache_path.is_file():
        raise CalibrationError(f"the calibration cache was not written: {cache_path}")
    return hashlib.sha256(cache_path.read_bytes()).hexdigest()


def create_entropy_calibrator(
    runtime: Any,
    selection: CalibrationSelection,
    prepare: Callable[[np.ndarray], Any],
    cache_path: Path,
    read_image: Callable[[Path], np.ndarray | None] = lambda path: cv2.imread(str(path)),
) -> Any:
    class EntropyCalibrator(runtime.IInt8EntropyCalibrator2):
        def __init__(self) -> None:
            super().__init__()
            self.error: CalibrationError | None = None
            self._next = 0
            self._current: Any = None

        def get_batch_size(self) -> int:
            return 1

        def get_batch(self, names: list[str]) -> list[int] | None:
            if self.error is not None or self._next >= len(selection.frames):
                return None
            if len(names) != 1:
                self.error = CalibrationError(f"expected one network input, TensorRT asked for {len(names)}")
                return None
            path = selection.frames[self._next]
            image = read_image(path)
            if image is None:
                self.error = CalibrationError(f"cannot read calibration frame {path}")
                return None
            self._next += 1
            self._current = prepare(image).batch.contiguous()
            return [int(self._current.data_ptr())]

        def read_calibration_cache(self) -> bytes | None:
            return cache_path.read_bytes() if cache_path.is_file() else None

        def write_calibration_cache(self, cache: Any) -> None:
            cache_path.write_bytes(bytes(cache))

    return EntropyCalibrator()
