import io
import json
import os
import subprocess
import tarfile
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from inference.artifacts import BUILD_COMMAND
from tests.test_tensorrt_worker_e2e import (
    COMPOSE_FILES,
    FRAME_COUNT,
    FRAMES,
    ROOT,
    TENSORRT_IMAGE,
    WEIGHTS_VOLUME,
    _docker,
    _skip_reason,
    _text,
    _wait_until,
)

PROJECT = "openmot-trt-e2e-int8"
ENGINES_VOLUME = "openmot-trt-e2e-int8-engines"
FRAMES_VOLUME = "openmot-trt-e2e-int8-frames"
API_PORT = int(os.environ.get("OPENMOT_TRT_E2E_INT8_API_PORT", "18002"))
BASE_URL = f"http://127.0.0.1:{API_PORT}"
HOST_ENGINES = ROOT / "models" / "engines"

pytestmark = [pytest.mark.integration, pytest.mark.gpu]


def _compose(
    *arguments: str, engine_file: str, precision: str | None = "int8", check: bool = True
) -> subprocess.CompletedProcess:
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("OPENMOT_")},
        "OPENMOT_API_PORT": str(API_PORT),
        "OPENMOT_ENGINES_VOLUME": ENGINES_VOLUME,
        "OPENMOT_ENGINE_FILE": engine_file,
    }
    if precision is not None:
        environment["OPENMOT_PRECISION"] = precision
    files = [part for file in COMPOSE_FILES for part in ("-f", str(file))]
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *files, *arguments], capture_output=True, check=check, env=environment
    )


def _host_int8_engine(directory: Path = HOST_ENGINES) -> Path | None:
    engines = sorted(directory.glob("*-int8.engine"))
    return engines[-1] if engines and Path(f"{engines[-1]}.json").is_file() else None


def _copy_engine_into_volume(engine: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for path in (engine, Path(f"{engine}.json")):
            bundle.add(path, arcname=path.name)
    _docker("volume", "create", ENGINES_VOLUME)
    _docker(
        "run", "-i", "--rm", "-v", f"{ENGINES_VOLUME}:/models/engines", "--entrypoint", "tar",
        TENSORRT_IMAGE, "x", "-C", "/models/engines", input=buffer.getvalue(),
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
        f"python -m tracking.run /frames --output /tmp/r{index}.txt --backend tensorrt --precision int8 "
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
    engine = _host_int8_engine()
    if engine is None:
        pytest.skip(f"build the INT8 engine first: {BUILD_COMMAND} --precision int8 (none in {HOST_ENGINES})")
    _compose("build", "worker", engine_file=engine.name)
    _copy_engine_into_volume(engine)
    _fill_frames_volume()
    try:
        yield engine.name
    finally:
        _compose("down", "-v", engine_file=engine.name, check=False)
        _docker("volume", "rm", FRAMES_VOLUME, ENGINES_VOLUME, check=False)


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
    archive = tmp_path_factory.mktemp("trt-int8-e2e") / "frames.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for number in range(1, FRAME_COUNT + 1):
            bundle.write(FRAMES / f"{number:06d}.jpg", f"{number:06d}.jpg")
    return archive


def test_the_int8_engine_is_repeatable_so_byte_comparison_is_meaningful(engine_file: str) -> None:
    runs = _cli_baseline(engine_file, runs=3)
    assert len({result for result, _ in runs}) == 1
    assert len({meta for _, meta in runs}) == 1


def test_a_job_on_the_int8_worker_matches_the_command_line_with_the_same_engine(
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
    print(f"\nINT8 worker job on {FRAME_COUNT} MOT17-09 frames took {elapsed:.1f}s")

    served_result = httpx.get(f"{BASE_URL}/jobs/{job_id}/result", timeout=30).content
    served_meta = _compose(
        "exec", "-T", "worker", "cat", f"/data/storage/jobs/{job_id}/result.meta.json", engine_file=engine_file
    ).stdout
    baseline_result, baseline_meta = _cli_baseline(engine_file)[0]
    assert served_result == baseline_result
    assert served_meta == baseline_meta
    metadata = json.loads(served_meta)
    assert metadata["backend"] == "tensorrt" and metadata["precision"] == "int8"


def test_the_worker_logs_the_int8_precision_and_engine_path(stack: None, engine_file: str) -> None:
    logs = _text(_compose("logs", "worker", engine_file=engine_file))
    assert f"detector: backend=tensorrt precision=int8 engine=/models/engines/{engine_file}" in logs


def test_an_int8_engine_without_the_precision_setting_stops_the_worker_and_names_the_mismatch(
    engine_file: str,
) -> None:
    result = _compose("run", "--rm", "--no-deps", "worker", engine_file=engine_file, precision=None, check=False)
    output = _text(result)
    assert result.returncode != 0
    assert "precision" in output and "int8" in output and "fp16" in output
