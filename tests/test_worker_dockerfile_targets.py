import re
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[1] / "deploy" / "docker" / "worker.Dockerfile"
RUNTIME_PREFIXES = ("USER ", "ENV ", "CMD ", "WORKDIR /home")


def stages() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current: list[str] = []
    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        match = re.fullmatch(r"FROM\s+(\S+)\s+AS\s+(\S+)", line, flags=re.IGNORECASE)
        if match:
            current = result.setdefault(match.group(2), [])
        if line and not line.startswith("#"):
            current.append(line)
    return result


def runtime_lines(name: str) -> list[str]:
    return [line for line in stages()[name] if line.startswith(RUNTIME_PREFIXES)]


def test_the_stages_are_base_then_tensorrt_then_the_default_worker() -> None:
    assert list(stages()) == ["base", "tensorrt", "worker"]


def test_the_default_target_is_the_last_stage() -> None:
    assert list(stages())[-1] == "worker"


def test_the_terminal_stages_build_on_the_base_stage() -> None:
    first_lines = {name: lines[0] for name, lines in stages().items()}
    assert first_lines["tensorrt"].split()[1] == "base"
    assert first_lines["worker"].split()[1] == "base"


def test_the_base_stage_installs_the_service_extra_and_the_inference_package() -> None:
    base = " ".join(stages()["base"])
    assert '".[service]"' in base
    assert "COPY inference ./inference" in stages()["base"]
    assert "[export]" not in base


def test_only_the_tensorrt_stage_installs_the_export_extra() -> None:
    assert any('".[export]"' in line for line in stages()["tensorrt"])
    assert not any("[export]" in line for line in stages()["worker"])


def test_both_terminal_stages_run_as_the_same_non_root_user_with_the_same_environment() -> None:
    assert "USER openmot" in runtime_lines("worker")
    assert runtime_lines("tensorrt") == runtime_lines("worker")
    assert any(line.startswith("CMD ") for line in runtime_lines("worker"))


def test_the_base_stage_creates_an_empty_engine_directory_owned_by_the_runtime_user() -> None:
    lines = stages()["base"]
    assert any("mkdir -p" in line and "/models/engines" in line for line in lines)
    assert any("chown openmot" in line and "/models/engines" in line for line in lines)


@pytest.mark.parametrize("stage", ["base", "tensorrt", "worker"])
def test_no_stage_copies_weights_onnx_or_engine_files(stage: str) -> None:
    copies = [line for line in stages()[stage] if line.startswith(("COPY", "ADD"))]
    for line in copies:
        assert not any(token in line for token in (".pth", ".onnx", ".engine", "models", "data"))
