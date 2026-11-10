from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout, write_result
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobStatus, new_job
from tracking.motchallenge import ResultRow, format_result

LIMITS = UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=2_000_000)
RESULT = format_result(
    (
        ResultRow(1, 1, 10.0, 20.0, 30.0, 40.0, 0.9),
        ResultRow(2, 1, 11.0, 21.0, 30.0, 40.0, 0.8),
        ResultRow(2, 2, 100.0, 50.0, 25.0, 60.0, 0.7),
    )
)


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def layout(tmp_path: Path) -> StorageLayout:
    return StorageLayout(tmp_path / "storage")


@pytest.fixture
def client(store: InMemoryJobStore, layout: StorageLayout) -> TestClient:
    return TestClient(create_app(store, layout, LIMITS, lambda job_id: None))


def _job_in_state(store: InMemoryJobStore, job_id: str, target: JobStatus, reason: str | None = None) -> None:
    store.create(new_job(job_id, now=100.0))
    path = {
        JobStatus.QUEUED: [],
        JobStatus.RUNNING: [JobStatus.RUNNING],
        JobStatus.SUCCEEDED: [JobStatus.RUNNING, JobStatus.SUCCEEDED],
        JobStatus.FAILED: [JobStatus.RUNNING, JobStatus.FAILED],
    }[target]
    for step in path:
        store.transition(job_id, step, now=200.0, reason=reason if step is JobStatus.FAILED else None)


@pytest.mark.parametrize("target", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED])
def test_the_status_of_a_known_job_is_reported(client: TestClient, store: InMemoryJobStore, target: JobStatus) -> None:
    _job_in_state(store, "job-1", target)
    response = client.get("/jobs/job-1")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "job-1"
    assert body["status"] == target.value
    assert body["reason"] is None


def test_a_failed_job_reports_why(client: TestClient, store: InMemoryJobStore) -> None:
    _job_in_state(store, "job-1", JobStatus.FAILED, reason="frame 7 is unreadable")
    body = client.get("/jobs/job-1").json()
    assert body["status"] == "failed"
    assert body["reason"] == "frame 7 is unreadable"


def test_the_status_carries_timestamps(client: TestClient, store: InMemoryJobStore) -> None:
    _job_in_state(store, "job-1", JobStatus.RUNNING)
    body = client.get("/jobs/job-1").json()
    assert body["created_at"] == 100.0 and body["updated_at"] == 200.0


@pytest.mark.parametrize("path", ["/jobs/missing", "/jobs/missing/result", "/jobs/..%2F..%2Fetc/result"])
def test_an_unknown_job_is_a_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_a_succeeded_job_returns_its_result_with_ten_fields_per_line(
    client: TestClient, store: InMemoryJobStore, layout: StorageLayout
) -> None:
    _job_in_state(store, "job-1", JobStatus.SUCCEEDED)
    write_result(layout, "job-1", RESULT)
    response = client.get("/jobs/job-1/result")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == RESULT
    lines = response.text.splitlines()
    assert len(lines) == 3
    assert all(len(line.split(",")) == 10 for line in lines)


@pytest.mark.parametrize("target", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_an_unfinished_job_has_no_result_yet(
    client: TestClient, store: InMemoryJobStore, layout: StorageLayout, target: JobStatus
) -> None:
    _job_in_state(store, "job-1", target)
    write_result(layout, "job-1", RESULT)
    response = client.get("/jobs/job-1/result")
    assert response.status_code == 409
    assert target.value in response.json()["detail"]
    assert RESULT.splitlines()[0] not in response.text


def test_a_failed_job_has_no_result_and_says_so_differently(
    client: TestClient, store: InMemoryJobStore, layout: StorageLayout
) -> None:
    _job_in_state(store, "job-1", JobStatus.FAILED, reason="frame 7 is unreadable")
    write_result(layout, "job-1", RESULT)
    response = client.get("/jobs/job-1/result")
    assert response.status_code == 410
    assert "failed" in response.json()["detail"]
    assert "frame 7 is unreadable" in response.json()["detail"]
    assert RESULT.splitlines()[0] not in response.text


def test_a_succeeded_job_whose_result_file_is_missing_is_a_server_error(
    client: TestClient, store: InMemoryJobStore
) -> None:
    _job_in_state(store, "job-1", JobStatus.SUCCEEDED)
    assert client.get("/jobs/job-1/result").status_code == 500
