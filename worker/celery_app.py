import time
from collections.abc import Callable
from pathlib import Path

from celery import Celery

from jobs.settings import Settings
from metrics.definitions import Metrics
from worker.processing import ProcessOutcome, WorkerContext, process_job

TASK_NAME = "openmot.process_job"


def create_celery(settings: Settings) -> Celery:
    app = Celery("openmot", broker=settings.redis_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        worker_prefetch_multiplier=1,
        worker_concurrency=1,
        worker_pool="solo",
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": settings.lock_timeout_seconds},
    )
    return app


def make_enqueue(app: Celery) -> Callable[[str], None]:
    def enqueue(job_id: str) -> None:
        app.send_task(TASK_NAME, args=[job_id])

    return enqueue


def _frame_count(frames_dir: Path) -> int:
    if not frames_dir.is_dir():
        return 0
    return sum(1 for entry in frames_dir.iterdir() if entry.is_file())


def _record_outcome(metrics: Metrics, outcome: ProcessOutcome, elapsed_seconds: float, frames: int) -> None:
    metrics.jobs.labels(outcome=outcome.value).inc()
    if outcome is not ProcessOutcome.SKIPPED:
        metrics.job_duration.observe(elapsed_seconds)
    if outcome is ProcessOutcome.SUCCEEDED:
        metrics.frames_processed.inc(frames)


def register_task(
    app: Celery,
    context: WorkerContext,
    metrics: Metrics | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    @app.task(name=TASK_NAME, shared=False)
    def process(job_id: str) -> str:
        if metrics is None:
            return process_job(context, job_id).value
        frames = _frame_count(context.layout.frames_dir(job_id))
        started = monotonic()
        outcome = process_job(context, job_id)
        _record_outcome(metrics, outcome, monotonic() - started, frames)
        return outcome.value
