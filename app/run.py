import os
import sys
from collections.abc import Callable, Mapping

import redis
import uvicorn
from fastapi import FastAPI
from prometheus_client import CollectorRegistry

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout
from jobs.redis_store import RedisJobStore
from jobs.settings import Settings, SettingsError, load_settings
from metrics.definitions import Metrics, create_metrics
from metrics.queue import ListLength, QueueDepthCollector, dedicated_redis_client, redis_queue_depth
from metrics.server import MetricsServer, start_metrics_server
from worker.celery_app import create_celery, make_enqueue

MetricsStarter = Callable[[int, CollectorRegistry], MetricsServer | None]

API_HOST = "0.0.0.0"
API_PORT = 8000


def _connect(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


def build_app(settings: Settings, redis_client: redis.Redis, metrics: Metrics | None = None) -> FastAPI:
    limits = UploadLimits(settings.max_upload_bytes, settings.max_unpacked_bytes)
    return create_app(
        RedisJobStore(redis_client),
        StorageLayout(settings.storage_dir),
        limits,
        make_enqueue(create_celery(settings)),
        lambda: redis_client.ping(),
        metrics=metrics,
    )


def _instrumentation(
    settings: Settings, queue_client_factory: Callable[[str], ListLength]
) -> tuple[CollectorRegistry, Metrics]:
    registry = CollectorRegistry()
    metrics = create_metrics(registry)
    queue_name = create_celery(settings).conf.task_default_queue
    depth = redis_queue_depth(queue_client_factory(settings.redis_url), queue_name)
    registry.register(QueueDepthCollector(depth))
    return registry, metrics


def _serve(app: FastAPI) -> None:
    uvicorn.run(app, host=API_HOST, port=API_PORT)


def main(
    environ: Mapping[str, str] = os.environ,
    serve: Callable[[FastAPI], None] = _serve,
    redis_factory: Callable[[str], redis.Redis] = _connect,
    metrics_server: MetricsStarter | None = None,
    queue_client_factory: Callable[[str], ListLength] = dedicated_redis_client,
) -> int:
    try:
        settings = load_settings(environ)
    except SettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    metrics = None
    if metrics_server is not None:
        registry, metrics = _instrumentation(settings, queue_client_factory)
        metrics_server(settings.api_metrics_port, registry)
    serve(build_app(settings, redis_factory(settings.redis_url), metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(metrics_server=start_metrics_server))
