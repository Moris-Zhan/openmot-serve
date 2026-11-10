import os
import sys
from collections.abc import Callable, Mapping

import redis
import uvicorn
from fastapi import FastAPI

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout
from jobs.redis_store import RedisJobStore
from jobs.settings import Settings, SettingsError, load_settings
from worker.celery_app import create_celery, make_enqueue

API_HOST = "0.0.0.0"
API_PORT = 8000


def _connect(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


def build_app(settings: Settings, redis_client: redis.Redis) -> FastAPI:
    limits = UploadLimits(settings.max_upload_bytes, settings.max_unpacked_bytes)
    return create_app(
        RedisJobStore(redis_client),
        StorageLayout(settings.storage_dir),
        limits,
        make_enqueue(create_celery(settings)),
        lambda: redis_client.ping(),
    )


def _serve(app: FastAPI) -> None:
    uvicorn.run(app, host=API_HOST, port=API_PORT)


def main(
    environ: Mapping[str, str] = os.environ,
    serve: Callable[[FastAPI], None] = _serve,
    redis_factory: Callable[[str], redis.Redis] = _connect,
) -> int:
    try:
        settings = load_settings(environ)
    except SettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    serve(build_app(settings, redis_factory(settings.redis_url)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
