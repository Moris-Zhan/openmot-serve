import time
from collections.abc import Callable, MutableMapping
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from metrics.definitions import Metrics

UNMATCHED_ROUTE = "unmatched"


def _route_template(scope: MutableMapping[str, Any]) -> str:
    route = scope.get("route")
    return getattr(route, "path", UNMATCHED_ROUTE)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics, clock: Callable[[], float] = time.perf_counter) -> None:
        self._app = app
        self._metrics = metrics
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = self._clock()
        status_code = 500

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, capture_status)
        finally:
            method, route = scope["method"], _route_template(scope)
            self._metrics.http_requests.labels(method=method, route=route, status=str(status_code)).inc()
            self._metrics.http_request_duration.labels(method=method, route=route).observe(self._clock() - started)
