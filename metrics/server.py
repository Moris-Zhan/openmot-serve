import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from prometheus_client import CollectorRegistry, start_http_server

ALL_INTERFACES = "0.0.0.0"


@dataclass(frozen=True)
class MetricsServer:
    port: int
    close: Callable[[], None]


def start_metrics_server(
    port: int,
    registry: CollectorRegistry,
    host: str = ALL_INTERFACES,
    error_output: TextIO = sys.stderr,
) -> MetricsServer | None:
    try:
        server, _thread = start_http_server(port, addr=host, registry=registry)
    except OSError as error:
        print(f"warning: metrics are not served, cannot bind port {port}: {error}", file=error_output)
        return None

    def close() -> None:
        server.shutdown()
        server.server_close()

    return MetricsServer(port=server.server_port, close=close)
