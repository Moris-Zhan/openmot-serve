import os
import sys
from collections.abc import Callable, Mapping

from celery import Celery

from jobs.settings import Settings, load_settings
from worker.celery_app import create_celery

PING_TIMEOUT_SECONDS = 5.0


def main(
    environ: Mapping[str, str] = os.environ,
    make_app: Callable[[Settings], Celery] = create_celery,
) -> int:
    try:
        replies = make_app(load_settings(environ)).control.ping(timeout=PING_TIMEOUT_SECONDS)
    except Exception as error:
        print(f"unhealthy: {error}", file=sys.stderr)
        return 1
    if not replies:
        print("unhealthy: no worker answered the ping", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
