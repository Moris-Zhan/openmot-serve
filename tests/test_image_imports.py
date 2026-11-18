import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "deploy" / "docker" / "worker.Dockerfile").read_text(encoding="utf-8")
PROJECT_PACKAGES = {
    pattern.removesuffix("*")
    for pattern in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"][
        "packages"
    ]["find"]["include"]
}
IMAGE_PACKAGES = ("tracking", "inference", "jobs", "metrics", "app", "worker")
COPIED = set(re.findall(r"^COPY (\S+) \./\1$", DOCKERFILE, re.M))


def _import_time_project_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    return imported & PROJECT_PACKAGES


def test_the_dockerfile_copies_the_packages_the_image_runs() -> None:
    assert set(IMAGE_PACKAGES) <= COPIED


@pytest.mark.parametrize("package", IMAGE_PACKAGES)
def test_every_project_package_imported_at_import_time_by_the_image_code_is_copied_into_the_image(
    package: str,
) -> None:
    offenders = {
        f"{path.relative_to(ROOT)} imports {name}": name
        for path in sorted((ROOT / package).rglob("*.py"))
        for name in _import_time_project_imports(path) - COPIED
    }
    assert not offenders, f"worker.Dockerfile does not COPY these packages: {sorted(offenders)}"


@pytest.mark.parametrize("module", ["inference.calibration", "inference.build_engine"])
def test_the_engine_build_modules_import_without_the_eval_package(module: str) -> None:
    code = f"import sys; sys.modules['eval'] = None; import {module}"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-600:]
