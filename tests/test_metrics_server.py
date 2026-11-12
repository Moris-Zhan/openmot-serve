import io
import socket
from collections.abc import Iterator
from contextlib import closing

import httpx
import pytest
from prometheus_client import CollectorRegistry

from metrics.definitions import create_metrics
from metrics.server import MetricsServer, start_metrics_server

LOOPBACK = "127.0.0.1"


@pytest.fixture
def registry() -> CollectorRegistry:
    registry = CollectorRegistry()
    create_metrics(registry).frames_processed.inc(3)
    return registry


@pytest.fixture
def running(registry: CollectorRegistry) -> Iterator[MetricsServer]:
    server = start_metrics_server(0, registry, host=LOOPBACK, error_output=io.StringIO())
    assert server is not None
    yield server
    server.close()


def test_the_server_answers_metrics_requests_in_the_prometheus_text_format(running: MetricsServer) -> None:
    response = httpx.get(f"http://{LOOPBACK}:{running.port}/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "openmot_frames_processed_total 3.0" in response.text


def test_the_server_reports_the_port_it_is_bound_to(running: MetricsServer) -> None:
    assert running.port > 0


def test_an_occupied_port_is_reported_on_the_error_stream_without_raising(registry: CollectorRegistry) -> None:
    errors = io.StringIO()
    with closing(socket.socket()) as occupant:
        occupant.bind((LOOPBACK, 0))
        occupant.listen()
        port = occupant.getsockname()[1]
        server = start_metrics_server(port, registry, host=LOOPBACK, error_output=errors)
    assert server is None
    message = errors.getvalue()
    assert str(port) in message and "metrics" in message.lower()


def test_closing_the_server_frees_its_port(registry: CollectorRegistry) -> None:
    first = start_metrics_server(0, registry, host=LOOPBACK, error_output=io.StringIO())
    assert first is not None
    port = first.port
    first.close()
    again = start_metrics_server(port, registry, host=LOOPBACK, error_output=io.StringIO())
    assert again is not None
    again.close()
