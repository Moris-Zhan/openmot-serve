from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from eval import latency
from eval.latency import LatencyError, load_frames, measure_latency, validation_frame_paths
from tracking.detector import PreparedFrame, StagedDetector


class Recorder:
    def __init__(self, durations_seconds: list[float]) -> None:
        self.events: list[str] = []
        self.images: list[np.ndarray] = []
        self._ticks = self._tick_values(durations_seconds)

    @staticmethod
    def _tick_values(durations: list[float]) -> Iterator[float]:
        now = 100.0
        for duration in durations:
            yield now
            yield now + duration
            now += duration + 1.0

    def clock(self) -> float:
        self.events.append("clock")
        return next(self._ticks)

    def staged(self) -> StagedDetector:
        def prepare(image: np.ndarray) -> PreparedFrame:
            self.events.append("prepare")
            self.images.append(image)
            return PreparedFrame(torch.zeros(1), 1.0)

        def infer(prepared: PreparedFrame) -> torch.Tensor:
            self.events.append("infer")
            return torch.zeros(1)

        def finish(raw: torch.Tensor, prepared: PreparedFrame) -> np.ndarray:
            self.events.append("finish")
            return np.empty((0, 5))

        return StagedDetector(prepare, infer, finish, lambda: self.events.append("sync"))


def _frames(count: int) -> list[np.ndarray]:
    return [np.full((4, 4, 3), index, np.uint8) for index in range(count)]


def test_warmup_frames_are_run_but_never_timed() -> None:
    recorder = Recorder([0.010, 0.020, 0.030, 0.001, 0.002, 0.003])
    result = measure_latency(recorder.staged(), _frames(5), warmup=2, measured=3, clock=recorder.clock)
    assert recorder.events[:7] == ["prepare", "infer", "finish"] * 2 + ["sync"]
    assert result.warmup_frames == 2 and result.measured_frames == 3
    assert result.end_to_end.median_ms == pytest.approx(20.0)


def test_statistics_follow_numpy_percentile_and_throughput_uses_the_latency_total() -> None:
    durations = [0.010, 0.020, 0.030, 0.040]
    recorder = Recorder(durations + [0.001] * 4)
    result = measure_latency(recorder.staged(), _frames(4), warmup=1, measured=4, clock=recorder.clock)
    milliseconds = np.array(durations) * 1000
    assert result.end_to_end.median_ms == pytest.approx(float(np.median(milliseconds)))
    assert result.end_to_end.p95_ms == pytest.approx(float(np.percentile(milliseconds, 95)))
    assert result.throughput_fps == pytest.approx(4 / sum(durations))


def test_the_inference_layer_has_its_own_statistics() -> None:
    recorder = Recorder([0.010] * 3 + [0.002, 0.004, 0.006])
    result = measure_latency(recorder.staged(), _frames(3), warmup=1, measured=3, clock=recorder.clock)
    assert result.inference.median_ms == pytest.approx(4.0)
    assert result.end_to_end.median_ms == pytest.approx(10.0)


def test_the_end_to_end_pass_synchronises_around_the_whole_pipeline() -> None:
    recorder = Recorder([0.010] * 2 + [0.001] * 2)
    measure_latency(recorder.staged(), _frames(2), warmup=1, measured=2, clock=recorder.clock)
    timed = recorder.events[recorder.events.index("sync") + 1 :]
    one_frame = ["sync", "clock", "prepare", "infer", "finish", "sync", "clock"]
    assert timed[: len(one_frame) * 2] == one_frame * 2


def test_the_inference_pass_times_only_infer_and_prepares_outside_the_timer() -> None:
    recorder = Recorder([0.010] * 2 + [0.001] * 2)
    measure_latency(recorder.staged(), _frames(2), warmup=1, measured=2, clock=recorder.clock)
    inference_events = recorder.events[len(recorder.events) - 2 * 6 :]
    one_frame = ["prepare", "sync", "clock", "infer", "sync", "clock"]
    assert inference_events == one_frame * 2


def test_frames_are_used_in_order_and_cycle_when_there_are_too_few() -> None:
    recorder = Recorder([0.001] * 5 + [0.001] * 5)
    frames = _frames(3)
    measure_latency(recorder.staged(), frames, warmup=2, measured=5, clock=recorder.clock)
    used = [int(image[0, 0, 0]) for image in recorder.images]
    assert used[:2] == [0, 1]
    assert used[2:7] == [0, 1, 2, 0, 1]


def test_measuring_without_frames_is_an_error() -> None:
    with pytest.raises(LatencyError, match="no frames"):
        measure_latency(Recorder([]).staged(), [], warmup=1, measured=1)


def _write_sequence(root: Path, name: str, length: int) -> None:
    folder = root / "train" / name / "img1"
    folder.mkdir(parents=True)
    for number in range(1, length + 1):
        cv2.imwrite(str(folder / f"{number:06d}.jpg"), np.full((8, 8, 3), number, np.uint8))


def test_frame_paths_come_from_each_validation_half_in_sequence_order(tmp_path: Path) -> None:
    _write_sequence(tmp_path, "SEQ-A", 4)
    _write_sequence(tmp_path, "SEQ-B", 6)
    paths = validation_frame_paths(tmp_path, ("SEQ-A", "SEQ-B"), 5)
    assert [(path.parent.parent.name, path.name) for path in paths] == [
        ("SEQ-A", "000003.jpg"),
        ("SEQ-A", "000004.jpg"),
        ("SEQ-B", "000004.jpg"),
        ("SEQ-B", "000005.jpg"),
        ("SEQ-B", "000006.jpg"),
    ]


def test_frame_paths_cycle_when_more_are_requested_than_exist(tmp_path: Path) -> None:
    _write_sequence(tmp_path, "SEQ-A", 4)
    paths = validation_frame_paths(tmp_path, ("SEQ-A",), 5)
    assert [path.name for path in paths] == ["000003.jpg", "000004.jpg"] * 2 + ["000003.jpg"]


def test_each_distinct_file_is_read_once_even_when_cycling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sequence(tmp_path, "SEQ-A", 4)
    paths = validation_frame_paths(tmp_path, ("SEQ-A",), 6)
    reads: list[str] = []
    original = cv2.imread
    monkeypatch.setattr(latency.cv2, "imread", lambda path: reads.append(path) or original(path))
    frames = load_frames(paths)
    assert len(frames) == 6 and len(reads) == 2
    assert frames[0] is frames[2]


def test_an_unreadable_frame_names_the_file(tmp_path: Path) -> None:
    broken = tmp_path / "000001.jpg"
    broken.write_bytes(b"not an image")
    with pytest.raises(LatencyError, match="000001.jpg"):
        load_frames((broken,))
