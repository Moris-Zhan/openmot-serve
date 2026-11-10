import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from jobs.layout import StorageLayout
from jobs.record import JobStatus
from jobs.store import JobStore
from tracking.config import RunMetadata, metadata_path_for
from tracking.sequence import DetectFrame, track_sequence


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


def _failure_reason(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _discard_partial_output(context: WorkerContext, job_id: str) -> None:
    result_path = context.layout.result_path(job_id)
    result_path.unlink(missing_ok=True)
    metadata_path_for(result_path).unlink(missing_ok=True)


def _run_tracking(context: WorkerContext, job_id: str, clock: Callable[[], float]) -> ProcessOutcome:
    try:
        track_sequence(
            context.layout.frames_dir(job_id),
            context.layout.result_path(job_id),
            context.detect_frame,
            context.metadata,
        )
    except Exception as error:
        _discard_partial_output(context, job_id)
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
