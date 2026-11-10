import threading
import time
from collections.abc import Callable

from jobs.record import JobRecord, JobStatus, transition
from jobs.store import JobNotFound


class InMemoryJobStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._guard = threading.Lock()
        self._records: dict[str, JobRecord] = {}
        self._locks: dict[str, tuple[str, float]] = {}

    def create(self, record: JobRecord) -> bool:
        with self._guard:
            if record.job_id in self._records:
                return False
            self._records[record.job_id] = record
            return True

    def get(self, job_id: str) -> JobRecord | None:
        with self._guard:
            return self._records.get(job_id)

    def transition(
        self, job_id: str, target: JobStatus, now: float, reason: str | None = None
    ) -> JobRecord:
        with self._guard:
            current = self._records.get(job_id)
            if current is None:
                raise JobNotFound(job_id)
            updated = transition(current, target, now, reason)
            self._records[job_id] = updated
            return updated

    def acquire_lock(self, job_id: str, owner: str, timeout_seconds: int) -> bool:
        with self._guard:
            held = self._locks.get(job_id)
            if held is not None and held[1] > self._clock():
                return False
            self._locks[job_id] = (owner, self._clock() + timeout_seconds)
            return True

    def release_lock(self, job_id: str, owner: str) -> bool:
        with self._guard:
            held = self._locks.get(job_id)
            if held is None or held[0] != owner or held[1] <= self._clock():
                return False
            del self._locks[job_id]
            return True
