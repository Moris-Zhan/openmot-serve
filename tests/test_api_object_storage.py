import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.object_store import S3ObjectStore
from jobs.record import JobRecord, JobStatus, new_job
from tests.test_api_submit import LIMITS, _frames, _submit
from tests.test_object_store import BUCKET, SECRET, FakeS3Client

FRAME_COUNT = 4
RESULT_TEXT = "1,1,10.0,20.0,30.0,40.0,0.9,-1,-1,-1\n2,1,11.0,21.0,30.0,40.0,0.9,-1,-1,-1\n"


class RecordingStore(InMemoryJobStore):
    def __init__(self) -> None:
        super().__init__()
        self.created: list[str] = []

    def create(self, record: JobRecord) -> bool:
        self.created.append(record.job_id)
        return super().create(record)


@dataclass
class Backend:
    store: RecordingStore
    layout: StorageLayout
    client: FakeS3Client
    queued: list[str] = field(default_factory=list)
    enqueue_error: Exception | None = None

    def enqueue(self, job_id: str) -> None:
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.queued.append(job_id)


@pytest.fixture
def backend(tmp_path: Path) -> Backend:
    return Backend(RecordingStore(), StorageLayout(tmp_path / "scratch"), FakeS3Client())


@pytest.fixture
def client(backend: Backend) -> TestClient:
    objects = S3ObjectStore(backend.client, BUCKET)
    app = create_app(backend.store, backend.layout, LIMITS, backend.enqueue, lambda: None, objects=objects)
    return TestClient(app)


def _local_files(backend: Backend) -> list[Path]:
    root = backend.layout.root
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


def _succeeded_job(backend: Backend, job_id: str = "job1") -> str:
    backend.store.create(new_job(job_id, 1.0))
    backend.store.transition(job_id, JobStatus.RUNNING, 2.0)
    backend.store.transition(job_id, JobStatus.SUCCEEDED, 3.0)
    return job_id


def test_the_frames_are_in_the_bucket_and_not_on_the_local_disk(client: TestClient, backend: Backend) -> None:
    response = _submit(client, _frames(FRAME_COUNT))
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    keys = sorted(backend.client.objects)
    assert keys == [f"jobs/{job_id}/frames/{number:06d}.jpg" for number in range(1, FRAME_COUNT + 1)]
    assert _local_files(backend) == []
    assert not backend.layout.job_dir(job_id).exists()
    assert backend.queued == [job_id]


def test_the_uploaded_objects_hold_the_submitted_bytes(client: TestClient, backend: Backend) -> None:
    job_id = _submit(client, _frames(FRAME_COUNT)).json()["job_id"]
    stored = backend.client.objects[f"jobs/{job_id}/frames/000001.jpg"]
    assert stored[:2] == b"\xff\xd8"


def test_the_frames_are_uploaded_before_the_job_exists_and_is_queued(backend: Backend) -> None:
    seen: list[tuple[int, int]] = []

    def enqueue(job_id: str) -> None:
        seen.append((len(backend.client.objects), len(backend.store.created)))

    objects = S3ObjectStore(backend.client, BUCKET)
    app = create_app(backend.store, backend.layout, LIMITS, enqueue, lambda: None, objects=objects)
    assert _submit(TestClient(app), _frames(FRAME_COUNT)).status_code == 202
    assert seen == [(FRAME_COUNT, 1)]


def test_an_upload_that_fails_halfway_leaves_nothing_and_creates_no_job(client: TestClient, backend: Backend) -> None:
    backend.client.fail_uploads_after = 2
    response = _submit(client, _frames(FRAME_COUNT))
    assert 500 <= response.status_code < 600
    assert "cannot store the upload" in response.json()["detail"]
    assert backend.client.objects == {}
    assert backend.store.created == [] and backend.queued == []
    assert _local_files(backend) == []


def test_a_failed_cleanup_after_a_failed_upload_is_logged_with_the_job_id(
    client: TestClient, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    backend.client.fail_uploads_after = 2
    backend.client.delete_errors = [{"Key": "x", "Code": "AccessDenied", "Message": "denied"}]
    with caplog.at_level(logging.ERROR):
        response = _submit(client, _frames(FRAME_COUNT))
    assert 500 <= response.status_code < 600
    assert "could not clean up" in caplog.text and "jobs/" in caplog.text
    assert SECRET not in caplog.text


def test_a_queue_failure_removes_the_objects_and_fails_the_job(client: TestClient, backend: Backend) -> None:
    backend.enqueue_error = ConnectionError("redis is down")
    response = _submit(client, _frames(FRAME_COUNT))
    assert response.status_code == 503
    assert backend.client.objects == {}
    (job_id,) = backend.store.created
    record = backend.store.get(job_id)
    assert record is not None and record.status is JobStatus.FAILED


def test_a_succeeded_job_returns_the_result_object(client: TestClient, backend: Backend) -> None:
    job_id = _succeeded_job(backend)
    backend.client.objects[f"jobs/{job_id}/result.txt"] = RESULT_TEXT.encode()
    response = client.get(f"/jobs/{job_id}/result")
    assert response.status_code == 200
    assert response.text == RESULT_TEXT
    assert all(len(line.split(",")) == 10 for line in response.text.splitlines())


def test_a_succeeded_job_without_a_result_object_reports_it_missing(client: TestClient, backend: Backend) -> None:
    job_id = _succeeded_job(backend)
    response = client.get(f"/jobs/{job_id}/result")
    assert response.status_code == 500
    assert "missing" in response.json()["detail"] and job_id in response.json()["detail"]


def test_the_other_result_responses_match_the_filesystem_backend(client: TestClient, backend: Backend) -> None:
    backend.store.create(new_job("queued1", 1.0))
    assert client.get("/jobs/queued1/result").status_code == 409
    backend.store.create(new_job("failed1", 1.0))
    backend.store.transition("failed1", JobStatus.RUNNING, 2.0)
    backend.store.transition("failed1", JobStatus.FAILED, 3.0, reason="boom")
    assert client.get("/jobs/failed1/result").status_code == 410
    assert client.get("/jobs/unknown/result").status_code == 404


def test_an_existing_bucket_and_writable_scratch_are_healthy(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_a_missing_bucket_is_unavailable_and_named_without_secrets(client: TestClient, backend: Backend) -> None:
    backend.client.bucket_exists = False
    response = client.get("/health")
    assert response.status_code == 503
    problems = response.json()["problems"]
    assert len(problems) == 1 and "object store" in problems[0] and BUCKET in problems[0]
    assert SECRET not in response.text


def test_unwritable_scratch_is_still_reported_with_the_s3_backend(tmp_path: Path) -> None:
    blocker = tmp_path / "scratch"
    blocker.write_text("a file where the scratch folder should be")
    backend = Backend(RecordingStore(), StorageLayout(blocker), FakeS3Client())
    objects = S3ObjectStore(backend.client, BUCKET)
    app = create_app(backend.store, backend.layout, LIMITS, backend.enqueue, lambda: None, objects=objects)
    response = TestClient(app).get("/health")
    assert response.status_code == 503 and any("storage" in problem for problem in response.json()["problems"])
