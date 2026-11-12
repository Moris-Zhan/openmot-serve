from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest
from prometheus_client import CollectorRegistry

from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobStatus, new_job
from jobs.settings import Settings
from metrics.definitions import create_metrics
from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256
from worker.celery_app import TASK_NAME, create_celery, register_task
from worker.processing import WorkerContext

FRAME_COUNT = 3


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_dir=tmp_path / "storage",
        redis_url="redis://cache.example:6380/2",
        weights=Path("models/yolox_x.pth"),
        precision="fp16",
        max_upload_bytes=1000,
        max_unpacked_bytes=2000,
        lock_timeout_seconds=900,
        task_time_limit_seconds=600,
    )


def _working_detector(image: np.ndarray) -> np.ndarray:
    return np.array([[10.0, 10.0, 50.0, 90.0, 0.9]])


def _exploding_detector(image: np.ndarray) -> np.ndarray:
    raise RuntimeError("CUDA out of memory")


def _queue_job(store: InMemoryJobStore, layout: StorageLayout, job_id: str, with_frames: bool = True) -> None:
    if with_frames:
        frames = layout.frames_dir(job_id)
        frames.mkdir(parents=True)
        for number in range(1, FRAME_COUNT + 1):
            cv2.imwrite(str(frames / f"{number:06d}.jpg"), np.full((32, 48, 3), 90, np.uint8))
    store.create(new_job(job_id, now=1.0))


class Instrumented:
    def __init__(self, tmp_path: Path, detector: Callable[[np.ndarray], np.ndarray], ticks: list[float]) -> None:
        self.store = InMemoryJobStore()
        self.registry = CollectorRegistry()
        self.layout = StorageLayout(tmp_path / "storage")
        remaining = iter(ticks)
        metadata = RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, "fp16")
        context = WorkerContext(self.store, self.layout, detector, metadata, "worker-a", 900, 600)
        self.app = create_celery(_settings(tmp_path))
        register_task(self.app, context, metrics=create_metrics(self.registry), monotonic=lambda: next(remaining))

    def run(self, job_id: str) -> None:
        self.app.tasks[TASK_NAME].apply(args=[job_id]).get()

    def value(self, name: str, labels: dict[str, str] | None = None) -> float | None:
        return self.registry.get_sample_value(name, labels or {})


@pytest.fixture
def succeeding(tmp_path: Path) -> Instrumented:
    return Instrumented(tmp_path, _working_detector, [100.0, 107.5])


def test_a_succeeded_job_counts_its_outcome_duration_and_frames(succeeding: Instrumented) -> None:
    _queue_job(succeeding.store, succeeding.layout, "job-1")
    succeeding.run("job-1")
    record = succeeding.store.get("job-1")
    assert record is not None and record.status is JobStatus.SUCCEEDED
    assert succeeding.value("openmot_jobs_total", {"outcome": "succeeded"}) == 1
    assert succeeding.value("openmot_job_duration_seconds_count") == 1
    assert succeeding.value("openmot_job_duration_seconds_sum") == pytest.approx(7.5)
    assert succeeding.value("openmot_frames_processed_total") == FRAME_COUNT


def test_a_failed_job_counts_its_outcome_and_duration_but_no_frames(tmp_path: Path) -> None:
    failing = Instrumented(tmp_path, _exploding_detector, [0.0, 2.0])
    _queue_job(failing.store, failing.layout, "job-1")
    failing.run("job-1")
    assert failing.value("openmot_jobs_total", {"outcome": "failed"}) == 1
    assert failing.value("openmot_job_duration_seconds_sum") == pytest.approx(2.0)
    assert failing.value("openmot_frames_processed_total") == 0


def test_a_job_without_a_frames_folder_fails_without_breaking_the_metrics(tmp_path: Path) -> None:
    broken = Instrumented(tmp_path, _working_detector, [0.0, 1.0])
    _queue_job(broken.store, broken.layout, "job-1", with_frames=False)
    broken.run("job-1")
    assert broken.value("openmot_jobs_total", {"outcome": "failed"}) == 1
    assert broken.value("openmot_frames_processed_total") == 0


def test_a_redelivered_job_is_counted_as_skipped_without_duration_or_frames(tmp_path: Path) -> None:
    redelivered = Instrumented(tmp_path, _working_detector, [0.0, 4.0, 10.0, 10.1])
    _queue_job(redelivered.store, redelivered.layout, "job-1")
    redelivered.run("job-1")
    redelivered.run("job-1")
    assert redelivered.value("openmot_jobs_total", {"outcome": "succeeded"}) == 1
    assert redelivered.value("openmot_jobs_total", {"outcome": "skipped"}) == 1
    assert redelivered.value("openmot_job_duration_seconds_count") == 1
    assert redelivered.value("openmot_frames_processed_total") == FRAME_COUNT


def test_the_task_returns_the_outcome_value_as_before(succeeding: Instrumented) -> None:
    _queue_job(succeeding.store, succeeding.layout, "job-1")
    assert succeeding.app.tasks[TASK_NAME].apply(args=["job-1"]).get() == "succeeded"
