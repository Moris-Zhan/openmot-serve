import io
import json
import os
import secrets
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
    COMPOSE_DIR / "docker-compose.s3.yml",
]
FRAMES = ROOT / "data" / "MOT17" / "train" / "MOT17-09-FRCNN" / "img1"
PROJECT = "openmot-s3-e2e"
FRAMES_VOLUME = "openmot-s3-e2e-frames"
API_IMAGE = "openmot-s3-e2e-api:latest"
WORKER_IMAGE = "openmot-s3-e2e-worker:latest"
WEIGHTS_VOLUME = "openmot-weights"
API_PORT = int(os.environ.get("OPENMOT_S3_E2E_API_PORT", "18003"))
S3_PORT = int(os.environ.get("OPENMOT_S3_E2E_S3_PORT", "19000"))
BASE_URL = f"http://127.0.0.1:{API_PORT}"
S3_URL = f"http://127.0.0.1:{S3_PORT}"
FRAME_COUNT = 60
STORAGE_TARGET = "/data/storage"
ACCESS_KEY = f"e2e{secrets.token_hex(4)}"
SECRET_KEY = secrets.token_hex(16)
BUCKET = f"openmot-e2e-{secrets.token_hex(4)}"
S3_IMAGE_PREFIX = "rustfs/rustfs:"

pytestmark = [pytest.mark.integration, pytest.mark.gpu]


def _docker(*arguments: str, check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *arguments], capture_output=True, check=check, input=input)


def _text(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).decode(errors="replace")


def _s3_image() -> str:
    for line in (COMPOSE_DIR / "docker-compose.s3.yml").read_text().splitlines():
        if line.strip().startswith("image: ") and S3_IMAGE_PREFIX in line:
            return line.split("image:", 1)[1].strip()
    raise AssertionError("no pinned S3 server image in docker-compose.s3.yml")


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
    try:
        import boto3  # noqa: F401
    except ImportError:
        return "boto3 is not installed in the test environment"
    image = _s3_image()
    if _docker("image", "inspect", image, check=False).returncode == 0:
        return None
    if _docker("pull", image, check=False).returncode != 0:
        return f"the S3 server image {image} is not available and could not be pulled"
    return None


def _compose(*arguments: str, check: bool = True, extra: Path | None = None) -> subprocess.CompletedProcess:
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith(("OPENMOT_", "S3_"))},
        "OPENMOT_API_PORT": str(API_PORT),
        "OPENMOT_S3_PORT": str(S3_PORT),
        "OPENMOT_S3_BUCKET": BUCKET,
        "S3_ACCESS_KEY": ACCESS_KEY,
        "S3_SECRET_KEY": SECRET_KEY,
    }
    files = [part for file in [*COMPOSE_FILES, *([extra] if extra else [])] for part in ("-f", str(file))]
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *files, *arguments], capture_output=True, check=check, env=environment
    )


def _s3():  # type: ignore[no-untyped-def]
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=S3_URL,
        region_name="us-east-1",
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(s3={"addressing_style": "path"}),
    )


def _wait_until(condition, timeout: float, description: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(2)
    raise AssertionError(f"timed out waiting for {description}")


def _healthy(service: str, extra: Path) -> bool:
    rows = [
        json.loads(line)
        for line in _compose("ps", "--format", "json", service, extra=extra).stdout.decode().splitlines()
        if line.strip()
    ]
    return bool(rows) and rows[0].get("Health") == "healthy"


def _fill_frames_volume() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for number in range(1, FRAME_COUNT + 1):
            bundle.add(FRAMES / f"{number:06d}.jpg", arcname=f"{number:06d}.jpg")
    _docker("volume", "create", FRAMES_VOLUME)
    _docker(
        "run", "-i", "--rm", "--user", "root", "-v", f"{FRAMES_VOLUME}:/frames", "--entrypoint", "tar",
        WORKER_IMAGE, "x", "-C", "/frames", input=buffer.getvalue(),
    )  # fmt: skip


def _cli_baseline() -> tuple[bytes, bytes]:
    script = (
        "python -m tracking.run /frames --output /tmp/r0.txt --weights /w/yolox_x.pth >&2; "
        "tar c -C /tmp r0.txt r0.meta.json"
    )
    result = _docker(
        "run", "--rm", "--gpus", "all", "-v", f"{FRAMES_VOLUME}:/frames:ro", "-v", f"{WEIGHTS_VOLUME}:/w:ro",
        "--entrypoint", "sh", WORKER_IMAGE, "-c", script,
    )  # fmt: skip
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as bundle:
        return bundle.extractfile("r0.txt").read(), bundle.extractfile("r0.meta.json").read()  # type: ignore[union-attr]


def _storage_volume(service: str, extra: Path) -> str:
    container = _compose("ps", "-q", service, extra=extra).stdout.decode().strip()
    mounts = json.loads(_docker("inspect", "--format", "{{json .Mounts}}", container).stdout)
    return next(mount["Name"] for mount in mounts if mount["Destination"] == STORAGE_TARGET)


def _local_files(service: str, extra: Path) -> list[str]:
    output = _compose("exec", "-T", service, "find", STORAGE_TARGET, "-type", "f", extra=extra).stdout.decode()
    return [line for line in output.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def images_override(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("s3-e2e") / "images.yml"
    path.write_text(f"services:\n  api:\n    image: {API_IMAGE}\n  worker:\n    image: {WORKER_IMAGE}\n")
    return path


@pytest.fixture(scope="module")
def stack(images_override: Path) -> Iterator[Path]:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    try:
        _compose("build", "api", "worker", extra=images_override)
        _fill_frames_volume()
        _compose("up", "-d", "s3", extra=images_override)
        _wait_until(lambda: _healthy("s3", images_override), 120, "the S3 server to be healthy")
        _s3().create_bucket(Bucket=BUCKET)
        _compose("up", "-d", "redis", "api", "worker", extra=images_override)
        _wait_until(
            lambda: all(_healthy(name, images_override) for name in ("redis", "api", "worker")),
            300,
            "the stack to be healthy",
        )
        yield images_override
    finally:
        print(_text(_compose("logs", "api", "worker", extra=images_override, check=False))[-4000:])
        _compose("down", "-v", extra=images_override, check=False)
        _docker("volume", "rm", FRAMES_VOLUME, check=False)
        _docker("image", "rm", API_IMAGE, WORKER_IMAGE, check=False)


@pytest.fixture(scope="module")
def frames_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    archive = tmp_path_factory.mktemp("s3-e2e-frames") / "frames.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for number in range(1, FRAME_COUNT + 1):
            bundle.write(FRAMES / f"{number:06d}.jpg", f"{number:06d}.jpg")
    return archive


@pytest.fixture(scope="module")
def job_id(stack: Path, frames_zip: Path) -> str:
    started = time.monotonic()
    with frames_zip.open("rb") as handle:
        response = httpx.post(f"{BASE_URL}/jobs", files={"file": ("frames.zip", handle)}, timeout=120)
    assert response.status_code == 202, response.text
    submitted = time.monotonic() - started
    identifier = response.json()["job_id"]
    status = lambda: httpx.get(f"{BASE_URL}/jobs/{identifier}", timeout=30).json()["status"]  # noqa: E731
    _wait_until(lambda: status() in ("succeeded", "failed"), 300, f"job {identifier}")
    total = time.monotonic() - started
    print(f"\nS3 job on {FRAME_COUNT} MOT17-09 frames: submit {submitted:.1f}s, succeeded after {total:.1f}s")
    assert status() == "succeeded"
    return identifier


def test_a_job_succeeds_through_the_bucket(job_id: str) -> None:
    assert httpx.get(f"{BASE_URL}/jobs/{job_id}", timeout=30).json()["status"] == "succeeded"


def test_the_result_and_metadata_equal_the_command_line_byte_for_byte(job_id: str, stack: Path) -> None:
    client = _s3()
    stored_result = client.get_object(Bucket=BUCKET, Key=f"jobs/{job_id}/result.txt")["Body"].read()
    stored_meta = client.get_object(Bucket=BUCKET, Key=f"jobs/{job_id}/result.meta.json")["Body"].read()
    served = httpx.get(f"{BASE_URL}/jobs/{job_id}/result", timeout=30).content
    baseline_result, baseline_meta = _cli_baseline()
    assert served == stored_result == baseline_result
    assert stored_meta == baseline_meta


def test_the_bucket_holds_every_frame_and_both_result_objects(job_id: str) -> None:
    listing = _s3().list_objects_v2(Bucket=BUCKET, Prefix=f"jobs/{job_id}/").get("Contents", [])
    keys = [entry["Key"] for entry in listing]
    frames = [key for key in keys if key.startswith(f"jobs/{job_id}/frames/")]
    assert len(frames) == FRAME_COUNT
    assert f"jobs/{job_id}/result.txt" in keys and f"jobs/{job_id}/result.meta.json" in keys


def test_api_and_worker_do_not_share_a_disk(job_id: str, stack: Path) -> None:
    api, worker = _storage_volume("api", stack), _storage_volume("worker", stack)
    assert api != worker
    assert api.startswith(PROJECT) and worker.startswith(PROJECT)


def test_the_local_staging_and_scratch_are_empty(job_id: str, stack: Path) -> None:
    assert _local_files("api", stack) == []
    assert _local_files("worker", stack) == []


def test_health_reports_503_when_the_bucket_is_gone_without_leaking_the_secret(job_id: str, stack: Path) -> None:
    client = _s3()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for entry in page.get("Contents", []):
            client.delete_object(Bucket=BUCKET, Key=entry["Key"])
    client.delete_bucket(Bucket=BUCKET)
    response = httpx.get(f"{BASE_URL}/health", timeout=30)
    assert response.status_code == 503
    assert "object store" in response.text and SECRET_KEY not in response.text
