import json
from pathlib import Path

import pytest

from inference.artifacts import ArtifactInfo
from tests import test_latency_cli, test_run, test_run_cli

REJECTED_BACKENDS = ["torch", "onnx"]


def test_tracking_run_accepts_tensorrt_int8(tmp_path: Path) -> None:
    frames = test_run_cli.make_sequence(tmp_path / "seq")
    loader = test_run_cli.RecordingLoader()
    arguments = [str(frames), "--output", str(tmp_path / "o.txt"), "--backend", "tensorrt", "--precision", "int8"]
    assert test_run_cli.run(arguments, loader) == 0
    assert [(call[1], call[3]) for call in loader.calls] == [("int8", "tensorrt")]
    metadata = json.loads((tmp_path / "o.meta.json").read_text())
    assert metadata["precision"] == "int8" and metadata["backend"] == "tensorrt"


@pytest.mark.parametrize("backend", REJECTED_BACKENDS)
def test_tracking_run_rejects_int8_before_loading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: str
) -> None:
    frames = test_run_cli.make_sequence(tmp_path / "seq")
    loader = test_run_cli.RecordingLoader()
    arguments = [str(frames), "--output", str(tmp_path / "out" / "o.txt"), "--backend", backend, "--precision", "int8"]
    assert test_run_cli.run(arguments, loader) != 0
    error = capsys.readouterr().err
    assert backend in error and "int8" in error
    assert loader.calls == []
    test_run_cli.assert_nothing_written(tmp_path / "out")


def test_benchmark_run_accepts_tensorrt_int8(tmp_path: Path) -> None:
    harness = test_run.Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("m.engine"), test_run.TENSORRT_SIDECAR)
    assert harness.run("int8", ("--backend", "tensorrt", "--precision", "int8")) == 0
    summary = json.loads((harness.results_root / "int8" / "summary.json").read_text())
    assert summary["precision"] == "int8" and summary["backend"] == "tensorrt"
    assert harness.loaded_with[0][0] == "int8"
    assert harness.resolutions[0][1].precision == "int8"


@pytest.mark.parametrize("backend", REJECTED_BACKENDS)
def test_benchmark_run_rejects_int8_before_anything_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: str
) -> None:
    harness = test_run.Harness(tmp_path)
    assert harness.run("bad", ("--backend", backend, "--precision", "int8")) != 0
    error = capsys.readouterr().err
    assert backend in error and "int8" in error
    assert harness.loads == 0 and harness.resolutions == [] and not harness.results_root.exists()


def test_latency_accepts_tensorrt_int8(tmp_path: Path) -> None:
    harness = test_latency_cli.Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("m.engine"), {**test_latency_cli.TENSORRT_SIDECAR})
    assert harness.run(extra=("--backend", "tensorrt", "--precision", "int8")) == 0
    document = json.loads((harness.result_dir() / "latency.json").read_text())
    assert document["precision"] == "int8" and document["backend"] == "tensorrt"


@pytest.mark.parametrize("backend", REJECTED_BACKENDS)
def test_latency_rejects_int8_before_resolving_or_loading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: str
) -> None:
    harness = test_latency_cli.Harness(tmp_path)
    assert harness.run(extra=("--backend", backend, "--precision", "int8")) != 0
    error = capsys.readouterr().err
    assert backend in error and "int8" in error
    assert harness.loads == [] and harness.resolutions == [] and not harness.results_root.exists()
