from pathlib import Path

import numpy as np
import pytest

from jobs.memory_store import InMemoryJobStore
from jobs.settings import Settings, load_settings
from tracking.env_check import CheckOutcome, EnvReport
from worker.celery_app import TASK_NAME
from worker.run import main
from worker.startup import StartupDependencies, StartupError, start_worker

PASSING = CheckOutcome(EnvReport("Fake GPU", "555.00", "2.7.1+cu128", "12.8", True), ())
FAILING = CheckOutcome(
    EnvReport("n/a", "n/a", "2.7.1+cpu", None, False), ("CUDA is not available to PyTorch (2.7.1+cpu)",)
)


class Recorder:
    def __init__(self, outcome: CheckOutcome = PASSING, load_error: Exception | None = None) -> None:
        self.outcome = outcome
        self.load_error = load_error
        self.loads: list[tuple[Path, str, str]] = []
        self.store = InMemoryJobStore()
        self.served: list[object] = []

    def check_environment(self) -> CheckOutcome:
        return self.outcome

    def load_detect_frame(self, weights: Path, precision: str, device: str):  # type: ignore[no-untyped-def]
        self.loads.append((weights, precision, device))
        if self.load_error is not None:
            raise self.load_error
        return lambda image: np.zeros((0, 5))

    def make_store(self, settings: Settings) -> InMemoryJobStore:
        return self.store

    def serve(self, app: object) -> None:
        self.served.append(app)

    def dependencies(self) -> StartupDependencies:
        return StartupDependencies(self.check_environment, self.load_detect_frame, self.make_store)


def _environ(tmp_path: Path) -> dict[str, str]:
    return {
        "OPENMOT_STORAGE_DIR": str(tmp_path / "storage"),
        "OPENMOT_WEIGHTS": "/weights/yolox_x.pth",
        "OPENMOT_PRECISION": "fp16",
        "OPENMOT_LOCK_TIMEOUT_SECONDS": "900",
        "OPENMOT_TASK_TIME_LIMIT_SECONDS": "600",
    }


def test_a_healthy_start_loads_the_detector_once_on_cuda(tmp_path: Path) -> None:
    recorder = Recorder()
    context = start_worker(load_settings(_environ(tmp_path)), recorder.dependencies(), "worker-a")
    assert recorder.loads == [(Path("/weights/yolox_x.pth"), "fp16", "cuda")]
    assert context.owner == "worker-a"
    assert context.lock_timeout_seconds == 900
    assert context.time_limit_seconds == 600
    assert context.metadata.precision == "fp16"
    assert context.store is recorder.store


def test_a_failed_environment_check_stops_the_start_before_any_weights_are_loaded(tmp_path: Path) -> None:
    recorder = Recorder(outcome=FAILING)
    with pytest.raises(StartupError, match="CUDA is not available"):
        start_worker(load_settings(_environ(tmp_path)), recorder.dependencies(), "worker-a")
    assert recorder.loads == []


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("weights SHA-256 does not match"),
        FileNotFoundError("no such weights file"),
        ValueError("bad precision"),
    ],
)
def test_weights_that_cannot_be_loaded_or_verified_stop_the_start(tmp_path: Path, error: Exception) -> None:
    recorder = Recorder(load_error=error)
    with pytest.raises(StartupError, match=str(error)):
        start_worker(load_settings(_environ(tmp_path)), recorder.dependencies(), "worker-a")


def test_a_healthy_main_serves_with_the_processing_task_registered(tmp_path: Path) -> None:
    recorder = Recorder()
    assert main(_environ(tmp_path), recorder.dependencies(), recorder.serve) == 0
    assert len(recorder.served) == 1
    assert TASK_NAME in recorder.served[0].tasks  # type: ignore[attr-defined]
    assert len(recorder.loads) == 1


def test_a_failed_environment_check_exits_non_zero_and_serves_nothing(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder(outcome=FAILING)
    assert main(_environ(tmp_path), recorder.dependencies(), recorder.serve) == 1
    assert recorder.served == [] and recorder.loads == []
    assert "CUDA is not available" in capsys.readouterr().err


def test_weights_that_fail_verification_exit_non_zero_and_serve_nothing(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder(load_error=RuntimeError("weights SHA-256 does not match"))
    assert main(_environ(tmp_path), recorder.dependencies(), recorder.serve) == 1
    assert recorder.served == []
    assert "SHA-256" in capsys.readouterr().err


def test_bad_settings_exit_non_zero_naming_the_variable_and_do_nothing(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder()
    environ = _environ(tmp_path)
    environ["OPENMOT_PRECISION"] = "int4"
    assert main(environ, recorder.dependencies(), recorder.serve) == 2
    assert recorder.served == [] and recorder.loads == []
    assert "OPENMOT_PRECISION" in capsys.readouterr().err


def test_a_missing_storage_variable_is_reported(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder()
    environ = _environ(tmp_path)
    del environ["OPENMOT_STORAGE_DIR"]
    assert main(environ, recorder.dependencies(), recorder.serve) == 2
    assert "OPENMOT_STORAGE_DIR" in capsys.readouterr().err
