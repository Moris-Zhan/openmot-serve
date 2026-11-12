import io
import json
import os
import shutil
import socket
import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKER_DIR = ROOT / "deploy" / "docker"
PROJECT = "openmot-obs-e2e"
API_PORT = int(os.environ.get("OPENMOT_E2E_API_PORT", "18000"))
PROMETHEUS_PORT = int(os.environ.get("OPENMOT_E2E_PROMETHEUS_PORT", "19090"))
GRAFANA_PORT = int(os.environ.get("OPENMOT_E2E_GRAFANA_PORT", "13000"))
GRAFANA_PASSWORD = "e2e-test-password"
API_URL = f"http://127.0.0.1:{API_PORT}"
PROMETHEUS_URL = f"http://127.0.0.1:{PROMETHEUS_PORT}"
GRAFANA_URL = f"http://127.0.0.1:{GRAFANA_PORT}"
STARTED_SERVICES = ("redis", "api", "prometheus", "grafana")
BUILD_TIMEOUT_SECONDS = 1800
READY_TIMEOUT_SECONDS = 180
SCRAPE_TIMEOUT_SECONDS = 120

pytestmark = pytest.mark.integration


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, check=check)


def _skip_reason() -> str | None:
    if shutil.which("docker") is None:
        return "the docker CLI is not installed"
    if _docker("info", check=False).returncode != 0:
        return "the docker daemon is not reachable"
    if _docker("compose", "version", check=False).returncode != 0:
        return "the docker compose plugin is not available"
    return None


def _compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "OPENMOT_API_PORT": str(API_PORT),
        "OPENMOT_PROMETHEUS_PORT": str(PROMETHEUS_PORT),
        "OPENMOT_GRAFANA_PORT": str(GRAFANA_PORT),
        "GRAFANA_ADMIN_PASSWORD": GRAFANA_PASSWORD,
    }
    files = ["-f", str(DOCKER_DIR / "docker-compose.yml"), "-f", str(DOCKER_DIR / "docker-compose.monitoring.yml")]
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *files, *arguments],
        capture_output=True,
        text=True,
        check=check,
        env=environment,
        timeout=BUILD_TIMEOUT_SECONDS,
    )


def _wait_until(condition: Callable[[], bool], timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            pass
        time.sleep(3)
    raise AssertionError(f"timed out waiting for {description}")


def _prometheus(path: str, **params: str) -> dict:
    response = httpx.get(f"{PROMETHEUS_URL}{path}", params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def _query(expression: str) -> list[dict]:
    body = _prometheus("/api/v1/query", query=expression)
    assert body["status"] == "success", body
    return body["data"]["result"]


def _scalar(expression: str) -> float | None:
    result = _query(expression)
    return float(result[0]["value"][1]) if result else None


def _targets() -> dict[str, str]:
    active = _prometheus("/api/v1/targets")["data"]["activeTargets"]
    return {target["labels"]["job"]: target["health"] for target in active}


def _frames_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for number in range(1, 4):
            ok, encoded = cv2.imencode(".jpg", np.full((32, 48, 3), 90, np.uint8))
            assert ok
            bundle.writestr(f"{number:06d}.jpg", encoded.tobytes())
    return buffer.getvalue()


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    _compose("up", "-d", "--build", *STARTED_SERVICES)
    try:
        _wait_until(lambda: httpx.get(f"{API_URL}/health", timeout=5).status_code == 200, READY_TIMEOUT_SECONDS, "api")
        _wait_until(lambda: _targets().get("openmot-api") == "up", SCRAPE_TIMEOUT_SECONDS, "the api target to be up")
        yield
    finally:
        _compose("down", "-v", check=False)


def test_prometheus_scrapes_the_api_and_reports_the_missing_worker_as_down(stack: None) -> None:
    targets = _targets()
    assert targets["openmot-api"] == "up"
    assert targets["openmot-worker"] == "down"


def test_requests_to_the_api_show_up_as_metrics_by_route_template(stack: None) -> None:
    for _ in range(3):
        assert httpx.get(f"{API_URL}/health", timeout=5).status_code == 200
    assert httpx.get(f"{API_URL}/jobs/does-not-exist", timeout=5).status_code == 404
    _wait_until(
        lambda: (_scalar('sum(openmot_http_requests_total{route="/health",status="200"})') or 0) >= 3,
        SCRAPE_TIMEOUT_SECONDS,
        "the health requests to be scraped",
    )
    assert _scalar('sum(openmot_http_requests_total{route="/jobs/{job_id}",status="404"})') >= 1
    assert _query('openmot_http_requests_total{route=~"/jobs/does-not-exist.*"}') == []


def test_a_job_waiting_without_a_worker_makes_the_queue_depth_one(stack: None) -> None:
    response = httpx.post(f"{API_URL}/jobs", files={"file": ("frames.zip", _frames_zip())}, timeout=60)
    assert response.status_code == 202, response.text
    _wait_until(lambda: _scalar("openmot_queue_depth") == 1, SCRAPE_TIMEOUT_SECONDS, "the queue depth to reach 1")


def test_the_metrics_are_not_reachable_from_the_host(stack: None) -> None:
    assert httpx.get(f"{API_URL}/metrics", timeout=5).status_code == 404
    for port in (9101, 9102):
        with socket.socket() as probe:
            probe.settimeout(2)
            assert probe.connect_ex(("127.0.0.1", port)) != 0, f"port {port} is reachable from the host"


def test_prometheus_loaded_the_alert_rules(stack: None) -> None:
    groups = _prometheus("/api/v1/rules")["data"]["groups"]
    alerts = {rule["name"] for group in groups for rule in group["rules"]}
    assert alerts == {"ApiDown", "WorkerDown", "HighJobFailureRatio", "JobQueueBacklog", "HighApiErrorRatio"}
    _wait_until(
        lambda: any(
            rule["name"] == "WorkerDown" and rule["state"] in ("pending", "firing")
            for group in _prometheus("/api/v1/rules")["data"]["groups"]
            for rule in group["rules"]
        ),
        SCRAPE_TIMEOUT_SECONDS,
        "WorkerDown to leave the inactive state",
    )


def _grafana(path: str) -> httpx.Response:
    return httpx.get(f"{GRAFANA_URL}{path}", auth=("admin", GRAFANA_PASSWORD), timeout=15)


def test_grafana_provisions_the_datasource_and_the_dashboard_without_manual_steps(stack: None) -> None:
    _wait_until(lambda: _grafana("/api/health").status_code == 200, READY_TIMEOUT_SECONDS, "grafana")
    datasources = _grafana("/api/datasources").json()
    assert [(source["type"], source["url"]) for source in datasources] == [("prometheus", "http://prometheus:9090")]
    _wait_until(
        lambda: any(hit["uid"] == "openmot-overview" for hit in _grafana("/api/search?query=openmot").json()),
        READY_TIMEOUT_SECONDS,
        "the dashboard to be provisioned",
    )


def test_every_dashboard_query_is_accepted_by_prometheus(stack: None) -> None:
    dashboard = json.loads((ROOT / "monitoring" / "grafana" / "dashboards" / "openmot.json").read_text("utf-8"))
    served = _grafana("/api/dashboards/uid/openmot-overview").json()["dashboard"]
    assert [panel["title"] for panel in served["panels"]] == [panel["title"] for panel in dashboard["panels"]]
    for panel in served["panels"]:
        for target in panel["targets"]:
            assert _prometheus("/api/v1/query", query=target["expr"])["status"] == "success", panel["title"]
