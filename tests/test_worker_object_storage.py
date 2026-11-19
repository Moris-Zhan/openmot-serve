import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from prometheus_client import CollectorRegistry

from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.object_store import S3ObjectStore
from jobs.record import JobStatus, new_job
from jobs.settings import load_settings
from metrics.definitions import create_metrics
from tests.test_object_store import BUCKET, FakeClientError, FakeS3Client
from tests.test_worker_metrics import FRAME_COUNT as METRIC_FRAMES
from tests.test_worker_metrics import _settings
from tests.test_worker_processing import FRAME_COUNT, LOCK_SECONDS, OWNER, FakeDetector
from tests.test_worker_startup import PASSING, Recorder
from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata, metadata_path_for
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256
from worker.celery_app import TASK_NAME, create_celery, register_task
from worker.processing import ProcessOutcome, WorkerContext, process_job
from worker.startup import StartupDependencies, start_worker

JOB = "job-1"


class EventStore(InMemoryJobStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def transition(self, job_id, target, now, reason=None):  # type: ignore[no-untyped-def]
        self.events.append(f"status:{target.value}")
        return super().transition(job_id, target, now, reason)


class EventObjects(S3ObjectStore):
    def __init__(self, client: FakeS3Client, events: list[str]) -> None:
        super().__init__(client, BUCKET)
        self.events = events

    def upload_file(self, key: str, path: Path) -> None:
        self.events.append(f"upload:{key.rsplit('/', 1)[-1]}")
        super().upload_file(key, path)


class FlakyDownloads(FakeS3Client):
    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
        self.downloads = 0

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        self.downloads += 1
        if self.downloads == self.fail_on_call:
            raise FakeClientError("InternalError", "the store dropped the connection")
        super().download_file(Bucket, Key, Filename)


class SlowDownloads(FakeS3Client):
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        time.sleep(3)
        super().download_file(Bucket, Key, Filename)


def _frame_bytes() -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.full((32, 48, 3), 90, np.uint8))
    assert ok
    return encoded.tobytes()


def _put_frames(client: FakeS3Client, job_id: str = JOB, count: int = FRAME_COUNT) -> None:
    for number in range(1, count + 1):
        client.objects[f"jobs/{job_id}/frames/{number:06d}.jpg"] = _frame_bytes()


def _context(
    store: InMemoryJobStore,
    layout: StorageLayout,
    detector,  # type: ignore[no-untyped-def]
    objects: S3ObjectStore | None,
    time_limit_seconds: int | None = None,
) -> WorkerContext:
    metadata = RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, "fp16")
    return WorkerContext(store, layout, detector, metadata, OWNER, LOCK_SECONDS, time_limit_seconds, objects=objects)


def _status(store: InMemoryJobStore, job_id: str = JOB) -> JobStatus:
    record = store.get(job_id)
    assert record is not None
    return record.status


def _reason(store: InMemoryJobStore, job_id: str = JOB) -> str:
    record = store.get(job_id)
    assert record is not None and record.reason is not None
    return record.reason


def _local_files(layout: StorageLayout) -> list[Path]:
    return [path for path in layout.root.rglob("*") if path.is_file()] if layout.root.exists() else []


@pytest.fixture
def client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def layout(tmp_path: Path) -> StorageLayout:
    return StorageLayout(tmp_path / "scratch")


def _run(client: FakeS3Client, layout: StorageLayout, detector=None, **options):  # type: ignore[no-untyped-def]
    store = InMemoryJobStore()
    store.create(new_job(JOB, now=10.0))
    context = _context(store, layout, detector or FakeDetector(), S3ObjectStore(client, BUCKET), **options)
    return store, process_job(context, JOB)


def test_a_job_succeeds_through_the_bucket_and_leaves_no_local_files(
    client: FakeS3Client, layout: StorageLayout
) -> None:
    _put_frames(client)
    store, outcome = _run(client, layout)
    assert outcome is ProcessOutcome.SUCCEEDED and _status(store) is JobStatus.SUCCEEDED
    assert f"jobs/{JOB}/result.txt" in client.objects and f"jobs/{JOB}/result.meta.json" in client.objects
    assert _local_files(layout) == [] and not layout.job_dir(JOB).exists()


def test_the_result_is_uploaded_before_the_job_is_marked_succeeded(client: FakeS3Client, layout: StorageLayout) -> None:
    _put_frames(client)
    events: list[str] = []
    store = EventStore(events)
    store.create(new_job(JOB, now=10.0))
    context = _context(store, layout, FakeDetector(), EventObjects(client, events))
    assert process_job(context, JOB) is ProcessOutcome.SUCCEEDED
    assert events == ["status:running", "upload:result.meta.json", "upload:result.txt", "status:succeeded"]


def test_the_result_and_metadata_equal_the_filesystem_backend(
    client: FakeS3Client, layout: StorageLayout, tmp_path: Path
) -> None:
    _put_frames(client)
    _run(client, layout)
    local = StorageLayout(tmp_path / "filesystem")
    frames = local.frames_dir(JOB)
    frames.mkdir(parents=True)
    for number in range(1, FRAME_COUNT + 1):
        (frames / f"{number:06d}.jpg").write_bytes(_frame_bytes())
    store = InMemoryJobStore()
    store.create(new_job(JOB, now=10.0))
    assert process_job(_context(store, local, FakeDetector(), None), JOB) is ProcessOutcome.SUCCEEDED
    assert client.objects[f"jobs/{JOB}/result.txt"] == local.result_path(JOB).read_bytes()
    assert client.objects[f"jobs/{JOB}/result.meta.json"] == metadata_path_for(local.result_path(JOB)).read_bytes()


def test_a_job_without_frames_in_the_bucket_fails_with_a_reason(client: FakeS3Client, layout: StorageLayout) -> None:
    store, outcome = _run(client, layout)
    assert outcome is ProcessOutcome.FAILED and _status(store) is JobStatus.FAILED
    assert "no frames" in _reason(store)
    assert not [key for key in client.objects if key.endswith("result.txt")]


def test_a_download_failure_fails_the_job_and_the_worker_goes_on_to_the_next_one(layout: StorageLayout) -> None:
    flaky = FlakyDownloads(fail_on_call=2)
    _put_frames(flaky, "job-1")
    _put_frames(flaky, "job-2")
    store = InMemoryJobStore()
    for job_id in ("job-1", "job-2"):
        store.create(new_job(job_id, now=10.0))
    context = _context(store, layout, FakeDetector(), S3ObjectStore(flaky, BUCKET))
    assert process_job(context, "job-1") is ProcessOutcome.FAILED
    assert "FakeClientError" in _reason(store, "job-1")
    assert "jobs/job-1/result.txt" not in flaky.objects
    assert _local_files(layout) == []
    assert process_job(context, "job-2") is ProcessOutcome.SUCCEEDED
    assert "jobs/job-2/result.txt" in flaky.objects


def test_a_tracking_failure_removes_the_scratch_and_leaves_no_result_object(
    client: FakeS3Client, layout: StorageLayout
) -> None:
    _put_frames(client)
    store, outcome = _run(client, layout, FakeDetector(fail_at=3))
    assert outcome is ProcessOutcome.FAILED and "CUDA out of memory" in _reason(store)
    assert _local_files(layout) == [] and not layout.job_dir(JOB).exists()
    assert sorted(client.objects) == [f"jobs/{JOB}/frames/{n:06d}.jpg" for n in range(1, FRAME_COUNT + 1)]


def test_a_failed_result_upload_fails_the_job_and_leaves_no_result_object(
    client: FakeS3Client, layout: StorageLayout
) -> None:
    _put_frames(client)
    client.fail_uploads_after = 0
    store, outcome = _run(client, layout)
    assert outcome is ProcessOutcome.FAILED and "FakeClientError" in _reason(store)
    assert _local_files(layout) == []
    assert not [key for key in client.objects if "result" in key]


def test_a_partial_result_upload_is_removed_when_the_job_fails(client: FakeS3Client, layout: StorageLayout) -> None:
    _put_frames(client)
    client.fail_uploads_after = 1
    store, outcome = _run(client, layout)
    assert outcome is ProcessOutcome.FAILED
    assert not [key for key in client.objects if "result" in key]


def test_leftover_scratch_from_an_interrupted_run_does_not_leak_into_the_result(
    client: FakeS3Client, layout: StorageLayout, tmp_path: Path
) -> None:
    _put_frames(client)
    stray = layout.frames_dir(JOB)
    stray.mkdir(parents=True)
    (stray / "999999.jpg").write_bytes(_frame_bytes())
    (layout.job_dir(JOB) / "result.txt").write_text("stale result\n")
    _, outcome = _run(client, layout)
    assert outcome is ProcessOutcome.SUCCEEDED
    clean = FakeS3Client()
    _put_frames(clean)
    _run(clean, StorageLayout(tmp_path / "other-scratch"))
    assert client.objects[f"jobs/{JOB}/result.txt"] == clean.objects[f"jobs/{JOB}/result.txt"]
    assert _local_files(layout) == []


def test_the_time_limit_covers_the_download(layout: StorageLayout) -> None:
    slow = SlowDownloads()
    _put_frames(slow)
    store, outcome = _run(slow, layout, time_limit_seconds=1)
    assert outcome is ProcessOutcome.FAILED and "time limit" in _reason(store)
    assert _local_files(layout) == []


def test_the_frames_processed_metric_counts_the_frames_in_the_bucket(tmp_path: Path) -> None:
    client = FakeS3Client()
    _put_frames(client, JOB, METRIC_FRAMES)
    store = InMemoryJobStore()
    store.create(new_job(JOB, now=1.0))
    registry = CollectorRegistry()
    context = _context(store, StorageLayout(tmp_path / "scratch"), FakeDetector(), S3ObjectStore(client, BUCKET), 600)
    app = create_celery(_settings(tmp_path))
    ticks = iter([100.0, 101.0])
    register_task(app, context, metrics=create_metrics(registry), monotonic=lambda: next(ticks))
    app.tasks[TASK_NAME].apply(args=[JOB]).get()
    assert _status(store) is JobStatus.SUCCEEDED
    assert registry.get_sample_value("openmot_frames_processed_total") == METRIC_FRAMES


def test_the_s3_backend_starts_the_worker_with_the_object_store_and_a_scratch_folder(tmp_path: Path) -> None:
    recorder = Recorder(PASSING)
    store = S3ObjectStore(FakeS3Client(), BUCKET)
    dependencies = StartupDependencies(
        recorder.check_environment, recorder.load_detect_frame, recorder.make_store, lambda settings: store
    )
    settings = load_settings(
        {
            "OPENMOT_STORAGE_DIR": str(tmp_path / "storage"),
            "OPENMOT_STORAGE_BACKEND": "s3",
            "OPENMOT_S3_BUCKET": BUCKET,
        }
    )
    context = start_worker(settings, dependencies, OWNER)
    assert context.objects is store
    assert context.layout.root == tmp_path / "storage" / "scratch"


def test_the_filesystem_backend_starts_the_worker_without_an_object_store(tmp_path: Path) -> None:
    recorder = Recorder(PASSING)
    settings = load_settings({"OPENMOT_STORAGE_DIR": str(tmp_path / "storage")})
    context = start_worker(settings, recorder.dependencies(), OWNER)
    assert context.objects is None
    assert context.layout.root == tmp_path / "storage"
