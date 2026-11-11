from pathlib import Path

import yaml

from jobs.settings import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    DEFAULT_MAX_UNPACKED_BYTES,
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_TASK_TIME_LIMIT_SECONDS,
    load_settings,
)
from k8s_manifests import BASE_DIR, of_kind, render, requires_kustomize

COMPOSE_FILE = Path(__file__).resolve().parents[1] / "deploy" / "docker" / "docker-compose.yml"

pytestmark = requires_kustomize


def _config_data() -> dict[str, str]:
    config_maps = of_kind(render(BASE_DIR), "ConfigMap")
    assert len(config_maps) == 1
    assert config_maps[0]["metadata"]["name"].startswith("openmot-config")
    return config_maps[0]["data"]


def test_the_base_renders_a_single_generated_config_map() -> None:
    _config_data()


def test_the_config_names_and_values_match_the_compose_deployment() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    compose_env = compose["x-service-env"] | compose["services"]["worker"]["environment"]
    data = _config_data()
    assert compose_env.keys() <= data.keys()
    assert {name: data[name] for name in compose_env} == compose_env


def test_the_config_loads_into_the_documented_defaults() -> None:
    settings = load_settings(_config_data())
    assert settings.precision == "fp16"
    assert settings.max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES
    assert settings.max_unpacked_bytes == DEFAULT_MAX_UNPACKED_BYTES
    assert settings.lock_timeout_seconds == DEFAULT_LOCK_TIMEOUT_SECONDS
    assert settings.task_time_limit_seconds == DEFAULT_TASK_TIME_LIMIT_SECONDS


def test_every_config_name_is_an_openmot_variable() -> None:
    assert all(name.startswith("OPENMOT_") for name in _config_data())


def test_the_base_pins_no_namespace_so_an_overlay_can_choose_one() -> None:
    assert all("namespace" not in document["metadata"] for document in render(BASE_DIR))
