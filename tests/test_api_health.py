import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore

LIMITS = UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=2_000_000)


def _client(layout: StorageLayout, ping_queue: Callable[[], None]) -> TestClient:
    return TestClient(create_app(InMemoryJobStore(), layout, LIMITS, lambda job_id: None, ping_queue))


def _broken_queue() -> None:
    raise ConnectionError("redis is down")


def _storage_that_is_a_file(tmp_path: Path) -> StorageLayout:
    blocker = tmp_path / "storage"
    blocker.write_text("a file where the storage folder should be")
    return StorageLayout(blocker)


def test_a_reachable_queue_and_writable_storage_is_healthy(tmp_path: Path) -> None:
    response = _client(StorageLayout(tmp_path / "storage"), lambda: None).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_health_check_leaves_nothing_behind(tmp_path: Path) -> None:
    layout = StorageLayout(tmp_path / "storage")
    _client(layout, lambda: None).get("/health")
    assert [path for path in layout.root.rglob("*") if path.is_file()] == []


def test_an_unreachable_queue_is_unavailable_and_named(tmp_path: Path) -> None:
    response = _client(StorageLayout(tmp_path / "storage"), _broken_queue).get("/health")
    assert response.status_code == 503
    problems = response.json()["problems"]
    assert len(problems) == 1 and "queue" in problems[0] and "redis is down" in problems[0]


def test_unwritable_storage_is_unavailable_and_named(tmp_path: Path) -> None:
    response = _client(_storage_that_is_a_file(tmp_path), lambda: None).get("/health")
    assert response.status_code == 503
    problems = response.json()["problems"]
    assert len(problems) == 1 and "storage" in problems[0]


def test_both_problems_are_reported_together(tmp_path: Path) -> None:
    response = _client(_storage_that_is_a_file(tmp_path), _broken_queue).get("/health")
    assert response.status_code == 503
    text = " ".join(response.json()["problems"])
    assert "queue" in text and "storage" in text


def test_a_folder_that_exists_but_rejects_writes_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = StorageLayout(tmp_path / "storage")
    layout.root.mkdir()

    real_temporary_file = tempfile.TemporaryFile

    def read_only_storage(*arguments: object, **options: object):  # type: ignore[no-untyped-def]
        if options.get("dir") == layout.root:
            raise OSError("read-only file system")
        return real_temporary_file(*arguments, **options)

    monkeypatch.setattr("app.api.tempfile.TemporaryFile", read_only_storage)
    response = _client(layout, lambda: None).get("/health")
    assert response.status_code == 503
    assert "read-only" in response.json()["problems"][0]
