import signal
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobStatus, new_job
from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata, metadata_path_for
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256
from worker.processing import ProcessOutcome, WorkerContext, process_job

FRAME_COUNT = 6
OWNER = "worker-a"
LOCK_SECONDS = 60


class FakeDetector:
    def __init__(self, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at
        self.statuses_seen: list[JobStatus] = []
        self.watch: tuple[InMemoryJobStore, str] | None = None

    def __call__(self, image: np.ndarray) -> np.ndarray:
        self.calls += 1
        if self.watch is not None:
            record = self.watch[0].get(self.watch[1])
            assert record is not None
            self.statuses_seen.append(record.status)
        if self.fail_at is not None and self.calls == self.fail_at:
            raise RuntimeError("CUDA out of memory")
        left = 100.0 + 3.0 * self.calls
        return np.array([[left, 200.0, left + 40.0, 300.0, 0.9]])


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> InMemoryJobStore:
    return InMemoryJobStore(clock=clock)


@pytest.fixture
def layout(tmp_path: Path) -> StorageLayout:
    return StorageLayout(tmp_path / "storage")


def _context(store: InMemoryJobStore, layout: StorageLayout, detector, owner: str = OWNER, time_limit_seconds: int | None = None) -> WorkerContext:  # type: ignore[no-untyped-def]
    metadata = RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, "fp16")
    return WorkerContext(store, layout, detector, metadata, owner, LOCK_SECONDS, time_limit_seconds)


def _queue_job(store: InMemoryJobStore, layout: StorageLayout, job_id: str = "job-1") -> None:
    frames = layout.frames_dir(job_id)
    frames.mkdir(parents=True)
    for number in range(1, FRAME_COUNT + 1):
        cv2.imwrite(str(frames / f"{number:06d}.jpg"), np.full((32, 48, 3), 90, np.uint8))
    store.create(new_job(job_id, now=10.0))


def _status(store: InMemoryJobStore, job_id: str = "job-1") -> JobStatus:
    record = store.get(job_id)
    assert record is not None
    return record.status


def _reason(store: InMemoryJobStore, job_id: str = "job-1") -> str:
    record = store.get(job_id)
    assert record is not None and record.reason is not None
    return record.reason


def _assert_no_result(layout: StorageLayout, job_id: str = "job-1") -> None:
    assert not layout.result_path(job_id).exists()
    assert not metadata_path_for(layout.result_path(job_id)).exists()


def test_a_queued_job_runs_to_succeeded_and_writes_its_result(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    detector = FakeDetector()
    detector.watch = (store, "job-1")
    outcome = process_job(_context(store, layout, detector), "job-1", clock)
    assert outcome is ProcessOutcome.SUCCEEDED
    assert _status(store) is JobStatus.SUCCEEDED
    assert detector.statuses_seen and set(detector.statuses_seen) == {JobStatus.RUNNING}
    lines = layout.result_path("job-1").read_text().splitlines()
    assert lines and all(len(line.split(",")) == 10 for line in lines)


def test_a_corrupt_frame_fails_the_job_and_names_it(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    (layout.frames_dir("job-1") / "000004.jpg").write_bytes(b"this is not a jpeg")
    outcome = process_job(_context(store, layout, FakeDetector()), "job-1", clock)
    assert outcome is ProcessOutcome.FAILED
    assert _status(store) is JobStatus.FAILED
    assert "000004.jpg" in _reason(store)
    _assert_no_result(layout)


def test_a_detector_error_fails_the_job_with_its_message(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    outcome = process_job(_context(store, layout, FakeDetector(fail_at=3)), "job-1", clock)
    assert outcome is ProcessOutcome.FAILED
    assert "CUDA out of memory" in _reason(store)
    _assert_no_result(layout)


def test_a_storage_failure_fails_the_job_and_leaves_no_result(store, layout, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)

    def broken_replace(source: object, destination: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("tracking.files.os.replace", broken_replace)
    outcome = process_job(_context(store, layout, FakeDetector()), "job-1", clock)
    assert outcome is ProcessOutcome.FAILED
    assert "no space left" in _reason(store)
    _assert_no_result(layout)
    assert sorted(path.name for path in layout.job_dir("job-1").iterdir()) == ["frames"]


def test_missing_frames_fail_the_job_instead_of_crashing_the_worker(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    store.create(new_job("job-1", now=10.0))
    outcome = process_job(_context(store, layout, FakeDetector()), "job-1", clock)
    assert outcome is ProcessOutcome.FAILED
    assert _status(store) is JobStatus.FAILED and _reason(store)


def test_a_job_delivered_twice_is_processed_once(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    detector = FakeDetector()
    context = _context(store, layout, detector)
    assert process_job(context, "job-1", clock) is ProcessOutcome.SUCCEEDED
    result = layout.result_path("job-1").read_text()
    calls = detector.calls
    assert process_job(context, "job-1", clock) is ProcessOutcome.SKIPPED
    assert detector.calls == calls
    assert layout.result_path("job-1").read_text() == result


def test_a_job_another_worker_is_processing_is_left_alone(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    assert store.acquire_lock("job-1", "worker-b", LOCK_SECONDS)
    detector = FakeDetector()
    assert process_job(_context(store, layout, detector), "job-1", clock) is ProcessOutcome.SKIPPED
    assert detector.calls == 0
    assert _status(store) is JobStatus.QUEUED
    _assert_no_result(layout)


def test_a_finished_job_is_never_overwritten(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    process_job(_context(store, layout, FakeDetector()), "job-1", clock)
    before = (store.get("job-1"), layout.result_path("job-1").read_text())
    assert process_job(_context(store, layout, FakeDetector(fail_at=1)), "job-1", clock) is ProcessOutcome.SKIPPED
    assert (store.get("job-1"), layout.result_path("job-1").read_text()) == before


def test_a_failed_job_stays_failed_when_delivered_again(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    process_job(_context(store, layout, FakeDetector(fail_at=1)), "job-1", clock)
    before = store.get("job-1")
    assert process_job(_context(store, layout, FakeDetector()), "job-1", clock) is ProcessOutcome.SKIPPED
    assert store.get("job-1") == before
    _assert_no_result(layout)


def test_an_unknown_job_is_skipped(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    assert process_job(_context(store, layout, FakeDetector()), "never-created", clock) is ProcessOutcome.SKIPPED


def test_a_job_abandoned_by_a_dead_worker_is_taken_over_and_finished(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    store.transition("job-1", JobStatus.RUNNING, now=20.0)
    assert store.acquire_lock("job-1", "dead-worker", 5)
    clock.now += 6
    outcome = process_job(_context(store, layout, FakeDetector()), "job-1", clock)
    assert outcome is ProcessOutcome.SUCCEEDED
    assert _status(store) is JobStatus.SUCCEEDED
    assert layout.result_path("job-1").exists()


def test_a_running_job_with_a_live_lock_is_not_taken_over(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    store.transition("job-1", JobStatus.RUNNING, now=20.0)
    assert store.acquire_lock("job-1", "live-worker", 600)
    detector = FakeDetector()
    assert process_job(_context(store, layout, detector), "job-1", clock) is ProcessOutcome.SKIPPED
    assert detector.calls == 0


def test_a_failing_job_does_not_stop_the_next_one(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout, "job-1")
    _queue_job(store, layout, "job-2")
    context = _context(store, layout, FakeDetector(fail_at=2))
    assert process_job(context, "job-1", clock) is ProcessOutcome.FAILED
    assert process_job(context, "job-2", clock) is ProcessOutcome.SUCCEEDED
    assert _status(store, "job-2") is JobStatus.SUCCEEDED


@pytest.mark.parametrize("fail_at", [None, 2])
def test_the_lock_is_released_whatever_the_outcome(store, layout, clock, fail_at) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    process_job(_context(store, layout, FakeDetector(fail_at=fail_at)), "job-1", clock)
    assert store.acquire_lock("job-1", "someone-else", LOCK_SECONDS) is True


def test_an_error_with_an_empty_message_still_gets_a_reason(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)

    def silent_failure(image: np.ndarray) -> np.ndarray:
        raise RuntimeError()

    assert process_job(_context(store, layout, silent_failure), "job-1", clock) is ProcessOutcome.FAILED
    assert "RuntimeError" in _reason(store)


def test_a_failed_retry_does_not_leave_the_result_of_a_dead_attempt(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    store.transition("job-1", JobStatus.RUNNING, now=20.0)
    assert store.acquire_lock("job-1", "dead-worker", 5)
    layout.result_path("job-1").write_text("1,1,0,0,1,1,1,-1,-1,-1\n")
    metadata_path_for(layout.result_path("job-1")).write_text("{}")
    clock.now += 6
    outcome = process_job(_context(store, layout, FakeDetector(fail_at=1)), "job-1", clock)
    assert outcome is ProcessOutcome.FAILED
    _assert_no_result(layout)


def test_a_job_over_its_time_limit_is_failed_with_a_reason(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)

    def slow_detector(image: np.ndarray) -> np.ndarray:
        time.sleep(0.6)
        return np.zeros((0, 5))

    outcome = process_job(_context(store, layout, slow_detector, time_limit_seconds=1), "job-1", clock)
    assert outcome is ProcessOutcome.FAILED
    assert _status(store) is JobStatus.FAILED
    assert "time limit of 1 seconds" in _reason(store)
    _assert_no_result(layout)
    assert store.acquire_lock("job-1", "someone-else", LOCK_SECONDS) is True


def test_a_job_within_its_time_limit_is_unaffected(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    outcome = process_job(_context(store, layout, FakeDetector(), time_limit_seconds=30), "job-1", clock)
    assert outcome is ProcessOutcome.SUCCEEDED


@pytest.mark.parametrize("fail_at", [None, 2])
def test_the_alarm_is_always_cleared_and_the_old_handler_restored(store, layout, clock, fail_at) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    before = signal.getsignal(signal.SIGALRM)
    process_job(_context(store, layout, FakeDetector(fail_at=fail_at), time_limit_seconds=30), "job-1", clock)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == before


def test_without_a_time_limit_no_alarm_is_armed(store, layout, clock) -> None:  # type: ignore[no-untyped-def]
    _queue_job(store, layout)
    armed: list[tuple[float, float]] = []

    def watching_detector(image: np.ndarray) -> np.ndarray:
        armed.append(signal.getitimer(signal.ITIMER_REAL))
        return np.zeros((0, 5))

    process_job(_context(store, layout, watching_detector), "job-1", clock)
    assert armed and all(timer == (0.0, 0.0) for timer in armed)
