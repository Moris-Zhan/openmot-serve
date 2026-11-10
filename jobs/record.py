from dataclasses import dataclass, replace
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_NEXT_STATUSES: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    status: JobStatus
    reason: str | None
    created_at: float
    updated_at: float


def new_job(job_id: str, now: float) -> JobRecord:
    return JobRecord(job_id, JobStatus.QUEUED, None, created_at=now, updated_at=now)


def can_transition(source: JobStatus, target: JobStatus) -> bool:
    return target in _NEXT_STATUSES[source]


def transition(record: JobRecord, target: JobStatus, now: float, reason: str | None = None) -> JobRecord:
    if not can_transition(record.status, target):
        raise InvalidTransition(
            f"job {record.job_id}: cannot go from {record.status.value} to {target.value}"
        )
    if target is JobStatus.FAILED and not (reason and reason.strip()):
        raise ValueError("a failed job needs a non-empty reason")
    if target is not JobStatus.FAILED and reason is not None:
        raise ValueError(f"only a failed job carries a reason, got one for {target.value}")
    if now < record.updated_at:
        raise ValueError(f"time ran backwards: {now} is before {record.updated_at}")
    return replace(record, status=target, reason=reason, updated_at=now)
