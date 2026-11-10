import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobStatus, new_job
from jobs.settings import Settings
from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256
from worker.celery_app import TASK_NAME, create_celery, make_enqueue, register_task
from worker.processing import WorkerContext


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


def test_the_broker_is_the_configured_redis(tmp_path: Path) -> None:
    app = create_celery(_settings(tmp_path))
    assert app.conf.broker_url == "redis://cache.example:6380/2"


def test_work_is_acknowledged_late_and_lost_workers_are_redelivered(tmp_path: Path) -> None:
    conf = create_celery(_settings(tmp_path)).conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True


def test_a_worker_takes_one_job_at_a_time_in_a_single_process(tmp_path: Path) -> None:
    conf = create_celery(_settings(tmp_path)).conf
    assert conf.worker_prefetch_multiplier == 1
    assert conf.worker_concurrency == 1
    assert conf.worker_pool == "solo"


def test_redelivery_waits_exactly_as_long_as_the_lock_lasts(tmp_path: Path) -> None:
    conf = create_celery(_settings(tmp_path)).conf
    assert conf.broker_transport_options["visibility_timeout"] == 900


def test_job_state_lives_in_our_store_not_in_celery_results(tmp_path: Path) -> None:
    conf = create_celery(_settings(tmp_path)).conf
    assert conf.task_ignore_result is True
    assert not conf.result_backend


def test_only_json_is_accepted_so_a_poisoned_message_cannot_run_code(tmp_path: Path) -> None:
    conf = create_celery(_settings(tmp_path)).conf
    assert list(conf.accept_content) == ["json"]
    assert conf.task_serializer == "json"


def test_enqueueing_sends_the_job_id_to_the_processing_task() -> None:
    sent: list[tuple[str, list[str]]] = []

    class RecordingApp:
        def send_task(self, name: str, args: list[str]) -> None:
            sent.append((name, args))

    make_enqueue(RecordingApp())("job-1")  # type: ignore[arg-type]
    assert sent == [(TASK_NAME, ["job-1"])]


def _context(tmp_path: Path, detector, store: InMemoryJobStore) -> WorkerContext:  # type: ignore[no-untyped-def]
    metadata = RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, "fp16")
    return WorkerContext(store, StorageLayout(tmp_path / "storage"), detector, metadata, "worker-a", 900, 600)


def _queue_job(store: InMemoryJobStore, layout: StorageLayout, job_id: str) -> None:
    frames = layout.frames_dir(job_id)
    frames.mkdir(parents=True)
    for number in range(1, 4):
        cv2.imwrite(str(frames / f"{number:06d}.jpg"), np.full((32, 48, 3), 90, np.uint8))
    store.create(new_job(job_id, now=1.0))


def test_the_registered_task_processes_a_job_with_the_shared_context(tmp_path: Path) -> None:
    store = InMemoryJobStore()
    calls = {"count": 0}

    def detector(image: np.ndarray) -> np.ndarray:
        calls["count"] += 1
        return np.array([[10.0, 10.0, 50.0, 90.0, 0.9]])

    context = _context(tmp_path, detector, store)
    _queue_job(store, context.layout, "job-1")
    app = create_celery(_settings(tmp_path))
    register_task(app, context)
    assert TASK_NAME in app.tasks
    app.tasks[TASK_NAME].apply(args=["job-1"]).get()
    record = store.get("job-1")
    assert record is not None and record.status is JobStatus.SUCCEEDED
    assert calls["count"] == 3


def test_two_jobs_in_a_row_use_the_same_detector_object(tmp_path: Path) -> None:
    store = InMemoryJobStore()
    seen: set[int] = set()

    def detector(image: np.ndarray) -> np.ndarray:
        seen.add(id(detector))
        return np.zeros((0, 5))

    context = _context(tmp_path, detector, store)
    for job_id in ("job-1", "job-2"):
        _queue_job(store, context.layout, job_id)
    app = create_celery(_settings(tmp_path))
    register_task(app, context)
    for job_id in ("job-1", "job-2"):
        app.tasks[TASK_NAME].apply(args=[job_id]).get()
    assert len(seen) == 1
    for job_id in ("job-1", "job-2"):
        record = store.get(job_id)
        assert record is not None and record.status is JobStatus.SUCCEEDED


def test_a_failing_job_leaves_the_task_successful_so_the_message_is_acknowledged(tmp_path: Path) -> None:
    store = InMemoryJobStore()

    def exploding_detector(image: np.ndarray) -> np.ndarray:
        raise RuntimeError("CUDA out of memory")

    context = _context(tmp_path, exploding_detector, store)
    _queue_job(store, context.layout, "job-1")
    app = create_celery(_settings(tmp_path))
    register_task(app, context)
    result = app.tasks[TASK_NAME].apply(args=["job-1"])
    assert result.successful()
    record = store.get("job-1")
    assert record is not None and record.status is JobStatus.FAILED


def test_the_celery_module_does_not_pull_in_torch() -> None:
    code = "import sys; import worker.celery_app; sys.exit(1 if 'torch' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
