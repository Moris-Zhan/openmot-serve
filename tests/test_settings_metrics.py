from pathlib import Path

import pytest

from jobs.settings import Settings, SettingsError, load_settings

MINIMAL = {"OPENMOT_STORAGE_DIR": "/srv/openmot"}


def test_the_metrics_ports_have_defaults() -> None:
    settings = load_settings(MINIMAL)
    assert settings.api_metrics_port == 9101
    assert settings.worker_metrics_port == 9102


def test_the_metrics_ports_can_be_overridden() -> None:
    settings = load_settings(
        {**MINIMAL, "OPENMOT_API_METRICS_PORT": "19101", "OPENMOT_WORKER_METRICS_PORT": "19102"}
    )
    assert (settings.api_metrics_port, settings.worker_metrics_port) == (19101, 19102)


@pytest.mark.parametrize("name", ["OPENMOT_API_METRICS_PORT", "OPENMOT_WORKER_METRICS_PORT"])
@pytest.mark.parametrize("value", ["abc", "", "0", "-1", "65536", "9101.5"])
def test_an_invalid_metrics_port_names_the_variable(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        load_settings({**MINIMAL, name: value})


def test_settings_built_without_the_metrics_ports_still_work() -> None:
    settings = Settings(
        storage_dir=Path("/srv"),
        redis_url="redis://localhost:6379/0",
        weights=Path("w.pth"),
        precision="fp16",
        max_upload_bytes=1,
        max_unpacked_bytes=2,
        lock_timeout_seconds=10,
        task_time_limit_seconds=5,
    )
    assert settings.api_metrics_port == 9101
