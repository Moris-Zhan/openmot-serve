import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTED_EXPORT_PINS = {
    "onnx": "1.17.0",
    "onnxruntime-gpu": "1.22.0",
    "tensorrt-cu12": "10.13.3.9",
}


def _pyproject() -> dict:
    with open(REPOSITORY / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


def test_the_export_extra_pins_each_package_to_an_exact_version() -> None:
    requirements = _pyproject()["project"]["optional-dependencies"]["export"]
    pins = dict(requirement.split("==") for requirement in requirements)
    assert pins == EXPECTED_EXPORT_PINS
    assert len(requirements) == len(EXPECTED_EXPORT_PINS)


def test_the_export_packages_are_not_default_or_dev_dependencies() -> None:
    project = _pyproject()["project"]
    extras = project["optional-dependencies"]
    others = project["dependencies"] + extras["dev"] + extras["service"]
    names = {requirement.split("==")[0].split(">=")[0] for requirement in others}
    assert names.isdisjoint(EXPECTED_EXPORT_PINS)


def test_the_inference_package_is_part_of_the_distribution() -> None:
    included = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "inference*" in included


def test_exported_artifacts_are_ignored_by_version_control() -> None:
    lines = (REPOSITORY / ".gitignore").read_text().splitlines()
    assert "models/engines/" in lines
