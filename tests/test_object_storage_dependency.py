import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "deploy" / "docker" / "api-requirements.txt"


def _boto3_lines(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip().startswith("boto3")]


def test_the_service_extra_depends_on_boto3_with_a_lower_bound() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _boto3_lines(project["project"]["optional-dependencies"]["service"]) == ["boto3>=1.34"]


def test_the_api_image_requirements_carry_the_same_boto3_line() -> None:
    assert _boto3_lines(REQUIREMENTS.read_text(encoding="utf-8").splitlines()) == ["boto3>=1.34"]


def test_boto3_is_not_a_core_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _boto3_lines(project["project"]["dependencies"]) == []
