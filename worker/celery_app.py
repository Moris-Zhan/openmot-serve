from collections.abc import Callable

from celery import Celery

from jobs.settings import Settings
from worker.processing import WorkerContext, process_job

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


def register_task(app: Celery, context: WorkerContext) -> None:
    @app.task(name=TASK_NAME, shared=False)
    def process(job_id: str) -> str:
        return process_job(context, job_id).value
