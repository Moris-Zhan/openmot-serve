import json
import os
import shutil
import subprocess
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "docker" / "docker-compose.yml"
WEIGHTS = Path(os.environ.get("OPENMOT_WEIGHTS_FILE", ROOT / "models" / "yolox_x.pth"))
FRAMES = ROOT / "data" / "MOT17" / "train" / "MOT17-09-FRCNN" / "img1"
EXTRA_COMPOSE_FILE = os.environ.get("OPENMOT_E2E_COMPOSE_OVERRIDE")
PROJECT = "openmot-e2e"
API_PORT = int(os.environ.get("OPENMOT_E2E_API_PORT", "18000"))
BASE_URL = f"http://127.0.0.1:{API_PORT}"
FRAME_COUNT = 60
BUILD_TIMEOUT_SECONDS = 1800
HEALTHY_TIMEOUT_SECONDS = 300
JOB_TIMEOUT_SECONDS = 300

pytestmark = pytest.mark.integration


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, check=check)


def _skip_reason() -> str | None:
    if shutil.which("docker") is None:
        return "the docker CLI is not installed"
    if _docker("info", "--format", "{{json .Runtimes}}", check=False).returncode != 0:
        return "the docker daemon is not reachable"
    runtimes = _docker("info", "--format", "{{json .Runtimes}}").stdout
    if "nvidia" not in runtimes:
        return "the docker daemon has no nvidia runtime (NVIDIA Container Toolkit)"
    if not WEIGHTS.is_file() and EXTRA_COMPOSE_FILE is None:
        return f"weights not found at {WEIGHTS}"
    if not FRAMES.is_dir():
        return f"MOT17 frames not found at {FRAMES}"
    return None


def _compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    files = ["-f", str(COMPOSE_FILE)] + (["-f", EXTRA_COMPOSE_FILE] if EXTRA_COMPOSE_FILE else [])
    environment = {**os.environ, "OPENMOT_API_PORT": str(API_PORT), "OPENMOT_WEIGHTS_FILE": str(WEIGHTS)}
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *files, *arguments],
        capture_output=True,
        text=True,
        check=check,
        env=environment,
    )


def _health(service: str) -> str:
    output = _compose("ps", "--format", "json", service).stdout
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    return rows[0].get("Health", "") if rows else ""


def _wait_until(condition, timeout: float, description: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(2)
    raise AssertionError(f"timed out waiting for {description}")


def _wait_healthy(*services: str) -> None:
    _wait_until(
        lambda: all(_health(name) == "healthy" for name in services), HEALTHY_TIMEOUT_SECONDS, f"{services} healthy"
    )


def _submit(archive: Path) -> str:
    with archive.open("rb") as handle:
        response = httpx.post(f"{BASE_URL}/jobs", files={"file": ("frames.zip", handle)}, timeout=120)
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def _status(job_id: str) -> str:
    return httpx.get(f"{BASE_URL}/jobs/{job_id}", timeout=30).json()["status"]


def _wait_succeeded(job_id: str) -> str:
    _wait_until(lambda: _status(job_id) in ("succeeded", "failed"), JOB_TIMEOUT_SECONDS, f"job {job_id} to finish")
    assert _status(job_id) == "succeeded"
    return httpx.get(f"{BASE_URL}/jobs/{job_id}/result", timeout=30).text


def _assert_motchallenge(result: str) -> None:
    rows = [line.split(",") for line in result.splitlines() if line.strip()]
    assert rows and all(len(row) == 10 for row in rows)
    assert {int(row[0]) for row in rows} <= set(range(1, FRAME_COUNT + 1))


@pytest.fixture(scope="module")
def frames_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    archive = tmp_path_factory.mktemp("e2e") / "frames.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for number in range(1, FRAME_COUNT + 1):
            bundle.write(FRAMES / f"{number:06d}.jpg", f"{number:06d}.jpg")
    return archive


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    _compose("up", "-d", "--build", check=True)
    try:
        _wait_healthy("redis", "api", "worker")
        yield
    finally:
        _compose("down", "-v", check=False)


def test_a_job_goes_through_the_containers_and_survives_recreating_them(stack: None, frames_zip: Path) -> None:
    job_id = _submit(frames_zip)
    result = _wait_succeeded(job_id)
    _assert_motchallenge(result)

    _compose("up", "-d", "--force-recreate", "--no-deps", "api", "worker")
    _wait_healthy("api", "worker")
    assert _status(job_id) == "succeeded"
    assert httpx.get(f"{BASE_URL}/jobs/{job_id}/result", timeout=30).text == result


def test_a_job_queued_before_the_worker_starts_is_processed_afterwards(stack: None, frames_zip: Path) -> None:
    _compose("stop", "worker")
    job_id = _submit(frames_zip)
    time.sleep(5)
    assert _status(job_id) == "queued"

    _compose("start", "worker")
    _wait_healthy("worker")
    _assert_motchallenge(_wait_succeeded(job_id))
