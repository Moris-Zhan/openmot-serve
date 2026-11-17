from k8s_manifests import BASE_DIR, of_kind, render, requires_kustomize

from jobs.settings import load_settings

pytestmark = requires_kustomize


def _config_data() -> dict[str, str]:
    return of_kind(render(BASE_DIR), "ConfigMap")[0]["data"]


def test_the_config_selects_the_pytorch_backend_explicitly() -> None:
    assert _config_data()["OPENMOT_BACKEND"] == "torch"


def test_the_config_loads_with_the_pytorch_backend_and_no_engine() -> None:
    settings = load_settings(_config_data())
    assert settings.backend == "torch"
    assert settings.engine is None


def test_the_engine_path_is_not_part_of_the_default_config() -> None:
    assert "OPENMOT_ENGINE" not in _config_data()
