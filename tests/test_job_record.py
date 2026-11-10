from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from jobs.record import InvalidTransition, JobRecord, JobStatus, can_transition, new_job, transition

LEGAL = {
    (JobStatus.QUEUED, JobStatus.RUNNING),
    (JobStatus.QUEUED, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.SUCCEEDED),
    (JobStatus.RUNNING, JobStatus.FAILED),
}
ALL_PAIRS = list(product(JobStatus, JobStatus))


def _record_in(status: JobStatus) -> JobRecord:
    reason = "boom" if status is JobStatus.FAILED else None
    return JobRecord("job-1", status, reason, created_at=100.0, updated_at=100.0)


def _reason_for(target: JobStatus) -> str | None:
    return "frame 3 is unreadable" if target is JobStatus.FAILED else None


def test_the_status_values_are_the_wire_names() -> None:
    assert [status.value for status in JobStatus] == ["queued", "running", "succeeded", "failed"]


def test_a_new_job_is_queued_without_a_reason() -> None:
    job = new_job("job-1", now=100.0)
    assert (job.job_id, job.status, job.reason) == ("job-1", JobStatus.QUEUED, None)
    assert job.created_at == job.updated_at == 100.0


@pytest.mark.parametrize("source, target", ALL_PAIRS)
def test_only_the_forward_transitions_are_legal(source: JobStatus, target: JobStatus) -> None:
    assert can_transition(source, target) is ((source, target) in LEGAL)


@pytest.mark.parametrize("source, target", sorted(LEGAL, key=str))
def test_a_legal_transition_returns_the_next_record(source: JobStatus, target: JobStatus) -> None:
    before = _record_in(source)
    after = transition(before, target, now=250.0, reason=_reason_for(target))
    assert after.status is target
    assert after.reason == _reason_for(target)
    assert after.created_at == 100.0 and after.updated_at == 250.0
    assert after.job_id == before.job_id
    assert before == _record_in(source)


@pytest.mark.parametrize("source, target", [pair for pair in ALL_PAIRS if pair not in LEGAL])
def test_an_illegal_transition_is_refused(source: JobStatus, target: JobStatus) -> None:
    with pytest.raises(InvalidTransition, match=f"{source.value}.*{target.value}"):
        transition(_record_in(source), target, now=250.0, reason=_reason_for(target))


@pytest.mark.parametrize("terminal", [JobStatus.SUCCEEDED, JobStatus.FAILED])
def test_a_terminal_job_can_never_change(terminal: JobStatus) -> None:
    for target in JobStatus:
        with pytest.raises(InvalidTransition):
            transition(_record_in(terminal), target, now=300.0, reason="again")


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_failing_requires_a_reason(reason: str | None) -> None:
    with pytest.raises(ValueError, match="reason"):
        transition(_record_in(JobStatus.RUNNING), JobStatus.FAILED, now=250.0, reason=reason)


@pytest.mark.parametrize("target", [JobStatus.RUNNING, JobStatus.SUCCEEDED])
def test_only_a_failure_carries_a_reason(target: JobStatus) -> None:
    source = JobStatus.QUEUED if target is JobStatus.RUNNING else JobStatus.RUNNING
    with pytest.raises(ValueError, match="reason"):
        transition(_record_in(source), target, now=250.0, reason="unexpected")


def test_the_record_is_immutable() -> None:
    job = new_job("job-1", now=1.0)
    with pytest.raises(FrozenInstanceError):
        job.status = JobStatus.SUCCEEDED  # type: ignore[misc]


def test_the_time_never_runs_backwards() -> None:
    with pytest.raises(ValueError, match="time"):
        transition(_record_in(JobStatus.QUEUED), JobStatus.RUNNING, now=99.0)
