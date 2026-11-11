import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from k8s_cluster import FRAMES, NAMESPACE, kubectl, real_probe, skip_reason
from k8s_manifests import K3S_OVERLAY_DIR

LOCAL_PORT = 18080
BASE_URL = f"http://127.0.0.1:{LOCAL_PORT}"
SHORT_FRAME_COUNT = 60
LONG_FRAME_COUNT = 200
ROLLOUT_TIMEOUT = "600s"
POLL_TIMEOUT_SECONDS = 600
JOB_TIMEOUT_SECONDS = 600

pytestmark = pytest.mark.integration


class ApiForward:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def restart(self) -> None:
        self.stop()
        self._process = subprocess.Popen(
            ["kubectl", "-n", NAMESPACE, "port-forward", "svc/api", f"{LOCAL_PORT}:8000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_until(_api_is_healthy, POLL_TIMEOUT_SECONDS, "the api to answer /health through the port-forward")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=30)
            self._process = None


def _wait_until(condition: Callable[[], bool], timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {description}")


def _api_is_healthy() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/health", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


def _pod_names(workload: str) -> set[str]:
    output = kubectl("-n", NAMESPACE, "get", "pods", "-l", f"app.kubernetes.io/name={workload}", "-o", "name").stdout
    return set(output.split())


def _pod_is_ready(workload: str) -> bool:
    template = "{.items[*].status.containerStatuses[*].ready}"
    output = kubectl(
        "-n", NAMESPACE, "get", "pods", "-l", f"app.kubernetes.io/name={workload}", "-o", f"jsonpath={template}"
    ).stdout
    return output.split() == ["true"]


def _replace_pods(*workloads: str) -> None:
    old = {workload: _pod_names(workload) for workload in workloads}
    for workload in workloads:
        kubectl("-n", NAMESPACE, "delete", "pod", "-l", f"app.kubernetes.io/name={workload}", "--wait=false")
    for workload in workloads:
        _wait_until(
            lambda workload=workload: _pod_names(workload).isdisjoint(old[workload]) and _pod_is_ready(workload),
            POLL_TIMEOUT_SECONDS,
            f"a replacement {workload} pod to become ready",
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


def _make_zip(directory: Path, frame_count: int) -> Path:
    archive = directory / f"frames-{frame_count}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for number in range(1, frame_count + 1):
            bundle.write(FRAMES / f"{number:06d}.jpg", f"{number:06d}.jpg")
    return archive


@pytest.fixture(scope="module")
def archives(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    directory = tmp_path_factory.mktemp("k8s-e2e")
    if not FRAMES.is_dir():
        return {}
    return {count: _make_zip(directory, count) for count in (SHORT_FRAME_COUNT, LONG_FRAME_COUNT)}


@pytest.fixture(scope="module")
def forward() -> Iterator[ApiForward]:
    reason = skip_reason(real_probe())
    if reason is not None:
        pytest.skip(reason)
    kubectl("apply", "-k", str(K3S_OVERLAY_DIR))
    api_forward = ApiForward()
    try:
        kubectl("-n", NAMESPACE, "rollout", "status", "statefulset/redis", f"--timeout={ROLLOUT_TIMEOUT}")
        kubectl("-n", NAMESPACE, "wait", "--for=condition=available", "deployment/api", f"--timeout={ROLLOUT_TIMEOUT}")
        kubectl("-n", NAMESPACE, "wait", "--for=condition=available", "deployment/worker", f"--timeout={ROLLOUT_TIMEOUT}")
        api_forward.restart()
        yield api_forward
    finally:
        api_forward.stop()
        kubectl("delete", "-k", str(K3S_OVERLAY_DIR), "--wait=true", check=False)


def test_a_job_succeeds_and_survives_recreating_the_api_and_worker_pods(forward: ApiForward, archives: dict[int, Path]) -> None:
    job_id = _submit(archives[SHORT_FRAME_COUNT])
    result = _wait_succeeded(job_id)
    assert result.strip()

    _replace_pods("api", "worker")
    forward.restart()
    assert _status(job_id) == "succeeded"
    assert httpx.get(f"{BASE_URL}/jobs/{job_id}/result", timeout=30).text == result


def test_the_job_status_survives_recreating_the_redis_pod(forward: ApiForward, archives: dict[int, Path]) -> None:
    job_id = _submit(archives[SHORT_FRAME_COUNT])
    _wait_succeeded(job_id)

    _replace_pods("redis")
    _wait_until(_api_is_healthy, POLL_TIMEOUT_SECONDS, "the api to see the recreated redis")
    assert _status(job_id) == "succeeded"


def test_deleting_the_worker_mid_job_still_ends_in_the_same_result(forward: ApiForward, archives: dict[int, Path]) -> None:
    baseline = _wait_succeeded(_submit(archives[LONG_FRAME_COUNT]))

    job_id = _submit(archives[LONG_FRAME_COUNT])
    _wait_until(lambda: _status(job_id) == "running", POLL_TIMEOUT_SECONDS, f"job {job_id} to start running")
    _replace_pods("worker")
    forward.restart()
    assert _wait_succeeded(job_id) == baseline
