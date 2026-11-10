import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tracking.files import write_text_atomic

_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class InvalidJobId(ValueError):
    pass


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageLayout:
    root: Path

    def job_dir(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise InvalidJobId(f"invalid job id {job_id!r}: use 1-64 letters, digits, '-' or '_'")
        return self.root / "jobs" / job_id

    def frames_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "frames"

    def result_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "result.txt"

    @property
    def staging_root(self) -> Path:
        return self.root / "staging"


def new_job_id() -> str:
    return uuid.uuid4().hex


def _safe_job_dir(layout: StorageLayout, job_id: str) -> Path:
    job_dir = layout.job_dir(job_id)
    if job_dir.resolve().parent != (layout.root / "jobs").resolve():
        raise StorageError(f"job folder for {job_id} does not resolve inside the storage root")
    return job_dir


@contextmanager
def staged_frames(layout: StorageLayout) -> Iterator[Path]:
    layout.staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=layout.staging_root, prefix="upload-"))
    staging.chmod(0o755)
    try:
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def publish_frames(layout: StorageLayout, job_id: str, staged: Path) -> Path:
    job_dir = _safe_job_dir(layout, job_id)
    try:
        job_dir.mkdir(parents=True)
    except FileExistsError as error:
        raise StorageError(f"job {job_id} already has a folder in storage") from error
    frames_dir = layout.frames_dir(job_id)
    try:
        os.rename(staged, frames_dir)
    except OSError as error:
        job_dir.rmdir()
        raise StorageError(f"cannot publish frames for job {job_id}: {error}") from error
    return frames_dir


def write_result(layout: StorageLayout, job_id: str, text: str) -> Path:
    job_dir = _safe_job_dir(layout, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(layout.result_path(job_id), text)
    return layout.result_path(job_id)


def remove_job(layout: StorageLayout, job_id: str) -> None:
    job_dir = _safe_job_dir(layout, job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)
