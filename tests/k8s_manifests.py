import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

K8S_DIR = Path(__file__).resolve().parents[1] / "deploy" / "k8s"
BASE_DIR = K8S_DIR / "base"

requires_kustomize = pytest.mark.skipif(shutil.which("kustomize") is None, reason="the kustomize CLI is not installed")


def render(directory: Path) -> list[dict]:
    result = subprocess.run(["kustomize", "build", str(directory)], capture_output=True, text=True, check=True)
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def of_kind(documents: list[dict], kind: str) -> list[dict]:
    return [document for document in documents if document["kind"] == kind]
