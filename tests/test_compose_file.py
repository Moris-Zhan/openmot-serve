import json
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).resolve().parents[1] / "deploy" / "docker" / "docker-compose.yml"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="the docker CLI is not installed")


def _config() -> dict[str, dict]:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_there_are_exactly_the_three_services() -> None:
    assert set(_config()["services"]) == {"redis", "api", "worker"}


def test_redis_persists_through_an_append_only_file_on_a_volume() -> None:
    redis = _config()["services"]["redis"]
    assert redis["command"] == ["redis-server", "--appendonly", "yes"]
    assert any(volume["target"] == "/data" and volume["type"] == "volume" for volume in redis["volumes"])


def test_api_and_worker_share_one_named_storage_volume() -> None:
    config = _config()
    sources = {
        name: {volume["source"] for volume in config["services"][name]["volumes"] if volume["target"] == "/data/storage"}
        for name in ("api", "worker")
    }
    assert sources["api"] == sources["worker"] == {"storage"}
    assert config["volumes"]["storage"]["name"] == "openmot_storage"


@pytest.mark.parametrize("name", ["api", "worker"])
def test_services_wait_for_a_healthy_redis(name: str) -> None:
    assert _config()["services"][name]["depends_on"]["redis"]["condition"] == "service_healthy"


@pytest.mark.parametrize("name", ["redis", "api", "worker"])
def test_every_service_has_a_healthcheck_and_restarts(name: str) -> None:
    service = _config()["services"][name]
    assert service["healthcheck"]["test"]
    assert service["restart"] == "unless-stopped"


def test_healthchecks_probe_the_right_things() -> None:
    services = _config()["services"]
    assert "redis-cli" in services["redis"]["healthcheck"]["test"]
    assert "http://127.0.0.1:8000/health" in " ".join(services["api"]["healthcheck"]["test"])
    assert "worker.healthcheck" in " ".join(services["worker"]["healthcheck"]["test"])


def test_the_worker_reserves_an_nvidia_gpu_and_the_api_does_not() -> None:
    services = _config()["services"]
    device = services["worker"]["deploy"]["resources"]["reservations"]["devices"][0]
    assert device["driver"] == "nvidia" and device["capabilities"] == ["gpu"] and device["count"] == 1
    assert "deploy" not in services["api"]


def test_weights_are_a_read_only_bind_mount_into_the_worker_only() -> None:
    services = _config()["services"]
    mounts = [volume for volume in services["worker"]["volumes"] if volume["target"] == "/models/yolox_x.pth"]
    assert len(mounts) == 1 and mounts[0]["type"] == "bind" and mounts[0]["read_only"] is True
    assert services["worker"]["environment"]["OPENMOT_WEIGHTS"] == "/models/yolox_x.pth"
    assert all("/models" not in volume["target"] for volume in services["api"]["volumes"])


def test_only_the_api_publishes_a_port() -> None:
    services = _config()["services"]
    assert "ports" not in services["redis"] and "ports" not in services["worker"]
    assert services["api"]["ports"][0]["target"] == 8000


def test_services_reach_redis_by_its_service_name() -> None:
    services = _config()["services"]
    for name in ("api", "worker"):
        assert services[name]["environment"]["OPENMOT_REDIS_URL"] == "redis://redis:6379/0"


def test_the_weights_volume_override_replaces_the_bind_mount() -> None:
    override = COMPOSE_FILE.with_name("docker-compose.weights-volume.yml")
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(override), "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    mounts = [v for v in json.loads(result.stdout)["services"]["worker"]["volumes"] if v["target"] == "/models/yolox_x.pth"]
    assert len(mounts) == 1 and mounts[0]["type"] == "volume" and mounts[0]["read_only"] is True
