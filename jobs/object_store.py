from pathlib import Path
from typing import Any, Protocol

from jobs.layout import checked_job_id
from jobs.settings import Settings

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30
DELETE_BATCH_SIZE = 1000
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


class ObjectStoreError(RuntimeError):
    pass


class ObjectNotFound(ObjectStoreError):
    pass


class ObjectStore(Protocol):
    def upload_file(self, key: str, path: Path) -> None: ...

    def download_file(self, key: str, path: Path) -> None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def check(self) -> None: ...


def job_prefix(job_id: str) -> str:
    return f"jobs/{checked_job_id(job_id)}/"


def frames_prefix(job_id: str) -> str:
    return f"{job_prefix(job_id)}frames/"


def result_key(job_id: str) -> str:
    return f"{job_prefix(job_id)}result.txt"


def metadata_key(job_id: str) -> str:
    return f"{job_prefix(job_id)}result.meta.json"


def upload_frames(objects: ObjectStore, job_id: str, directory: Path) -> None:
    prefix = frames_prefix(job_id)
    for path in sorted(directory.iterdir()):
        objects.upload_file(f"{prefix}{path.name}", path)


def download_frames(objects: ObjectStore, job_id: str, directory: Path) -> None:
    prefix = frames_prefix(job_id)
    keys = objects.list_keys(prefix)
    if not keys:
        raise ObjectNotFound(f"job {job_id} has no frames in the object store")
    directory.mkdir(parents=True, exist_ok=True)
    for key in keys:
        name = key[len(prefix) :]
        if not name or "/" in name or name in (".", ".."):
            raise ObjectStoreError(f"unexpected object key {key!r} under {prefix}")
        objects.download_file(key, directory / name)


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


class S3ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def upload_file(self, key: str, path: Path) -> None:
        self.client.upload_file(Filename=str(path), Bucket=self.bucket, Key=key)

    def download_file(self, key: str, path: Path) -> None:
        try:
            self.client.download_file(Bucket=self.bucket, Key=key, Filename=str(path))
        except Exception as error:
            if _error_code(error) in _MISSING_CODES:
                raise ObjectNotFound(f"object {key} does not exist in bucket {self.bucket!r}") from error
            raise

    def read_bytes(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as error:
            if _error_code(error) in _MISSING_CODES:
                raise ObjectNotFound(f"object {key} does not exist in bucket {self.bucket!r}") from error
            raise

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            arguments: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token is not None:
                arguments["ContinuationToken"] = token
            page = self.client.list_objects_v2(**arguments)
            keys.extend(entry["Key"] for entry in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            token = page["NextContinuationToken"]

    def delete_prefix(self, prefix: str) -> None:
        keys = self.list_keys(prefix)
        for start in range(0, len(keys), DELETE_BATCH_SIZE):
            batch = keys[start : start + DELETE_BATCH_SIZE]
            response = self.client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True}
            )
            failures = response.get("Errors") or []
            if failures:
                first = failures[0]
                raise ObjectStoreError(
                    f"could not delete {len(failures)} object(s) under {prefix}, "
                    f"first {first.get('Key')}: {first.get('Code')}"
                )

    def check(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as error:
            raise ObjectStoreError(
                f"the object store is unavailable: bucket {self.bucket!r}, {type(error).__name__}"
            ) from error


def create_object_store(settings: Settings) -> S3ObjectStore | None:
    if settings.storage_backend != "s3":
        return None
    if settings.s3_bucket is None:
        raise ObjectStoreError("OPENMOT_S3_BUCKET is required for the s3 storage backend")
    import boto3
    from botocore.config import Config

    addressing = {"s3": {"addressing_style": "path"}} if settings.s3_endpoint_url else {}
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        config=Config(connect_timeout=CONNECT_TIMEOUT_SECONDS, read_timeout=READ_TIMEOUT_SECONDS, **addressing),
    )
    return S3ObjectStore(client, settings.s3_bucket)
