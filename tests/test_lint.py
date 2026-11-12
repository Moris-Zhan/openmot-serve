import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

requires_ruff = pytest.mark.skipif(
    importlib.util.find_spec("ruff") is None,
    reason="ruff is not installed; install it with: pip install -e '.[dev]'",
)


def _ruff(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )


def _pyproject() -> dict:
    return tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())


@requires_ruff
def test_the_repository_is_free_of_lint_violations() -> None:
    result = _ruff("check", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_lint_configuration_excludes_vendored_third_party_code() -> None:
    assert "eval/vendor" in _pyproject()["tool"]["ruff"]["exclude"]


@requires_ruff
def test_vendored_third_party_code_is_not_linted() -> None:
    result = _ruff("check", "--show-files", ".")
    assert result.returncode == 0, result.stderr
    assert "eval/vendor" not in result.stdout


def test_ruff_is_a_pinned_development_dependency() -> None:
    assert "ruff==0.8.4" in _pyproject()["project"]["optional-dependencies"]["dev"]
