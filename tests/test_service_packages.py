import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize("package", ["app", "worker"])
def test_the_service_packages_are_importable(package: str) -> None:
    assert importlib.import_module(package).__name__ == package


def test_the_integration_marker_is_registered_and_excluded_by_default(pytestconfig: pytest.Config) -> None:
    markers = [line.split(":")[0] for line in pytestconfig.getini("markers")]
    assert "integration" in markers
    assert "not integration" in " ".join(pytestconfig.getini("addopts"))


def test_the_api_side_modules_never_load_torch() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, app, jobs.settings, tracking.sequence, tracking.motchallenge, tracking.config;"
            "assert 'torch' not in sys.modules, 'torch was imported'",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_service_dependencies_are_installed() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import fastapi, uvicorn, multipart, celery, redis"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
