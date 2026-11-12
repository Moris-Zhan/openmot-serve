import os
import socket
import sys
import uuid
from collections.abc import Callable, Mapping

import redis
from celery import Celery
from prometheus_client import CollectorRegistry

from jobs.redis_store import RedisJobStore
from jobs.settings import Settings, SettingsError, load_settings
from jobs.store import JobStore
from metrics.definitions import create_metrics
from metrics.server import MetricsServer, start_metrics_server
from worker.celery_app import create_celery, register_task
from worker.startup import StartupDependencies, StartupError, start_worker

MetricsStarter = Callable[[int, CollectorRegistry], MetricsServer | None]


def _redis_store(settings: Settings) -> JobStore:
    return RedisJobStore(redis.Redis.from_url(settings.redis_url, decode_responses=True))


def default_dependencies() -> StartupDependencies:
    from tracking.env_check import EnvProbe, read_torch_report, run_env_check, torch_gpu_matches_cpu
    from tracking.run import load_default_detect_frame

    return StartupDependencies(
        check_environment=lambda: run_env_check(EnvProbe(read_torch_report, torch_gpu_matches_cpu)),
        load_detect_frame=load_default_detect_frame,
        make_store=_redis_store,
    )


def _serve(app: Celery) -> None:
    app.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=INFO"])


def _owner_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def main(
    environ: Mapping[str, str] = os.environ,
    dependencies: StartupDependencies | None = None,
    serve: Callable[[Celery], None] = _serve,
    metrics_server: MetricsStarter | None = None,
) -> int:
    try:
        settings = load_settings(environ)
    except SettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        context = start_worker(settings, dependencies or default_dependencies(), _owner_id())
    except StartupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    metrics = None
    if metrics_server is not None:
        registry = CollectorRegistry()
        metrics = create_metrics(registry)
        metrics_server(settings.worker_metrics_port, registry)
    app = create_celery(settings)
    register_task(app, context, metrics)
    serve(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(metrics_server=start_metrics_server))
