import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch

from eval.latency import Dependencies, default_dependencies, read_gpu_memory_mib
from inference.backends import BackendError
from tests.test_latency_cli import PASSING, Harness
from tracking.detector import PreparedFrame, StagedDetector

READINGS = [100, 1100, 1300]
GPU_MEMORY_KEYS = {
    "available",
    "used_before_load_mib",
    "used_after_load_mib",
    "used_after_run_mib",
    "load_delta_mib",
    "run_delta_mib",
    "method",
}


class Recorder:
    def __init__(self, readings: list[int | None]) -> None:
        self.readings = list(readings)
        self.events: list[str] = []
        self.devices: list[str] = []

    def probe(self, device: str) -> int | None:
        self.events.append("read")
        self.devices.append(device)
        return self.readings.pop(0)

    def check(self):  # type: ignore[no-untyped-def]
        self.events.append("check")
        return PASSING

    def load(self, weights, precision, device, backend="torch", engine=None):  # type: ignore[no-untyped-def]
        self.events.append("load")
        return StagedDetector(
            prepare=lambda image: PreparedFrame(torch.zeros(1), 1.0),
            infer=lambda prepared: self.events.append("infer") or torch.zeros(1),
            finish=lambda raw, prepared: np.empty((0, 5)),
            synchronize=lambda: None,
        )


def _run(tmp_path: Path, readings: list[int | None]) -> tuple[Harness, Recorder, int]:
    harness = Harness(tmp_path)
    recorder = Recorder(readings)
    harness.dependencies = replace(
        harness.dependencies,
        check_environment=recorder.check,
        load_staged=recorder.load,
        read_gpu_memory=recorder.probe,
    )
    return harness, recorder, harness.run()


def _document(harness: Harness) -> dict:
    return json.loads((harness.result_dir() / "latency.json").read_text())


def test_memory_is_read_before_the_environment_check_after_loading_and_after_the_measurement(tmp_path: Path) -> None:
    _, recorder, status = _run(tmp_path, READINGS)
    assert status == 0
    events = recorder.events
    assert events[:3] == ["read", "check", "load"]
    assert events[-1] == "read"
    assert "infer" in events[3:-1] and events.count("read") == 3


def test_the_probe_is_asked_about_the_device_the_run_uses(tmp_path: Path) -> None:
    _, recorder, _ = _run(tmp_path, READINGS)
    assert recorder.devices == ["cpu", "cpu", "cpu"]


def test_the_json_has_the_three_readings_the_two_increases_and_the_method(tmp_path: Path) -> None:
    harness, _, _ = _run(tmp_path, READINGS)
    memory = _document(harness)["gpu_memory"]
    assert set(memory) == GPU_MEMORY_KEYS
    assert memory["available"] is True
    assert (memory["used_before_load_mib"], memory["used_after_load_mib"], memory["used_after_run_mib"]) == (
        100,
        1100,
        1300,
    )
    assert memory["load_delta_mib"] == 1100 - 100
    assert memory["run_delta_mib"] == 1300 - 100
    assert "nvidia-smi" in memory["method"] and "整張 GPU" in memory["method"]


def test_the_results_keys_are_unchanged(tmp_path: Path) -> None:
    harness, _, _ = _run(tmp_path, READINGS)
    assert set(_document(harness)["results"]) == {"inference", "end_to_end", "throughput_fps"}


def test_the_table_shows_the_increases_that_the_json_records(tmp_path: Path) -> None:
    harness, _, _ = _run(tmp_path, READINGS)
    table = (harness.result_dir() / "latency.md").read_text()
    memory_line = next(line for line in table.splitlines() if "GPU 記憶體" in line)
    assert "1000" in memory_line and "1200" in memory_line
    assert "MiB" in memory_line


@pytest.mark.parametrize("readings", [[None, None, None], [100, None, 300], [None, 200, 300], [100, 200, None]])
def test_an_unreadable_memory_marks_it_unavailable_and_the_run_still_completes(
    tmp_path: Path, readings: list[int | None]
) -> None:
    harness, _, status = _run(tmp_path, readings)
    assert status == 0
    document = _document(harness)
    assert document["gpu_memory"] == {"available": False}
    assert document["results"]["throughput_fps"] > 0
    assert "GPU 記憶體" not in (harness.result_dir() / "latency.md").read_text()


def test_a_failed_load_writes_no_result(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    def failing_load(*_: object, **__: object) -> StagedDetector:
        raise BackendError("cannot load the engine")

    harness.dependencies = replace(
        harness.dependencies, load_staged=failing_load, read_gpu_memory=Recorder(READINGS).probe
    )
    assert harness.run() == 1
    assert not harness.result_dir().exists()


def test_without_a_probe_the_memory_is_unavailable() -> None:
    dependencies = Dependencies(lambda: PASSING, lambda *_: None)  # type: ignore[arg-type,return-value]
    assert dependencies.read_gpu_memory("cuda") is None


def test_the_command_line_uses_the_nvidia_smi_probe() -> None:
    assert default_dependencies().read_gpu_memory is read_gpu_memory_mib
