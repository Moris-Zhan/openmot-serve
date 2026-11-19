import os
import subprocess

import pytest
import yaml

from tests.test_compose_tensorrt import BASE, DOCKER_DIR, needs_docker, rendered

S3 = DOCKER_DIR / "docker-compose.s3.yml"
CREDENTIALS = {"S3_ACCESS_KEY": "test-user", "S3_SECRET_KEY": "test-password-123"}
STORAGE_TARGET = "/data/storage"


def _storage_source(service: dict) -> str:
    return next(volume["source"] for volume in service["volumes"] if volume["target"] == STORAGE_TARGET)


def _with_override(**extra: str) -> dict:
    return rendered([BASE, S3], engine_file=None, **{**CREDENTIALS, **extra})


@needs_docker
def test_the_default_compose_has_no_s3_server_and_api_and_worker_still_share_the_storage_volume() -> None:
    config = rendered([BASE], engine_file=None)
    assert "s3" not in config["services"]
    assert _storage_source(config["services"]["api"]) == _storage_source(config["services"]["worker"]) == "storage"
    assert "OPENMOT_STORAGE_BACKEND" not in config["services"]["api"]["environment"]


@needs_docker
def test_with_the_override_api_and_worker_keep_their_scratch_in_different_volumes() -> None:
    services = _with_override()["services"]
    api, worker = _storage_source(services["api"]), _storage_source(services["worker"])
    assert api != worker
    assert {api, worker}.isdisjoint({"storage", "s3-data"})


@needs_docker
def test_the_s3_data_volume_is_not_mounted_into_api_or_worker() -> None:
    services = _with_override()["services"]
    for name in ("api", "worker"):
        assert all(volume["source"] != "s3-data" for volume in services[name].get("volumes", []))
    assert any(volume["source"] == "s3-data" for volume in services["s3"]["volumes"])


@needs_docker
@pytest.mark.parametrize("name", ["api", "worker"])
def test_api_and_worker_select_the_s3_backend_and_receive_the_credentials(name: str) -> None:
    environment = _with_override()["services"][name]["environment"]
    assert environment["OPENMOT_STORAGE_BACKEND"] == "s3"
    assert environment["OPENMOT_S3_ENDPOINT_URL"] == "http://s3:9000"
    assert environment["OPENMOT_S3_BUCKET"] == "openmot"
    assert environment["AWS_ACCESS_KEY_ID"] == CREDENTIALS["S3_ACCESS_KEY"]
    assert environment["AWS_SECRET_ACCESS_KEY"] == CREDENTIALS["S3_SECRET_KEY"]
    assert environment["OPENMOT_STORAGE_DIR"] == STORAGE_TARGET


@needs_docker
def test_the_bucket_name_can_be_chosen() -> None:
    environment = _with_override(OPENMOT_S3_BUCKET="tracking")["services"]["api"]["environment"]
    assert environment["OPENMOT_S3_BUCKET"] == "tracking"


@needs_docker
def test_s3_server_has_a_healthcheck_and_api_and_worker_wait_for_it() -> None:
    services = _with_override()["services"]
    assert services["s3"]["healthcheck"]["test"]
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["s3"]["condition"] == "service_healthy"
        assert services[name]["depends_on"]["redis"]["condition"] == "service_healthy"


@needs_docker
def test_s3_server_is_published_on_the_loopback_interface_only() -> None:
    ports = _with_override()["services"]["s3"]["ports"]
    assert ports and all(port["host_ip"] == "127.0.0.1" for port in ports)


@needs_docker
@pytest.mark.parametrize("missing", ["S3_ACCESS_KEY", "S3_SECRET_KEY"])
def test_rendering_without_credentials_fails_and_names_the_variable(missing: str) -> None:
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("OPENMOT_", "S3_"))}
    environment.update({key: value for key, value in CREDENTIALS.items() if key != missing})
    result = subprocess.run(
        ["docker", "compose", "-f", str(BASE), "-f", str(S3), "config", "--format", "json"],
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode != 0 and missing in result.stderr


def test_the_override_file_carries_no_default_credentials_and_pins_the_rustfs_image() -> None:
    text = S3.read_text()
    document = yaml.safe_load(text)
    assert "rustfsadmin" not in text.lower()
    image = document["services"]["s3"]["image"]
    assert image.startswith("rustfs/rustfs:") and not image.endswith(":latest")
    for variable in ("S3_ACCESS_KEY", "S3_SECRET_KEY"):
        assert f"${{{variable}:?" in text
