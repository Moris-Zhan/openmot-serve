import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.split import split_sequence
from tracking.sequence import SequenceError, list_frames

CALIBRATION_SEQUENCES = tuple(f"MOT17-{number}-FRCNN" for number in ("02", "04", "05", "09", "10", "11", "13"))
DEFAULT_CALIBRATION_FRAMES = 512
CALIBRATION_SOURCE = "MOT17 train FRCNN Tuning Half"
DEFAULT_DATA_ROOT = Path("data/MOT17")


class CalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalibrationSelection:
    frames: tuple[Path, ...]
    sha256: str


def _tuning_pool(data_root: Path, sequences: Sequence[str]) -> list[Path]:
    pool: list[Path] = []
    for name in sorted(sequences):
        try:
            frames = list_frames(data_root / "train" / name / "img1")
        except SequenceError as error:
            raise CalibrationError(f"calibration data is missing for {name}: {error}") from error
        tuning, _ = split_sequence(len(frames))
        pool.extend(frames[tuning.first - 1 : tuning.last])
    return pool


def _identity(data_root: Path, path: Path) -> str:
    return path.relative_to(data_root / "train").as_posix()


def select_calibration_frames(
    data_root: Path,
    count: int = DEFAULT_CALIBRATION_FRAMES,
    sequences: Sequence[str] = CALIBRATION_SEQUENCES,
) -> CalibrationSelection:
    if count < 1:
        raise CalibrationError(f"the calibration frame count must be at least 1, got {count}")
    pool = _tuning_pool(data_root, sequences)
    if len(pool) < count:
        raise CalibrationError(
            f"only {len(pool)} Tuning Half frames are available under {data_root}, {count} were requested"
        )
    frames = tuple(pool[index * len(pool) // count] for index in range(count))
    listing = "\n".join(_identity(data_root, path) for path in frames)
    return CalibrationSelection(frames, hashlib.sha256(listing.encode()).hexdigest())
