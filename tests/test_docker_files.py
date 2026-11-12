import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKER_DIR = ROOT / "deploy" / "docker"
GPU_PACKAGES = {"torch", "torchvision"}


def _requirement_lines(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _package_name(requirement: str) -> str:
    for separator in ("==", ">=", "<=", "~=", ">", "<", "["):
        requirement = requirement.split(separator)[0]
    return requirement.strip()


def _project_requirements() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    return [*project["dependencies"], *project["optional-dependencies"]["service"]]


def test_the_api_dependency_list_matches_pyproject_minus_the_gpu_packages() -> None:
    expected = {line for line in _project_requirements() if _package_name(line) not in GPU_PACKAGES}
    assert set(_requirement_lines(DOCKER_DIR / "api-requirements.txt")) == expected


def test_the_api_dependency_list_has_no_gpu_packages() -> None:
    names = {_package_name(line) for line in _requirement_lines(DOCKER_DIR / "api-requirements.txt")}
    assert names.isdisjoint(GPU_PACKAGES)


@pytest.mark.parametrize("name", ["api.Dockerfile", "worker.Dockerfile"])
def test_images_use_the_slim_python_base_and_uv(name: str) -> None:
    text = (DOCKER_DIR / name).read_text()
    assert "FROM python:3.11-slim" in text
    assert "uv pip install" in text


def test_the_api_image_installs_the_project_without_dependencies() -> None:
    text = (DOCKER_DIR / "api.Dockerfile").read_text()
    assert "--no-deps" in text
    assert "api-requirements.txt" in text
    assert "cu128" not in text


def test_the_worker_image_installs_cu128_torch() -> None:
    text = (DOCKER_DIR / "worker.Dockerfile").read_text()
    assert "download.pytorch.org/whl/cu128" in text
    assert "torch==2.7.1" in text and "torchvision==0.22.1" in text


@pytest.mark.parametrize("name", ["api.Dockerfile", "worker.Dockerfile"])
def test_images_never_copy_weights_or_data(name: str) -> None:
    copies = [line for line in (DOCKER_DIR / name).read_text().splitlines() if line.startswith(("COPY", "ADD"))]
    assert copies
    for line in copies:
        assert "models" not in line and "data" not in line and ".pth" not in line


@pytest.mark.parametrize("name", ["api.Dockerfile", "worker.Dockerfile"])
def test_images_do_not_run_as_root(name: str) -> None:
    assert "USER openmot" in (DOCKER_DIR / name).read_text()


def test_the_pinned_torch_versions_match_pyproject() -> None:
    pinned = {line for line in _project_requirements() if _package_name(line) in GPU_PACKAGES}
    text = (DOCKER_DIR / "worker.Dockerfile").read_text()
    assert all(requirement in text for requirement in pinned)


@pytest.mark.parametrize(
    "pattern", ["data/", "models/", ".git", "*.pth", ".env", "*.pem", "*.key", "tests/", "graphify-out/"]
)
def test_dockerignore_excludes_sensitive_and_heavy_paths(pattern: str) -> None:
    assert pattern in _requirement_lines(ROOT / ".dockerignore")
