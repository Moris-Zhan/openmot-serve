from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from metrics.definitions import Metrics, create_metrics
from metrics.http import MetricsMiddleware

LIMITS = UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=2_000_000)
REQUESTS = "openmot_http_requests_total"


def _broken_queue() -> None:
    raise ConnectionError("redis is down")


def _measured_client(
    tmp_path: Path, ping_queue: Callable[[], None] = lambda: None
) -> tuple[TestClient, CollectorRegistry]:
    registry = CollectorRegistry()
    app = create_app(
        InMemoryJobStore(),
        StorageLayout(tmp_path / "storage"),
        LIMITS,
        lambda job_id: None,
        ping_queue,
        metrics=create_metrics(registry),
    )
    return TestClient(app), registry


def test_a_request_to_a_templated_route_is_counted_under_the_template(tmp_path: Path) -> None:
    client, registry = _measured_client(tmp_path)
    assert client.get("/jobs/abc123").status_code == 404
    labels = {"method": "GET", "route": "/jobs/{job_id}", "status": "404"}
    assert registry.get_sample_value(REQUESTS, labels) == 1
    assert "abc123" not in generate_latest(registry).decode()


def test_unknown_paths_share_one_fixed_route_label(tmp_path: Path) -> None:
    client, registry = _measured_client(tmp_path)
    client.get("/no-such-path-1")
    client.get("/no-such-path-2/deeper")
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    assert registry.get_sample_value(REQUESTS, labels) == 2
    routes = {
        sample.labels["route"] for family in registry.collect() for sample in family.samples if "route" in sample.labels
    }
    assert routes == {"unmatched"}


def test_a_server_error_is_counted_with_its_real_status_code(tmp_path: Path) -> None:
    client, registry = _measured_client(tmp_path, _broken_queue)
    assert client.get("/health").status_code == 503
    assert registry.get_sample_value(REQUESTS, {"method": "GET", "route": "/health", "status": "503"}) == 1


def test_the_public_port_does_not_serve_metrics(tmp_path: Path) -> None:
    client, _ = _measured_client(tmp_path)
    assert client.get("/metrics").status_code == 404


def test_the_app_works_without_metrics(tmp_path: Path) -> None:
    app = create_app(
        InMemoryJobStore(), StorageLayout(tmp_path / "storage"), LIMITS, lambda job_id: None, lambda: None
    )
    assert TestClient(app).get("/health").status_code == 200


def _app_with(metrics: Metrics, clock: Callable[[], float]) -> FastAPI:
    app = FastAPI()

    @app.get("/slow")
    def slow() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("unhandled")

    app.add_middleware(MetricsMiddleware, metrics=metrics, clock=clock)
    return app


def test_the_request_duration_is_measured_with_the_injected_clock() -> None:
    registry = CollectorRegistry()
    ticks = iter([10.0, 10.25])
    client = TestClient(_app_with(create_metrics(registry), lambda: next(ticks)))
    client.get("/slow")
    labels = {"method": "GET", "route": "/slow"}
    assert registry.get_sample_value("openmot_http_request_duration_seconds_sum", labels) == pytest.approx(0.25)
    assert registry.get_sample_value("openmot_http_request_duration_seconds_count", labels) == 1


def test_an_unhandled_error_is_counted_as_500_and_still_propagates() -> None:
    registry = CollectorRegistry()
    client = TestClient(_app_with(create_metrics(registry), lambda: 0.0), raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500
    assert registry.get_sample_value(REQUESTS, {"method": "GET", "route": "/boom", "status": "500"}) == 1
    with pytest.raises(RuntimeError, match="unhandled"):
        TestClient(_app_with(create_metrics(CollectorRegistry()), lambda: 0.0)).get("/boom")
