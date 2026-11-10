import multiprocessing
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from jobs.memory_store import InMemoryJobStore
from jobs.record import InvalidTransition, JobStatus, new_job
from jobs.store import JobNotFound, JobStore

REDIS_URL = os.environ.get("OPENMOT_TEST_REDIS_URL", "redis://localhost:16379/0")


@dataclass(frozen=True)
class Harness:
    store: JobStore
    advance: Callable[[float], None]


def _memory_harness() -> Iterator[Harness]:
    clock = [1000.0]

    def advance(seconds: float) -> None:
        clock[0] += seconds

    yield Harness(InMemoryJobStore(clock=lambda: clock[0]), advance)


def _redis_harness() -> Iterator[Harness]:
    import redis

    from jobs.redis_store import RedisJobStore

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip(f"no Redis at {REDIS_URL}; start one or set OPENMOT_TEST_REDIS_URL")
    prefix = f"openmot-test-{uuid.uuid4().hex}:"
    yield Harness(RedisJobStore(client, prefix=prefix), time.sleep)
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


@pytest.fixture(params=["memory", pytest.param("redis", marks=pytest.mark.integration)])
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    yield from (_memory_harness if request.param == "memory" else _redis_harness)()


def test_a_created_job_can_be_read_back(harness: Harness) -> None:
    job = new_job("job-1", now=10.0)
    assert harness.store.create(job) is True
    assert harness.store.get("job-1") == job


def test_an_unknown_job_reads_as_none(harness: Harness) -> None:
    assert harness.store.get("missing") is None


def test_creating_the_same_job_twice_keeps_the_first(harness: Harness) -> None:
    first = new_job("job-1", now=10.0)
    assert harness.store.create(first) is True
    assert harness.store.create(new_job("job-1", now=99.0)) is False
    assert harness.store.get("job-1") == first


def test_a_transition_is_persisted_and_returned(harness: Harness) -> None:
    harness.store.create(new_job("job-1", now=10.0))
    running = harness.store.transition("job-1", JobStatus.RUNNING, now=20.0)
    assert running.status is JobStatus.RUNNING and running.updated_at == 20.0
    assert harness.store.get("job-1") == running


def test_a_failure_reason_survives_the_round_trip(harness: Harness) -> None:
    harness.store.create(new_job("job-1", now=10.0))
    harness.store.transition("job-1", JobStatus.RUNNING, now=20.0)
    failed = harness.store.transition("job-1", JobStatus.FAILED, now=30.0, reason="frame 7 is unreadable")
    assert harness.store.get("job-1") == failed
    assert failed.reason == "frame 7 is unreadable"
    assert failed.created_at == 10.0


def test_an_illegal_transition_leaves_the_stored_job_untouched(harness: Harness) -> None:
    harness.store.create(new_job("job-1", now=10.0))
    before = harness.store.get("job-1")
    with pytest.raises(InvalidTransition):
        harness.store.transition("job-1", JobStatus.SUCCEEDED, now=20.0)
    assert harness.store.get("job-1") == before


def test_a_terminal_job_cannot_be_overwritten(harness: Harness) -> None:
    harness.store.create(new_job("job-1", now=10.0))
    harness.store.transition("job-1", JobStatus.RUNNING, now=20.0)
    done = harness.store.transition("job-1", JobStatus.SUCCEEDED, now=30.0)
    with pytest.raises(InvalidTransition):
        harness.store.transition("job-1", JobStatus.FAILED, now=40.0, reason="late")
    assert harness.store.get("job-1") == done


def test_transitioning_an_unknown_job_is_reported(harness: Harness) -> None:
    with pytest.raises(JobNotFound, match="missing"):
        harness.store.transition("missing", JobStatus.RUNNING, now=1.0)


def test_only_one_owner_holds_a_lock(harness: Harness) -> None:
    assert harness.store.acquire_lock("job-1", "worker-a", timeout_seconds=30) is True
    assert harness.store.acquire_lock("job-1", "worker-b", timeout_seconds=30) is False
    assert harness.store.acquire_lock("job-1", "worker-a", timeout_seconds=30) is False


def test_locks_are_per_job(harness: Harness) -> None:
    assert harness.store.acquire_lock("job-1", "worker-a", timeout_seconds=30) is True
    assert harness.store.acquire_lock("job-2", "worker-b", timeout_seconds=30) is True


def test_only_the_owner_can_release_a_lock(harness: Harness) -> None:
    harness.store.acquire_lock("job-1", "worker-a", timeout_seconds=30)
    assert harness.store.release_lock("job-1", "worker-b") is False
    assert harness.store.acquire_lock("job-1", "worker-b", timeout_seconds=30) is False
    assert harness.store.release_lock("job-1", "worker-a") is True
    assert harness.store.acquire_lock("job-1", "worker-b", timeout_seconds=30) is True


def test_releasing_a_lock_nobody_holds_is_harmless(harness: Harness) -> None:
    assert harness.store.release_lock("job-1", "worker-a") is False


def test_an_expired_lock_can_be_taken_over(harness: Harness) -> None:
    assert harness.store.acquire_lock("job-1", "worker-a", timeout_seconds=1) is True
    harness.advance(1.3)
    assert harness.store.acquire_lock("job-1", "worker-b", timeout_seconds=30) is True
    assert harness.store.release_lock("job-1", "worker-a") is False


RACERS = 8
_start_line: "multiprocessing.synchronize.Barrier | None" = None


def _install_start_line(barrier: "multiprocessing.synchronize.Barrier") -> None:
    global _start_line
    _start_line = barrier


def _wait_for_the_others() -> None:
    assert _start_line is not None
    _start_line.wait(timeout=30)


def _claim(args: tuple[str, str]) -> bool:
    import redis

    from jobs.redis_store import RedisJobStore

    prefix, job_id = args
    store = RedisJobStore(redis.Redis.from_url(REDIS_URL, decode_responses=True), prefix=prefix)
    _wait_for_the_others()
    try:
        store.transition(job_id, JobStatus.RUNNING, now=time.time())
    except InvalidTransition:
        return False
    return True


def _lock(args: tuple[str, str, str]) -> bool:
    import redis

    from jobs.redis_store import RedisJobStore

    prefix, job_id, owner = args
    store = RedisJobStore(redis.Redis.from_url(REDIS_URL, decode_responses=True), prefix=prefix)
    _wait_for_the_others()
    return store.acquire_lock(job_id, owner, timeout_seconds=30)


@pytest.mark.integration
def test_processes_racing_for_one_job_produce_exactly_one_winner() -> None:
    import redis

    from jobs.redis_store import RedisJobStore

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip(f"no Redis at {REDIS_URL}")
    prefix = f"openmot-test-{uuid.uuid4().hex}:"
    store = RedisJobStore(client, prefix=prefix)
    job_ids = [f"job-{number}" for number in range(100)]
    for job_id in job_ids:
        store.create(new_job(job_id, now=time.time()))
    context = multiprocessing.get_context("fork")
    start_line = context.Barrier(RACERS)
    with context.Pool(RACERS, initializer=_install_start_line, initargs=(start_line,)) as pool:
        claims = pool.map(_claim, [(prefix, job_id) for job_id in job_ids for _ in range(RACERS)], chunksize=1)
        locks = pool.map(
            _lock, [(prefix, job_id, f"owner-{n}") for job_id in job_ids for n in range(RACERS)], chunksize=1
        )
    try:
        for index, job_id in enumerate(job_ids):
            assert sum(claims[index * RACERS : (index + 1) * RACERS]) == 1, job_id
            assert sum(locks[index * RACERS : (index + 1) * RACERS]) == 1, job_id
            assert store.get(job_id).status is JobStatus.RUNNING
    finally:
        for key in client.scan_iter(f"{prefix}*"):
            client.delete(key)
