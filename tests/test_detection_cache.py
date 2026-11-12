import dataclasses
from pathlib import Path

import cv2
import numpy as np
import pytest

from eval.detection_cache import CacheKey, detect_with_cache
from tracking.detector import DetectorSettings

FRAME_COUNT = 4
SHA = "a" * 64


def _frames(folder: Path, count: int = FRAME_COUNT) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(1, count + 1):
        cv2.imwrite(str(folder / f"{number:06d}.jpg"), np.full((32, 48, 3), 100, np.uint8))
    return folder


class CountingDetector:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, image: np.ndarray) -> np.ndarray:
        self.calls += 1
        frame_index = self.calls
        if frame_index % 3 == 0:
            return np.empty((0, 5), dtype=np.float64)
        return np.array([[1.5, 2.5, 10.25 + frame_index, 20.0, 0.123456789012345]], dtype=np.float64)


def _key(**changes: object) -> CacheKey:
    base = CacheKey.from_settings(DetectorSettings(), SHA)
    return dataclasses.replace(base, **changes)


def test_key_is_built_from_the_detector_settings_and_weights_hash() -> None:
    settings = DetectorSettings(input_height=640, input_width=1152, precision="fp32", nms_threshold=0.5)
    key = CacheKey.from_settings(settings, SHA)
    assert (key.weights_sha256, key.input_height, key.input_width) == (SHA, 640, 1152)
    assert (key.precision, key.nms_threshold, key.confidence_floor) == ("fp32", 0.5, 0.1)


def test_second_call_with_the_same_key_hits_and_runs_no_detection(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    first_detector, second_detector = CountingDetector(), CountingDetector()
    first = detect_with_cache(cache, _key(), "SEQ-tuning", frames, first_detector)
    second = detect_with_cache(cache, _key(), "SEQ-tuning", frames, second_detector)
    assert first_detector.calls == FRAME_COUNT
    assert second_detector.calls == 0
    assert len(second) == FRAME_COUNT
    for before, after in zip(first, second, strict=False):
        assert before.dtype == after.dtype == np.float64
        assert np.array_equal(before, after)


def test_cached_detections_are_bit_exact_including_empty_frames(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    first = detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    second = detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    assert first[2].shape == (0, 5) and second[2].shape == (0, 5)
    assert second[0][0, 4] == 0.123456789012345


@pytest.mark.parametrize(
    "changes",
    [
        {"weights_sha256": "b" * 64},
        {"input_height": 640},
        {"input_width": 1152},
        {"precision": "fp32"},
        {"nms_threshold": 0.5},
        {"confidence_floor": 0.2},
    ],
    ids=lambda changes: next(iter(changes)),
)
def test_any_changed_key_value_misses_and_keeps_both_entries(tmp_path: Path, changes: dict[str, object]) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    other = CountingDetector()
    detect_with_cache(cache, _key(**changes), "SEQ-tuning", frames, other)
    assert other.calls == FRAME_COUNT
    assert len(list(cache.glob("*.npz"))) == 2
    again = CountingDetector()
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, again)
    assert again.calls == 0


def test_a_different_label_is_a_separate_entry(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    other = CountingDetector()
    detect_with_cache(cache, _key(), "SEQ-validation", frames, other)
    assert other.calls == FRAME_COUNT


def test_a_different_frame_count_misses(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    detect_with_cache(cache, _key(), "SEQ-tuning", _frames(tmp_path / "a", 4), CountingDetector())
    longer = CountingDetector()
    result = detect_with_cache(cache, _key(), "SEQ-tuning", _frames(tmp_path / "b", 6), longer)
    assert longer.calls == 6 and len(result) == 6


def test_a_corrupt_entry_is_recomputed_and_replaced(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    (entry,) = cache.glob("*.npz")
    entry.write_bytes(b"not an npz file")
    detector = CountingDetector()
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, detector)
    assert detector.calls == FRAME_COUNT
    healed = CountingDetector()
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, healed)
    assert healed.calls == 0


def test_an_entry_stored_under_the_wrong_name_is_not_trusted(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    detect_with_cache(cache, _key(precision="fp32"), "SEQ-tuning", frames, CountingDetector())
    (fp32_entry,) = cache.glob("*.npz")
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    fp16_entry = next(path for path in cache.glob("*.npz") if path != fp32_entry)
    fp16_entry.write_bytes(fp32_entry.read_bytes())
    detector = CountingDetector()
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, detector)
    assert detector.calls == FRAME_COUNT


def test_a_failed_detection_leaves_no_entry_and_no_temporary_file(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"

    def failing(image: np.ndarray) -> np.ndarray:
        raise RuntimeError("detector exploded")

    with pytest.raises(RuntimeError, match="exploded"):
        detect_with_cache(cache, _key(), "SEQ-tuning", frames, failing)
    assert not cache.exists() or list(cache.iterdir()) == []


def test_no_temporary_files_remain_after_a_successful_store(tmp_path: Path) -> None:
    frames = _frames(tmp_path / "frames")
    cache = tmp_path / "cache"
    detect_with_cache(cache, _key(), "SEQ-tuning", frames, CountingDetector())
    assert [path.suffix for path in cache.iterdir()] == [".npz"]


@pytest.mark.parametrize("label", ["", "a/b", "../x", "a b", ".hidden"])
def test_unsafe_labels_are_rejected(tmp_path: Path, label: str) -> None:
    frames = _frames(tmp_path / "frames")
    with pytest.raises(ValueError, match="label"):
        detect_with_cache(tmp_path / "cache", _key(), label, frames, CountingDetector())
