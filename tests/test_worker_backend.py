from pathlib import Path

import numpy as np
import pytest

import tracking.run
from jobs.memory_store import InMemoryJobStore
from jobs.settings import Settings, load_settings
from tracking.env_check import CheckOutcome, EnvReport
from worker.run import default_dependencies, main
from worker.startup import StartupDependencies, start_worker

PASSING = CheckOutcome(EnvReport("Fake GPU", "555.00", "2.7.1+cu128", "12.8", True), ())
ENGINE = "/models/engines/yolox_x-fp16.engine"


class Recorder:
    def __init__(self, load_error: Exception | None = None) -> None:
        self.load_error = load_error
        self.loads: list[tuple[Path, str, str]] = []
        self.served: list[object] = []
        self.store = InMemoryJobStore()

    def load_detect_frame(self, weights: Path, precision: str, device: str):  # type: ignore[no-untyped-def]
        self.loads.append((weights, precision, device))
        if self.load_error is not None:
            raise self.load_error
        return lambda image: np.zeros((0, 5))

    def dependencies(self) -> StartupDependencies:
        return StartupDependencies(lambda: PASSING, self.load_detect_frame, lambda settings: self.store)

    def serve(self, app: object) -> None:
        self.served.append(app)


def environ_for(tmp_path: Path, **overrides: str) -> dict[str, str]:
    return {"OPENMOT_STORAGE_DIR": str(tmp_path / "storage"), "OPENMOT_WEIGHTS": "/w/yolox_x.pth", **overrides}


def settings_for(tmp_path: Path, **overrides: str) -> Settings:
    return load_settings(environ_for(tmp_path, **overrides))


def test_the_default_dependencies_bind_the_backend_and_engine_into_the_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    def fake_loader(weights, precision, device, backend="torch", engine=None):  # type: ignore[no-untyped-def]
        calls.append((weights, precision, device, backend, engine))
        return lambda image: np.zeros((0, 5))

    monkeypatch.setattr(tracking.run, "load_default_detect_frame", fake_loader)
    settings = settings_for(tmp_path, OPENMOT_BACKEND="tensorrt", OPENMOT_ENGINE=ENGINE)
    default_dependencies(settings).load_detect_frame(Path("/w/yolox_x.pth"), "fp16", "cuda")
    assert calls == [(Path("/w/yolox_x.pth"), "fp16", "cuda", "tensorrt", Path(ENGINE))]


def test_the_default_dependencies_keep_the_pytorch_loader_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    def fake_loader(weights, precision, device, backend="torch", engine=None):  # type: ignore[no-untyped-def]
        calls.append((backend, engine))
        return lambda image: np.zeros((0, 5))

    monkeypatch.setattr(tracking.run, "load_default_detect_frame", fake_loader)
    default_dependencies(settings_for(tmp_path)).load_detect_frame(Path("/w/yolox_x.pth"), "fp16", "cuda")
    assert calls == [("torch", None)]


def test_the_job_metadata_records_a_tensorrt_backend_and_omits_torch(tmp_path: Path) -> None:
    recorder = Recorder()
    tensorrt = settings_for(tmp_path, OPENMOT_BACKEND="tensorrt", OPENMOT_ENGINE=ENGINE)
    assert start_worker(tensorrt, recorder.dependencies(), "w").metadata.backend == "tensorrt"
    assert start_worker(settings_for(tmp_path), recorder.dependencies(), "w").metadata.backend == "torch"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("engine was built for 'Other GPU', this GPU is 'Fake GPU'; rebuild it"),
        FileNotFoundError("tensorrt is not installed, install the [export] extra"),
    ],
)
def test_a_detector_that_cannot_be_loaded_exits_one_with_the_reason_and_serves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    recorder = Recorder(load_error=error)
    environ = environ_for(tmp_path, OPENMOT_BACKEND="tensorrt", OPENMOT_ENGINE=ENGINE)
    assert main(environ, recorder.dependencies(), recorder.serve) == 1
    assert recorder.served == []
    assert str(error) in capsys.readouterr().err


def test_the_log_line_names_the_backend_precision_and_engine_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = environ_for(tmp_path, OPENMOT_BACKEND="tensorrt", OPENMOT_ENGINE=ENGINE)
    recorder = Recorder()
    assert main(environ, recorder.dependencies(), recorder.serve) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("detector:")]
    assert lines == [f"detector: backend=tensorrt precision=fp16 engine={ENGINE}"]


def test_the_default_log_line_has_no_artifact_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    recorder = Recorder()
    assert main(environ_for(tmp_path), recorder.dependencies(), recorder.serve) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("detector:")]
    assert lines == ["detector: backend=torch precision=fp16"]


def test_no_log_line_is_written_when_loading_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    recorder = Recorder(load_error=RuntimeError("boom"))
    assert main(environ_for(tmp_path), recorder.dependencies(), recorder.serve) == 1
    assert "detector:" not in capsys.readouterr().out
