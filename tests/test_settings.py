from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jobs.settings import Settings, SettingsError, load_settings

MINIMAL = {"OPENMOT_STORAGE_DIR": "/srv/openmot"}


def test_only_the_storage_directory_is_required() -> None:
    settings = load_settings(MINIMAL)
    assert settings.storage_dir == Path("/srv/openmot")
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.weights == Path("models/yolox_x.pth")
    assert settings.precision == "fp16"
    assert settings.max_upload_bytes > 0
    assert settings.max_unpacked_bytes >= settings.max_upload_bytes
    assert settings.task_time_limit_seconds < settings.lock_timeout_seconds


def test_every_value_can_be_overridden() -> None:
    settings = load_settings(
        {
            "OPENMOT_STORAGE_DIR": "/data/jobs",
            "OPENMOT_REDIS_URL": "redis://redis:6379/1",
            "OPENMOT_WEIGHTS": "/models/custom.pth",
            "OPENMOT_PRECISION": "fp32",
            "OPENMOT_MAX_UPLOAD_BYTES": "1000",
            "OPENMOT_MAX_UNPACKED_BYTES": "5000",
            "OPENMOT_LOCK_TIMEOUT_SECONDS": "600",
            "OPENMOT_TASK_TIME_LIMIT_SECONDS": "500",
        }
    )
    assert settings == Settings(
        storage_dir=Path("/data/jobs"),
        redis_url="redis://redis:6379/1",
        weights=Path("/models/custom.pth"),
        precision="fp32",
        max_upload_bytes=1000,
        max_unpacked_bytes=5000,
        lock_timeout_seconds=600,
        task_time_limit_seconds=500,
    )


def test_a_missing_storage_directory_names_the_variable() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_STORAGE_DIR"):
        load_settings({})


@pytest.mark.parametrize(
    "name, value",
    [
        ("OPENMOT_STORAGE_DIR", ""),
        ("OPENMOT_STORAGE_DIR", "relative/path"),
        ("OPENMOT_REDIS_URL", "http://redis:6379"),
        ("OPENMOT_REDIS_URL", ""),
        ("OPENMOT_PRECISION", "int8"),
        ("OPENMOT_MAX_UPLOAD_BYTES", "lots"),
        ("OPENMOT_MAX_UPLOAD_BYTES", "0"),
        ("OPENMOT_MAX_UPLOAD_BYTES", "-5"),
        ("OPENMOT_MAX_UNPACKED_BYTES", "1.5"),
        ("OPENMOT_LOCK_TIMEOUT_SECONDS", "0"),
        ("OPENMOT_TASK_TIME_LIMIT_SECONDS", "abc"),
    ],
)
def test_an_invalid_value_fails_and_names_the_variable(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        load_settings({**MINIMAL, name: value})


def test_the_unpacked_limit_may_not_be_below_the_upload_limit() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_MAX_UNPACKED_BYTES"):
        load_settings({**MINIMAL, "OPENMOT_MAX_UPLOAD_BYTES": "1000", "OPENMOT_MAX_UNPACKED_BYTES": "999"})


def test_the_task_time_limit_must_stay_below_the_lock_timeout() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_TASK_TIME_LIMIT_SECONDS"):
        load_settings(
            {**MINIMAL, "OPENMOT_LOCK_TIMEOUT_SECONDS": "100", "OPENMOT_TASK_TIME_LIMIT_SECONDS": "100"}
        )


def test_unrelated_variables_are_ignored() -> None:
    assert load_settings({**MINIMAL, "PATH": "/usr/bin", "OPENMOT_UNKNOWN": "x"}).precision == "fp16"


def test_settings_are_immutable() -> None:
    settings = load_settings(MINIMAL)
    with pytest.raises(FrozenInstanceError):
        settings.precision = "fp32"  # type: ignore[misc]


def test_loading_does_not_read_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMOT_PRECISION", "fp32")
    assert load_settings(MINIMAL).precision == "fp16"
