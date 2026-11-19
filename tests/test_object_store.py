import sys
from pathlib import Path

import pytest

from jobs.object_store import ObjectNotFound, ObjectStoreError, S3ObjectStore, create_object_store
from jobs.settings import load_settings

BUCKET = "tracking"
SECRET = "do-not-print-this-secret"


class FakeClientError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.response = {"Error": {"Code": code, "Message": message}}


class FakeS3Client:
    """An in-memory stand-in for the boto3 client methods the store uses."""

    def __init__(self, page_size: int = 1000) -> None:
        self.objects: dict[str, bytes] = {}
        self.page_size = page_size
        self.delete_batches: list[int] = []
        self.bucket_exists = True
        self.delete_errors: list[dict[str, str]] = []
        self.fail_uploads_after: int | None = None
        self.uploads = 0

    def _require_bucket(self) -> None:
        if not self.bucket_exists:
            raise FakeClientError("NoSuchBucket", f"bucket does not exist (secret={SECRET})")

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:  # noqa: N803
        self._require_bucket()
        if self.fail_uploads_after is not None and self.uploads >= self.fail_uploads_after:
            raise FakeClientError("InternalError", "the store refused the write")
        self.uploads += 1
        self.objects[Key] = Path(Filename).read_bytes()

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        self._require_bucket()
        if Key not in self.objects:
            raise FakeClientError("404", "Not Found")
        Path(Filename).write_bytes(self.objects[Key])

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        self._require_bucket()
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey", "The specified key does not exist.")
        body = self.objects[Key]
        return {"Body": type("Body", (), {"read": lambda self_: body})()}

    def list_objects_v2(self, Bucket: str, Prefix: str, ContinuationToken: str | None = None) -> dict:  # noqa: N803
        self._require_bucket()
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken) if ContinuationToken else 0
        page = keys[start : start + self.page_size]
        truncated = start + self.page_size < len(keys)
        response: dict = {"Contents": [{"Key": key} for key in page], "IsTruncated": truncated, "KeyCount": len(page)}
        if truncated:
            response["NextContinuationToken"] = str(start + self.page_size)
        return response

    def delete_objects(self, Bucket: str, Delete: dict) -> dict:  # noqa: N803
        self._require_bucket()
        batch = [entry["Key"] for entry in Delete["Objects"]]
        assert len(batch) <= 1000, "DeleteObjects accepts at most 1000 keys"
        self.delete_batches.append(len(batch))
        if self.delete_errors:
            return {"Errors": self.delete_errors}
        for key in batch:
            self.objects.pop(key, None)
        return {}

    def head_bucket(self, Bucket: str) -> dict:  # noqa: N803
        self._require_bucket()
        return {}


@pytest.fixture
def client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def store(client: FakeS3Client) -> S3ObjectStore:
    return S3ObjectStore(client, BUCKET)


def test_a_file_round_trips_through_upload_download_and_read(store: S3ObjectStore, tmp_path: Path) -> None:
    source = tmp_path / "frame.jpg"
    source.write_bytes(b"\xff\xd8 frame bytes")
    store.upload_file("jobs/a/frames/000001.jpg", source)
    target = tmp_path / "copy.jpg"
    store.download_file("jobs/a/frames/000001.jpg", target)
    assert target.read_bytes() == source.read_bytes()
    assert store.read_bytes("jobs/a/frames/000001.jpg") == source.read_bytes()


def test_reading_a_missing_key_raises_a_recognisable_error(store: S3ObjectStore, tmp_path: Path) -> None:
    with pytest.raises(ObjectNotFound, match="jobs/a/result.txt"):
        store.read_bytes("jobs/a/result.txt")
    with pytest.raises(ObjectNotFound):
        store.download_file("jobs/a/frames/missing.jpg", tmp_path / "missing.jpg")


def test_listing_follows_every_page(client: FakeS3Client, store: S3ObjectStore) -> None:
    client.page_size = 7
    client.objects.update({f"jobs/a/frames/{number:06d}.jpg": b"x" for number in range(25)})
    client.objects["jobs/b/frames/000001.jpg"] = b"other job"
    assert store.list_keys("jobs/a/") == [f"jobs/a/frames/{number:06d}.jpg" for number in range(25)]


def test_deleting_a_prefix_handles_more_than_one_page_and_one_batch(client: FakeS3Client, store: S3ObjectStore) -> None:
    client.page_size = 600
    client.objects.update({f"jobs/a/frames/{number:06d}.jpg": b"x" for number in range(2500)})
    client.objects["jobs/b/result.txt"] = b"keep me"
    store.delete_prefix("jobs/a/")
    assert list(client.objects) == ["jobs/b/result.txt"]
    assert max(client.delete_batches) <= 1000


def test_deleting_an_empty_prefix_is_not_an_error(client: FakeS3Client, store: S3ObjectStore) -> None:
    store.delete_prefix("jobs/nothing/")
    assert client.delete_batches == []


def test_a_delete_error_reported_by_the_store_is_raised(client: FakeS3Client, store: S3ObjectStore) -> None:
    client.objects["jobs/a/result.txt"] = b"x"
    client.delete_errors = [{"Key": "jobs/a/result.txt", "Code": "AccessDenied", "Message": "denied"}]
    with pytest.raises(ObjectStoreError, match="jobs/a/result.txt"):
        store.delete_prefix("jobs/a/")


def test_check_passes_for_an_existing_bucket(store: S3ObjectStore) -> None:
    store.check()


def test_check_names_the_bucket_and_the_error_class_only(client: FakeS3Client, store: S3ObjectStore) -> None:
    client.bucket_exists = False
    with pytest.raises(ObjectStoreError) as error:
        store.check()
    message = str(error.value)
    assert BUCKET in message and "FakeClientError" in message
    assert SECRET not in message


def test_the_filesystem_backend_creates_no_store_and_never_imports_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    assert create_object_store(load_settings({"OPENMOT_STORAGE_DIR": "/srv/openmot"})) is None


def test_the_s3_backend_builds_a_client_with_timeouts_and_path_style_addressing() -> None:
    pytest.importorskip("boto3")
    settings = load_settings(
        {
            "OPENMOT_STORAGE_DIR": "/srv/openmot",
            "OPENMOT_STORAGE_BACKEND": "s3",
            "OPENMOT_S3_BUCKET": BUCKET,
            "OPENMOT_S3_ENDPOINT_URL": "http://minio:9000",
        }
    )
    store = create_object_store(settings)
    assert isinstance(store, S3ObjectStore)
    config = store.client.meta.config
    assert (config.connect_timeout, config.read_timeout) == (5, 30)
    assert config.s3["addressing_style"] == "path"
    assert store.client.meta.endpoint_url == "http://minio:9000"
    assert store.bucket == BUCKET
