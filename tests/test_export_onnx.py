import hashlib
from pathlib import Path

import pytest

from inference.artifacts import ArtifactError, onnx_name, read_sidecar, sidecar_path
from inference.export_onnx import ExportDependencies, export_onnx, main
from tracking.detector import DetectorSettings
from tracking.yolox import WeightsIntegrityError

WEIGHTS_SHA256 = "ab12cd34" + "0" * 56
ONNX_BYTES = b"fake-onnx-graph"


class Recorder:
    def __init__(self, fail_at: str | None = None, weights_ok: bool = True) -> None:
        self.calls: list[str] = []
        self.exported_to: list[Path] = []
        self.fail_at = fail_at
        self.weights_ok = weights_ok

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"{name} failed")

    def verify_weights(self, path: Path) -> str:
        self._step("verify_weights")
        if not self.weights_ok:
            raise WeightsIntegrityError(f"{path} has the wrong SHA-256")
        return WEIGHTS_SHA256

    def load_model(self, path: Path) -> object:
        self._step("load_model")
        return object()

    def export(self, model: object, path: Path, height: int, width: int) -> None:
        self._step("export")
        self.exported_to.append(path)
        path.write_bytes(ONNX_BYTES)

    def check(self, path: Path) -> None:
        self._step("check")

    def dependencies(self) -> ExportDependencies:
        return ExportDependencies(
            verify_weights=self.verify_weights,
            load_model=self.load_model,
            export=self.export,
            check=self.check,
            torch_version="9.9.9",
        )


def _target(directory: Path) -> Path:
    return directory / onnx_name(WEIGHTS_SHA256, 800, 1440)


def _run(recorder: Recorder, directory: Path) -> Path:
    return export_onnx(Path("weights.pth"), directory, DetectorSettings(precision="fp32"), recorder.dependencies())


def test_the_flow_runs_in_order_and_writes_the_onnx_and_its_sidecar(tmp_path: Path) -> None:
    recorder = Recorder()
    path = _run(recorder, tmp_path)
    assert recorder.calls == ["verify_weights", "load_model", "export", "check"]
    assert path == _target(tmp_path) and path.read_bytes() == ONNX_BYTES
    assert sidecar_path(path).is_file()


def test_the_sidecar_records_where_the_file_came_from(tmp_path: Path) -> None:
    path = _run(Recorder(), tmp_path)
    assert read_sidecar(sidecar_path(path)) == {
        "weights_sha256": WEIGHTS_SHA256,
        "onnx_sha256": hashlib.sha256(ONNX_BYTES).hexdigest(),
        "input_height": 800,
        "input_width": 1440,
        "opset": 17,
        "exporter": "torchscript",
        "torch_version": "9.9.9",
        "precision": "fp32",
    }


def test_the_export_goes_to_a_temporary_file_that_is_renamed_atomically(tmp_path: Path) -> None:
    recorder = Recorder()
    path = _run(recorder, tmp_path)
    assert recorder.exported_to and recorder.exported_to[0] != path
    assert sorted(entry.name for entry in tmp_path.iterdir()) == sorted([path.name, sidecar_path(path).name])


def test_corrupt_weights_write_nothing_and_never_load_the_model(tmp_path: Path) -> None:
    recorder = Recorder(weights_ok=False)
    directory = tmp_path / "engines"
    with pytest.raises(WeightsIntegrityError):
        _run(recorder, directory)
    assert recorder.calls == ["verify_weights"]
    assert not directory.exists()


def test_an_existing_target_is_refused_and_left_untouched(tmp_path: Path) -> None:
    existing = _target(tmp_path)
    existing.write_bytes(b"precious")
    recorder = Recorder()
    with pytest.raises(ArtifactError, match="already exists"):
        _run(recorder, tmp_path)
    assert existing.read_bytes() == b"precious" and "export" not in recorder.calls
    assert [entry.name for entry in tmp_path.iterdir()] == [existing.name]


@pytest.mark.parametrize("failing_step", ["export", "check"])
def test_a_failing_step_leaves_no_partial_files(tmp_path: Path, failing_step: str) -> None:
    with pytest.raises(RuntimeError, match=failing_step):
        _run(Recorder(fail_at=failing_step), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_main_exports_and_reports_the_written_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    recorder = Recorder()
    code = main(["--weights", "weights.pth", "--output-dir", str(tmp_path)], recorder.dependencies())
    assert code == 0 and _target(tmp_path).is_file()
    assert onnx_name(WEIGHTS_SHA256, 800, 1440) in capsys.readouterr().out


def test_main_exits_non_zero_when_the_weights_are_corrupt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--weights", "w.pth", "--output-dir", str(tmp_path)], Recorder(weights_ok=False).dependencies())
    assert code != 0 and "SHA-256" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_main_exits_non_zero_without_overwriting_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _target(tmp_path).write_bytes(b"precious")
    code = main(["--weights", "w.pth", "--output-dir", str(tmp_path)], Recorder().dependencies())
    assert code != 0 and "already exists" in capsys.readouterr().err
    assert _target(tmp_path).read_bytes() == b"precious"
