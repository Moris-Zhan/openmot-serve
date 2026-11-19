import logging
import signal
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from jobs.layout import StorageLayout, remove_job
from jobs.object_store import ObjectStore, download_frames, metadata_key, result_key
from jobs.record import JobStatus
from jobs.store import JobStore
from tracking.config import RunMetadata, metadata_path_for
from tracking.sequence import DetectFrame, track_sequence

logger = logging.getLogger(__name__)


class ProcessOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class WorkerContext:
    store: JobStore
    layout: StorageLayout
    detect_frame: DetectFrame
    metadata: RunMetadata
    owner: str
    lock_timeout_seconds: int
    time_limit_seconds: int | None = None
    objects: ObjectStore | None = None


class JobTimedOut(Exception):
    pass


@contextmanager
def _within_time_limit(seconds: int | None) -> Iterator[None]:
    if seconds is None:
        yield
        return

    def on_alarm(signal_number: int, frame: object) -> None:
        raise JobTimedOut(f"the job exceeded its time limit of {seconds} seconds")

    previous_handler = signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _failure_reason(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _discard_partial_output(context: WorkerContext, job_id: str) -> None:
    result_path = context.layout.result_path(job_id)
    result_path.unlink(missing_ok=True)
    metadata_path_for(result_path).unlink(missing_ok=True)


def _discard_result_objects(objects: ObjectStore, job_id: str) -> None:
    for key in (result_key(job_id), metadata_key(job_id)):
        try:
            objects.delete_prefix(key)
        except Exception as error:
            logger.error("could not remove the object %s: %s", key, type(error).__name__)


def _track_through_objects(context: WorkerContext, objects: ObjectStore, job_id: str) -> None:
    remove_job(context.layout, job_id)
    frames_dir = context.layout.frames_dir(job_id)
    result_path = context.layout.result_path(job_id)
    try:
        download_frames(objects, job_id, frames_dir)
        track_sequence(frames_dir, result_path, context.detect_frame, context.metadata)
        objects.upload_file(metadata_key(job_id), metadata_path_for(result_path))
        objects.upload_file(result_key(job_id), result_path)
    finally:
        remove_job(context.layout, job_id)


def _run_tracking(context: WorkerContext, job_id: str, clock: Callable[[], float]) -> ProcessOutcome:
    try:
        with _within_time_limit(context.time_limit_seconds):
            if context.objects is None:
                track_sequence(
                    context.layout.frames_dir(job_id),
                    context.layout.result_path(job_id),
                    context.detect_frame,
                    context.metadata,
                )
            else:
                _track_through_objects(context, context.objects, job_id)
    except Exception as error:
        _discard_partial_output(context, job_id)
        if context.objects is not None:
            _discard_result_objects(context.objects, job_id)
        context.store.transition(job_id, JobStatus.FAILED, clock(), reason=_failure_reason(error))
        return ProcessOutcome.FAILED
    context.store.transition(job_id, JobStatus.SUCCEEDED, clock())
    return ProcessOutcome.SUCCEEDED


def process_job(context: WorkerContext, job_id: str, clock: Callable[[], float] = time.time) -> ProcessOutcome:
    if not context.store.acquire_lock(job_id, context.owner, context.lock_timeout_seconds):
        return ProcessOutcome.SKIPPED
    try:
        current = context.store.get(job_id)
        if current is None or current.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            return ProcessOutcome.SKIPPED
        if current.status is JobStatus.QUEUED:
            context.store.transition(job_id, JobStatus.RUNNING, clock())
        return _run_tracking(context, job_id, clock)
    finally:
        context.store.release_lock(job_id, context.owner)
