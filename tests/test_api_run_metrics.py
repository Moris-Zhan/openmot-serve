from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app.run import main
from jobs.settings import load_settings
from metrics.server import MetricsServer, start_metrics_server
from worker.celery_app import create_celery

LOOPBACK = "127.0.0.1"


class StubRedis:
    def ping(self) -> bool:
        return True

    def register_script(self, script: str):  # type: ignore[no-untyped-def]
        return lambda **kwargs: 0


class StubQueueClient:
    def __init__(self, lengths: dict[str, int]) -> None:
        self.lengths = lengths
        self.asked: list[str] = []

    def llen(self, name: str) -> int:
        self.asked.append(name)
        return self.lengths.get(name, 0)


def _environ(tmp_path: Path) -> dict[str, str]:
    return {"OPENMOT_STORAGE_DIR": str(tmp_path / "storage")}


class Recorder:
    def __init__(self, result: MetricsServer | None = None) -> None:
        self.calls: list[tuple[int, CollectorRegistry]] = []
        self.events: list[str] = []
        self.result = result

    def start(self, port: int, registry: CollectorRegistry) -> MetricsServer | None:
        self.calls.append((port, registry))
        self.events.append("metrics")
        return self.result

    def serve(self, app: FastAPI) -> None:
        self.events.append("serve")


def _run(tmp_path: Path, recorder: Recorder, extra: dict[str, str] | None = None, queue=None) -> int:  # type: ignore[no-untyped-def]
    return main(
        _environ(tmp_path) | (extra or {}),
        serve=recorder.serve,
        redis_factory=lambda url: StubRedis(),  # type: ignore[arg-type,return-value]
        metrics_server=recorder.start,
        queue_client_factory=lambda url: queue or StubQueueClient({}),
    )


def test_the_metrics_server_starts_on_the_configured_port_before_serving(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, recorder, {"OPENMOT_API_METRICS_PORT": "19101"}) == 0
    assert [port for port, _ in recorder.calls] == [19101]
    assert recorder.events == ["metrics", "serve"]


def test_the_default_metrics_port_is_used_when_none_is_configured(tmp_path: Path) -> None:
    recorder = Recorder()
    _run(tmp_path, recorder)
    assert [port for port, _ in recorder.calls] == [9101]


def test_a_metrics_server_that_cannot_bind_does_not_stop_the_service(tmp_path: Path) -> None:
    recorder = Recorder(result=None)
    assert _run(tmp_path, recorder) == 0
    assert recorder.events == ["metrics", "serve"]


def test_the_queue_depth_reads_the_celery_default_queue_through_a_dedicated_client(tmp_path: Path) -> None:
    queue_name = create_celery(load_settings(_environ(tmp_path))).conf.task_default_queue
    queue = StubQueueClient({queue_name: 2})
    recorder = Recorder()
    _run(tmp_path, recorder, queue=queue)
    registry = recorder.calls[0][1]
    assert registry.get_sample_value("openmot_queue_depth") == 2
    assert queue.asked == [queue_name]


def test_requests_and_queue_depth_show_up_on_a_real_metrics_port(tmp_path: Path) -> None:
    started: list[MetricsServer] = []

    def start(port: int, registry: CollectorRegistry) -> MetricsServer | None:
        server = start_metrics_server(0, registry, host=LOOPBACK)
        assert server is not None
        started.append(server)
        return server

    scraped: list[str] = []

    def serve(app: FastAPI) -> None:
        assert TestClient(app).get("/health").status_code == 200
        scraped.append(httpx.get(f"http://{LOOPBACK}:{started[0].port}/metrics").text)

    try:
        code = main(
            _environ(tmp_path),
            serve=serve,
            redis_factory=lambda url: StubRedis(),  # type: ignore[arg-type,return-value]
            metrics_server=start,
            queue_client_factory=lambda url: StubQueueClient({"celery": 4}),
        )
    finally:
        for server in started:
            server.close()
    assert code == 0
    assert 'openmot_http_requests_total{method="GET",route="/health",status="200"} 1.0' in scraped[0]
    assert "openmot_queue_depth 4.0" in scraped[0]
