import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DOCKER_DIR = Path(__file__).resolve().parents[1] / "deploy" / "docker"
BASE = DOCKER_DIR / "docker-compose.yml"
WEIGHTS = DOCKER_DIR / "docker-compose.weights-volume.yml"
OVERRIDE = DOCKER_DIR / "docker-compose.tensorrt.yml"
ENGINE_FILE = "yolox_x-test.engine"

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="the docker CLI is not installed")


def override() -> dict:
    return yaml.safe_load(OVERRIDE.read_text())


def rendered(files: list[Path], engine_file: str | None = ENGINE_FILE, **extra: str) -> dict:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OPENMOT_")}
    if engine_file is not None:
        environment["OPENMOT_ENGINE_FILE"] = engine_file
    environment.update(extra)
    command = ["docker", "compose", *[part for file in files for part in ("-f", str(file))]]
    result = subprocess.run(
        [*command, "--profile", "tools", "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    return json.loads(result.stdout)


def test_the_override_defines_only_the_worker_and_the_tools_profile_builder() -> None:
    assert set(override()["services"]) == {"worker", "engine-builder"}
    assert override()["services"]["engine-builder"]["profiles"] == ["tools"]
    assert "profiles" not in override()["services"]["worker"]


def test_the_worker_builds_the_tensorrt_target_and_selects_the_backend() -> None:
    worker = override()["services"]["worker"]
    assert worker["build"]["target"] == "tensorrt"
    assert worker["image"] == "openmot-worker:tensorrt"
    assert worker["environment"]["OPENMOT_BACKEND"] == "tensorrt"
    assert worker["environment"]["OPENMOT_PRECISION"] == "fp16"
    engine = worker["environment"]["OPENMOT_ENGINE"]
    assert engine.startswith("/models/engines/${OPENMOT_ENGINE_FILE:?")


def test_the_engines_volume_is_external_and_named_by_an_overridable_variable() -> None:
    volume = override()["volumes"]["engines"]
    assert volume["external"] is True
    assert volume["name"] == "${OPENMOT_ENGINES_VOLUME:-openmot-engines}"


def test_the_worker_mounts_the_engines_read_only_and_the_builder_can_write() -> None:
    services = override()["services"]
    worker_mount = next(v for v in services["worker"]["volumes"] if v["target"] == "/models/engines")
    builder_mount = next(v for v in services["engine-builder"]["volumes"] if v["target"] == "/models/engines")
    assert worker_mount["source"] == builder_mount["source"] == "engines"
    assert worker_mount["read_only"] is True
    assert not builder_mount.get("read_only", False)


def test_the_builder_reuses_the_image_runs_python_and_keeps_the_gpu() -> None:
    builder = override()["services"]["engine-builder"]
    assert builder["image"] == "openmot-worker:tensorrt"
    assert builder["entrypoint"] in ("python", ["python"])
    devices = builder["deploy"]["resources"]["reservations"]["devices"]
    assert devices[0]["driver"] == "nvidia" and "gpu" in devices[0]["capabilities"]


def test_the_override_has_no_bind_mounts() -> None:
    for service in override()["services"].values():
        for volume in service.get("volumes", []):
            assert isinstance(volume, dict) and volume["type"] == "volume"


@needs_docker
def test_api_and_redis_are_unchanged_by_the_override() -> None:
    base = rendered([BASE, WEIGHTS], engine_file=None)["services"]
    stacked = rendered([BASE, WEIGHTS, OVERRIDE])["services"]
    assert stacked["api"] == base["api"]
    assert stacked["redis"] == base["redis"]


@needs_docker
def test_the_stacked_worker_has_the_tensorrt_environment_and_a_read_only_engine_mount() -> None:
    worker = rendered([BASE, WEIGHTS, OVERRIDE])["services"]["worker"]
    assert worker["build"]["target"] == "tensorrt"
    assert worker["environment"]["OPENMOT_BACKEND"] == "tensorrt"
    assert worker["environment"]["OPENMOT_PRECISION"] == "fp16"
    assert worker["environment"]["OPENMOT_ENGINE"] == f"/models/engines/{ENGINE_FILE}"
    mounts = {volume["target"]: volume for volume in worker["volumes"]}
    assert mounts["/models/engines"]["read_only"] is True
    assert all(volume["type"] == "volume" for volume in worker["volumes"])


@needs_docker
def test_the_engines_volume_name_can_be_overridden() -> None:
    config = rendered([BASE, WEIGHTS, OVERRIDE], OPENMOT_ENGINES_VOLUME="openmot-trt-e2e-engines")
    assert config["volumes"]["engines"]["name"] == "openmot-trt-e2e-engines"
    assert config["volumes"]["engines"]["external"] is True


@needs_docker
def test_a_missing_engine_file_variable_is_reported_by_name() -> None:
    with pytest.raises(subprocess.CalledProcessError) as error:
        rendered([BASE, WEIGHTS, OVERRIDE], engine_file=None)
    assert "OPENMOT_ENGINE_FILE" in error.value.stderr
