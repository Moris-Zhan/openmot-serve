from typing import Protocol

from jobs.record import JobRecord, JobStatus


class JobNotFound(LookupError):
    pass


class JobStore(Protocol):
    def create(self, record: JobRecord) -> bool: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def transition(
        self, job_id: str, target: JobStatus, now: float, reason: str | None = None
    ) -> JobRecord: ...

    def acquire_lock(self, job_id: str, owner: str, timeout_seconds: int) -> bool: ...

    def release_lock(self, job_id: str, owner: str) -> bool: ...
