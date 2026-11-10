import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobRecord, JobStatus

LIMITS = UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=2_000_000)


@dataclass
class Backend:
    store: InMemoryJobStore
    layout: StorageLayout
    queued: list[str] = field(default_factory=list)
    enqueue_error: Exception | None = None

    def enqueue(self, job_id: str) -> None:
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.queued.append(job_id)


def _jpeg(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    ok, encoded = cv2.imencode(".jpg", rng.integers(0, 255, (16, 16, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _frames(count: int = 4) -> bytes:
    return _zip({f"{number:06d}.jpg": _jpeg(number) for number in range(1, count + 1)})


def _app_client(backend: Backend, limits: UploadLimits = LIMITS) -> TestClient:
    return TestClient(create_app(backend.store, backend.layout, limits, backend.enqueue, lambda: None))


@pytest.fixture
def backend(tmp_path: Path) -> Backend:
    return Backend(InMemoryJobStore(), StorageLayout(tmp_path / "storage"))


@pytest.fixture
def client(backend: Backend) -> TestClient:
    return _app_client(backend)


def _submit(client: TestClient, data: bytes):
    return client.post("/jobs", files={"file": ("frames.zip", data, "application/zip")})


def _files_under_storage(backend: Backend) -> list[Path]:
    root = backend.layout.root
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


def _job_folders(backend: Backend) -> list[Path]:
    jobs = backend.layout.root / "jobs"
    return list(jobs.iterdir()) if jobs.exists() else []


def test_a_valid_archive_is_accepted_with_a_job_id_and_a_queued_status(client: TestClient, backend: Backend) -> None:
    response = _submit(client, _frames(4))
    assert response.status_code == 202
    body = response.json()
    job_id = body["job_id"]
    assert body["status"] == "queued"
    record = backend.store.get(job_id)
    assert record is not None and record.status is JobStatus.QUEUED
    assert backend.queued == [job_id]
    assert len(list(backend.layout.frames_dir(job_id).iterdir())) == 4


def test_the_job_and_its_frames_exist_before_the_job_is_queued(backend: Backend) -> None:
    seen: list[tuple[JobRecord | None, bool]] = []

    def enqueue(job_id: str) -> None:
        seen.append((backend.store.get(job_id), backend.layout.frames_dir(job_id).is_dir()))

    client = TestClient(create_app(backend.store, backend.layout, LIMITS, enqueue, lambda: None))
    assert _submit(client, _frames()).status_code == 202
    record, frames_present = seen[0]
    assert record is not None and record.status is JobStatus.QUEUED
    assert frames_present is True


def test_two_identical_submissions_get_different_jobs(client: TestClient, backend: Backend) -> None:
    data = _frames(3)
    first = _submit(client, data).json()["job_id"]
    second = _submit(client, data).json()["job_id"]
    assert first != second
    assert backend.queued == [first, second]
    assert len(_job_folders(backend)) == 2


@pytest.mark.parametrize(
    ("data", "status", "reason"),
    [
        (b"definitely not a zip", 422, "zip"),
        (_zip({}), 422, "no frames"),
        (_zip({"../evil.jpg": b"x", "000001.jpg": b"x"}), 422, "path"),
        (_zip({"000001.jpg": _jpeg(1), "000003.jpg": _jpeg(3)}), 422, "consecutive"),
        (_zip({"000001.jpg": b"not an image"}), 422, "000001.jpg"),
        (_zip({"000001.jpg": b"\0" * 5_000_000}), 413, "unpacked"),
    ],
)
def test_invalid_uploads_are_rejected_without_a_job_or_any_file(
    client: TestClient, backend: Backend, data: bytes, status: int, reason: str
) -> None:
    response = _submit(client, data)
    assert response.status_code == status
    assert reason in response.json()["detail"]
    assert backend.queued == []
    assert _files_under_storage(backend) == []
    assert _job_folders(backend) == []


def test_an_upload_over_the_size_limit_is_rejected_with_413(backend: Backend) -> None:
    client = _app_client(backend, UploadLimits(max_upload_bytes=100, max_unpacked_bytes=1000))
    response = _submit(client, _frames(4))
    assert response.status_code == 413
    assert backend.queued == []
    assert _files_under_storage(backend) == []


def test_a_request_without_a_file_is_rejected(client: TestClient, backend: Backend) -> None:
    response = client.post("/jobs")
    assert 400 <= response.status_code < 500
    assert backend.queued == []


def test_a_queue_failure_returns_5xx_and_removes_the_frames(backend: Backend) -> None:
    backend.enqueue_error = ConnectionError("broker is down")
    response = _submit(_app_client(backend), _frames())
    assert response.status_code == 503
    assert "queue" in response.json()["detail"]
    assert _files_under_storage(backend) == []
    assert _job_folders(backend) == []


def test_a_job_that_could_not_be_queued_is_not_left_looking_queued(backend: Backend) -> None:
    backend.enqueue_error = ConnectionError("broker is down")
    created: list[str] = []
    original_create = backend.store.create

    def recording_create(record: JobRecord) -> bool:
        created.append(record.job_id)
        return original_create(record)

    backend.store.create = recording_create  # type: ignore[method-assign]
    _submit(_app_client(backend), _frames())
    assert len(created) == 1
    record = backend.store.get(created[0])
    assert record is not None and record.status is JobStatus.FAILED and "queue" in (record.reason or "")


def test_staging_is_empty_after_a_submission(client: TestClient, backend: Backend) -> None:
    _submit(client, _frames())
    assert list((backend.layout.root / "staging").iterdir()) == []
