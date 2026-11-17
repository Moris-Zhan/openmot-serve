import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "deploy" / "docker"
COMPOSE_FILES = [
    COMPOSE_DIR / "docker-compose.yml",
    COMPOSE_DIR / "docker-compose.weights-volume.yml",
    COMPOSE_DIR / "docker-compose.tensorrt.yml",
]
FRAMES = ROOT / "data" / "MOT17" / "train" / "MOT17-09-FRCNN" / "img1"
PROJECT = "openmot-trt-e2e"
ENGINES_VOLUME = "openmot-trt-e2e-engines"
FRAMES_VOLUME = "openmot-trt-e2e-frames"
DEFAULT_IMAGE = "openmot-trt-e2e-default:latest"
TENSORRT_IMAGE = "openmot-worker:tensorrt"
WEIGHTS_VOLUME = "openmot-weights"
API_PORT = int(os.environ.get("OPENMOT_TRT_E2E_API_PORT", "18001"))
BASE_URL = f"http://127.0.0.1:{API_PORT}"
FRAME_COUNT = 60
KEEP_ENGINES = os.environ.get("OPENMOT_TRT_E2E_KEEP_ENGINES") == "1"

pytestmark = [pytest.mark.integration, pytest.mark.gpu]


def _docker(*arguments: str, check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *arguments], capture_output=True, check=check, input=input)


def _text(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).decode(errors="replace")


def _skip_reason() -> str | None:
    if shutil.which("docker") is None:
        return "the docker CLI is not installed"
    if _docker("info", "--format", "{{json .Runtimes}}", check=False).returncode != 0:
        return "the docker daemon is not reachable"
    if b"nvidia" not in _docker("info", "--format", "{{json .Runtimes}}").stdout:
        return "the docker daemon has no nvidia runtime"
    if _docker("volume", "inspect", WEIGHTS_VOLUME, check=False).returncode != 0:
        return f"the weights volume {WEIGHTS_VOLUME} does not exist"
    if not FRAMES.is_dir():
        return f"MOT17 frames not found at {FRAMES}"
    return None


def _compose(*arguments: str, engine_file: str = "pending.engine", check: bool = True) -> subprocess.CompletedProcess:
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("OPENMOT_")},
        "OPENMOT_API_PORT": str(API_PORT),
        "OPENMOT_ENGINES_VOLUME": ENGINES_VOLUME,
        "OPENMOT_ENGINE_FILE": engine_file,
    }
    files = [part for file in COMPOSE_FILES for part in ("-f", str(file))]
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *files, *arguments], capture_output=True, check=check, env=environment
    )


def _engine_names() -> list[str]:
    listing = _docker("run", "--rm", "-v", f"{ENGINES_VOLUME}:/e:ro", "--entrypoint", "ls", TENSORRT_IMAGE, "/e")
    return [name for name in listing.stdout.decode().split() if name.endswith(".engine")]


def _build_engine() -> None:
    for module in ("inference.export_onnx", "inference.build_engine"):
        _compose(
            "--profile", "tools", "run", "--rm", "engine-builder",
            "-m", module, "--weights", "/models/yolox_x.pth", "--output-dir", "/models/engines",
        )  # fmt: skip


def _fill_frames_volume() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for number in range(1, FRAME_COUNT + 1):
            bundle.add(FRAMES / f"{number:06d}.jpg", arcname=f"{number:06d}.jpg")
    _docker("volume", "create", FRAMES_VOLUME)
    _docker(
        "run", "-i", "--rm", "--user", "root", "-v", f"{FRAMES_VOLUME}:/frames", "--entrypoint", "tar",
        TENSORRT_IMAGE, "x", "-C", "/frames", input=buffer.getvalue(),
    )  # fmt: skip


def _cli_baseline(engine_file: str, runs: int = 1) -> list[tuple[bytes, bytes]]:
    script = " ".join(
        f"python -m tracking.run /frames --output /tmp/r{index}.txt --backend tensorrt "
        f"--engine /models/engines/{engine_file} --weights /w/yolox_x.pth >&2;"
        for index in range(runs)
    )
    script += " tar c -C /tmp " + " ".join(f"r{i}.txt r{i}.meta.json" for i in range(runs))
    result = _docker(
        "run", "--rm", "--gpus", "all", "-v", f"{ENGINES_VOLUME}:/models/engines:ro",
        "-v", f"{FRAMES_VOLUME}:/frames:ro", "-v", f"{WEIGHTS_VOLUME}:/w:ro",
        "--entrypoint", "sh", TENSORRT_IMAGE, "-c", script,
    )  # fmt: skip
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as bundle:
        return [
            (bundle.extractfile(f"r{i}.txt").read(), bundle.extractfile(f"r{i}.meta.json").read())  # type: ignore[union-attr]
            for i in range(runs)
        ]


def _wait_until(condition, timeout: float, description: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(2)
    raise AssertionError(f"timed out waiting for {description}")


def _healthy(service: str, engine_file: str) -> bool:
    rows = [
        json.loads(line)
        for line in _compose("ps", "--format", "json", service, engine_file=engine_file).stdout.decode().splitlines()
        if line.strip()
    ]
    return bool(rows) and rows[0].get("Health") == "healthy"


@pytest.fixture(scope="module")
def engine_file() -> Iterator[str]:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    _docker("volume", "create", ENGINES_VOLUME)
    _compose("build", "worker")
    if not _engine_names():
        _build_engine()
    names = _engine_names()
    assert len(names) == 1, names
    _fill_frames_volume()
    try:
        yield names[0]
    finally:
        _compose("down", "-v", check=False)
        volumes = [FRAMES_VOLUME] + ([] if KEEP_ENGINES else [ENGINES_VOLUME])
        _docker("volume", "rm", *volumes, check=False)
        _docker("image", "rm", DEFAULT_IMAGE, check=False)


@pytest.fixture(scope="module")
def stack(engine_file: str) -> Iterator[None]:
    _compose("up", "-d", "redis", "api", "worker", engine_file=engine_file)
    try:
        _wait_until(
            lambda: all(_healthy(name, engine_file) for name in ("redis", "api", "worker")),
            300,
            "the stack to be healthy",
        )
        yield
    finally:
        _compose("logs", "worker", engine_file=engine_file, check=False)


@pytest.fixture(scope="module")
def frames_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    archive = tmp_path_factory.mktemp("trt-e2e") / "frames.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for number in range(1, FRAME_COUNT + 1):
            bundle.write(FRAMES / f"{number:06d}.jpg", f"{number:06d}.jpg")
    return archive


def test_the_engine_is_repeatable_so_byte_comparison_is_meaningful(engine_file: str) -> None:
    runs = _cli_baseline(engine_file, runs=3)
    assert len({result for result, _ in runs}) == 1
    assert len({meta for _, meta in runs}) == 1


def test_a_job_on_the_tensorrt_worker_matches_the_command_line_with_the_same_engine(
    stack: None, engine_file: str, frames_zip: Path
) -> None:
    started = time.monotonic()
    with frames_zip.open("rb") as handle:
        response = httpx.post(f"{BASE_URL}/jobs", files={"file": ("frames.zip", handle)}, timeout=120)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status = lambda: httpx.get(f"{BASE_URL}/jobs/{job_id}", timeout=30).json()["status"]  # noqa: E731
    _wait_until(lambda: status() in ("succeeded", "failed"), 300, f"job {job_id}")
    elapsed = time.monotonic() - started
    assert status() == "succeeded"
    print(f"\nTensorRT worker job on {FRAME_COUNT} MOT17-09 frames took {elapsed:.1f}s")

    served_result = httpx.get(f"{BASE_URL}/jobs/{job_id}/result", timeout=30).content
    served_meta = _compose(
        "exec", "-T", "worker", "cat", f"/data/storage/jobs/{job_id}/result.meta.json", engine_file=engine_file
    ).stdout
    baseline_result, baseline_meta = _cli_baseline(engine_file)[0]
    assert served_result == baseline_result
    assert served_meta == baseline_meta
    assert json.loads(served_meta)["backend"] == "tensorrt"


def test_the_worker_logs_the_backend_precision_and_engine_path(stack: None, engine_file: str) -> None:
    logs = _text(_compose("logs", "worker", engine_file=engine_file))
    assert f"detector: backend=tensorrt precision=fp16 engine=/models/engines/{engine_file}" in logs


def test_a_wrong_engine_file_stops_the_worker_and_names_the_reason(engine_file: str) -> None:
    result = _compose("run", "--rm", "--no-deps", "worker", engine_file="does-not-exist.engine", check=False)
    output = _text(result)
    assert result.returncode != 0
    assert "does-not-exist.engine" in output


def test_the_default_image_with_the_tensorrt_backend_stops_and_names_the_missing_package(engine_file: str) -> None:
    _docker("build", "-f", str(COMPOSE_DIR / "worker.Dockerfile"), "--target", "worker", "-t", DEFAULT_IMAGE, str(ROOT))
    result = _docker(
        "run", "--rm", "--gpus", "all",
        "-e", "OPENMOT_STORAGE_DIR=/data/storage", "-e", "OPENMOT_WEIGHTS=/w/yolox_x.pth",
        "-e", "OPENMOT_BACKEND=tensorrt", "-e", f"OPENMOT_ENGINE=/models/engines/{engine_file}",
        "-v", f"{WEIGHTS_VOLUME}:/w:ro", "-v", f"{ENGINES_VOLUME}:/models/engines:ro",
        DEFAULT_IMAGE, check=False,
    )  # fmt: skip
    output = _text(result)
    assert result.returncode == 1
    assert "tensorrt" in output.lower() and "export" in output
