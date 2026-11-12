from pathlib import Path

import cv2
import httpx
import numpy as np
from celery import Celery
from prometheus_client import CollectorRegistry

from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.record import new_job
from jobs.settings import Settings
from metrics.server import MetricsServer, start_metrics_server
from tracking.env_check import CheckOutcome, EnvReport
from worker.celery_app import TASK_NAME
from worker.run import main
from worker.startup import StartupDependencies

LOOPBACK = "127.0.0.1"
PASSING = CheckOutcome(EnvReport("Fake GPU", "555.00", "2.7.1+cu128", "12.8", True), ())
FAILING = CheckOutcome(
    EnvReport("n/a", "n/a", "2.7.1+cpu", None, False), ("CUDA is not available to PyTorch (2.7.1+cpu)",)
)


class Recorder:
    def __init__(self, outcome: CheckOutcome = PASSING, metrics_result: MetricsServer | None = None) -> None:
        self.outcome = outcome
        self.metrics_result = metrics_result
        self.store = InMemoryJobStore()
        self.events: list[str] = []
        self.ports: list[int] = []
        self.registries: list[CollectorRegistry] = []
        self.apps: list[Celery] = []

    def dependencies(self) -> StartupDependencies:
        def check() -> CheckOutcome:
            return self.outcome

        def load(weights: Path, precision: str, device: str):  # type: ignore[no-untyped-def]
            return lambda image: np.array([[10.0, 10.0, 50.0, 90.0, 0.9]])

        def make_store(settings: Settings) -> InMemoryJobStore:
            return self.store

        return StartupDependencies(check, load, make_store)

    def start_metrics(self, port: int, registry: CollectorRegistry) -> MetricsServer | None:
        self.events.append("metrics")
        self.ports.append(port)
        self.registries.append(registry)
        return self.metrics_result

    def serve(self, app: Celery) -> None:
        self.events.append("serve")
        self.apps.append(app)


def _environ(tmp_path: Path) -> dict[str, str]:
    return {
        "OPENMOT_STORAGE_DIR": str(tmp_path / "storage"),
        "OPENMOT_WEIGHTS": "/weights/yolox_x.pth",
        "OPENMOT_LOCK_TIMEOUT_SECONDS": "900",
        "OPENMOT_TASK_TIME_LIMIT_SECONDS": "600",
    }


def _run(tmp_path: Path, recorder: Recorder, extra: dict[str, str] | None = None) -> int:
    return main(
        _environ(tmp_path) | (extra or {}),
        recorder.dependencies(),
        recorder.serve,
        metrics_server=recorder.start_metrics,
    )


def test_the_metrics_server_starts_after_a_healthy_start_and_before_serving(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, recorder) == 0
    assert recorder.events == ["metrics", "serve"]
    assert recorder.ports == [9102]


def test_the_worker_metrics_port_comes_from_the_settings(tmp_path: Path) -> None:
    recorder = Recorder()
    _run(tmp_path, recorder, {"OPENMOT_WORKER_METRICS_PORT": "19102"})
    assert recorder.ports == [19102]


def test_a_worker_that_cannot_start_serves_no_metrics(tmp_path: Path) -> None:
    recorder = Recorder(outcome=FAILING)
    assert _run(tmp_path, recorder) == 1
    assert recorder.events == []


def test_bad_settings_start_no_metrics_server(tmp_path: Path) -> None:
    recorder = Recorder()
    assert _run(tmp_path, recorder, {"OPENMOT_WORKER_METRICS_PORT": "not-a-port"}) == 2
    assert recorder.events == []


def test_a_metrics_server_that_cannot_bind_does_not_stop_the_worker(tmp_path: Path) -> None:
    recorder = Recorder(metrics_result=None)
    assert _run(tmp_path, recorder) == 0
    assert recorder.events == ["metrics", "serve"]


def test_a_job_run_by_the_served_worker_shows_up_on_a_real_metrics_port(tmp_path: Path) -> None:
    recorder = Recorder()
    layout = StorageLayout(tmp_path / "storage")
    frames = layout.frames_dir("job-1")
    frames.mkdir(parents=True)
    for number in range(1, 4):
        cv2.imwrite(str(frames / f"{number:06d}.jpg"), np.full((32, 48, 3), 90, np.uint8))
    recorder.store.create(new_job("job-1", now=1.0))
    started: list[MetricsServer] = []
    scraped: list[str] = []

    def start(port: int, registry: CollectorRegistry) -> MetricsServer | None:
        server = start_metrics_server(0, registry, host=LOOPBACK)
        assert server is not None
        started.append(server)
        return server

    def serve(app: Celery) -> None:
        app.tasks[TASK_NAME].apply(args=["job-1"]).get()
        scraped.append(httpx.get(f"http://{LOOPBACK}:{started[0].port}/metrics").text)

    try:
        code = main(_environ(tmp_path), recorder.dependencies(), serve, metrics_server=start)
    finally:
        for server in started:
            server.close()
    assert code == 0
    assert 'openmot_jobs_total{outcome="succeeded"} 1.0' in scraped[0]
    assert "openmot_frames_processed_total 3.0" in scraped[0]
