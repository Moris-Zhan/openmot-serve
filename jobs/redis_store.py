import redis

from jobs.record import JobRecord, JobStatus, transition
from jobs.store import JobNotFound

_RELEASE_IF_OWNER = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _fields(record: JobRecord) -> dict[str, str]:
    return {
        "status": record.status.value,
        "reason": record.reason or "",
        "created_at": repr(record.created_at),
        "updated_at": repr(record.updated_at),
    }


def _record(job_id: str, fields: dict[str, str]) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        status=JobStatus(fields["status"]),
        reason=fields["reason"] or None,
        created_at=float(fields["created_at"]),
        updated_at=float(fields["updated_at"]),
    )


class RedisJobStore:
    def __init__(self, client: redis.Redis, prefix: str = "openmot:") -> None:
        self._client = client
        self._prefix = prefix
        self._release_if_owner = client.register_script(_RELEASE_IF_OWNER)

    def _job_key(self, job_id: str) -> str:
        return f"{self._prefix}job:{job_id}"

    def _lock_key(self, job_id: str) -> str:
        return f"{self._prefix}lock:{job_id}"

    def create(self, record: JobRecord) -> bool:
        key = self._job_key(record.job_id)
        with self._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    if pipe.exists(key):
                        return False
                    pipe.multi()
                    pipe.hset(key, mapping=_fields(record))
                    pipe.execute()
                    return True
                except redis.WatchError:
                    continue

    def get(self, job_id: str) -> JobRecord | None:
        fields = self._client.hgetall(self._job_key(job_id))
        return _record(job_id, fields) if fields else None

    def transition(
        self, job_id: str, target: JobStatus, now: float, reason: str | None = None
    ) -> JobRecord:
        key = self._job_key(job_id)
        with self._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    fields = pipe.hgetall(key)
                    if not fields:
                        raise JobNotFound(job_id)
                    updated = transition(_record(job_id, fields), target, now, reason)
                    pipe.multi()
                    pipe.hset(key, mapping=_fields(updated))
                    pipe.execute()
                    return updated
                except redis.WatchError:
                    continue

    def acquire_lock(self, job_id: str, owner: str, timeout_seconds: int) -> bool:
        return bool(self._client.set(self._lock_key(job_id), owner, nx=True, ex=timeout_seconds))

    def release_lock(self, job_id: str, owner: str) -> bool:
        return bool(self._release_if_owner(keys=[self._lock_key(job_id)], args=[owner]))
