import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "service.md").read_text()
TENSORRT_SECTION = DOC.split("### TensorRT worker", 1)[1].split("\n### ", 1)[0]


def _table_variables() -> set[str]:
    return set(re.findall(r"^\| `(OPENMOT_[A-Z_]+)` \|", DOC, flags=re.MULTILINE))


def _settings_variables() -> set[str]:
    return set(re.findall(r'"(OPENMOT_[A-Z_]+)"', (ROOT / "jobs" / "settings.py").read_text()))


def test_every_variable_in_the_table_is_read_by_the_settings() -> None:
    assert _table_variables() <= _settings_variables()


def test_the_backend_variables_are_in_the_table() -> None:
    assert {"OPENMOT_BACKEND", "OPENMOT_ENGINE"} <= _table_variables()


def test_the_commands_name_files_and_modules_that_exist() -> None:
    for name in ("docker-compose.yml", "docker-compose.weights-volume.yml", "docker-compose.tensorrt.yml"):
        assert name in TENSORRT_SECTION
        assert (ROOT / "deploy" / "docker" / name).is_file()
    for module in ("inference.export_onnx", "inference.build_engine"):
        assert module in TENSORRT_SECTION
        assert (ROOT / (module.replace(".", "/") + ".py")).is_file()


def test_the_section_documents_the_compose_variables_and_the_startup_log_line() -> None:
    for name in ("OPENMOT_ENGINE_FILE", "OPENMOT_ENGINES_VOLUME", "engine-builder", "openmot-engines"):
        assert name in TENSORRT_SECTION
    assert "detector: backend=tensorrt precision=fp16 engine=" in TENSORRT_SECTION
    assert "detector: backend=torch precision=fp16" in TENSORRT_SECTION


def test_the_known_limits_are_stated() -> None:
    for limit in ("GPU 型號", "TensorRT 版本", "比 PyTorch 慢", "沒有 TensorRT 映像", "CI 不建置", "兩者都啟動失敗"):
        assert limit in TENSORRT_SECTION


def test_the_measured_results_are_recorded_with_their_scope() -> None:
    assert "逐位元組相同" in DOC and "3 次量測" in DOC
    assert "tests/test_tensorrt_worker_e2e.py" in DOC
