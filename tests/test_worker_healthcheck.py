import subprocess
import sys
from pathlib import Path

from celery import Celery

from worker.healthcheck import main


class FakeControl:
    def __init__(self, replies: list[dict[str, object]]) -> None:
        self.replies = replies
        self.timeouts: list[float] = []

    def ping(self, timeout: float) -> list[dict[str, object]]:
        self.timeouts.append(timeout)
        return self.replies


class FakeApp:
    def __init__(self, replies: list[dict[str, object]]) -> None:
        self.control = FakeControl(replies)


def _environ(tmp_path: Path) -> dict[str, str]:
    return {"OPENMOT_STORAGE_DIR": str(tmp_path / "storage")}


def test_a_worker_that_answers_the_ping_is_healthy(tmp_path: Path) -> None:
    app = FakeApp([{"celery@host": {"ok": "pong"}}])
    assert main(_environ(tmp_path), lambda settings: app) == 0  # type: ignore[arg-type,return-value]
    assert app.control.timeouts == [5.0]


def test_no_answer_to_the_ping_is_unhealthy(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(_environ(tmp_path), lambda settings: FakeApp([])) == 1  # type: ignore[arg-type,return-value]
    assert "no worker answered" in capsys.readouterr().err


def test_a_broker_error_is_unhealthy_not_a_crash(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    def broken(settings: object) -> Celery:
        raise ConnectionError("redis down")

    assert main(_environ(tmp_path), broken) == 1  # type: ignore[arg-type]
    assert "redis down" in capsys.readouterr().err


def test_bad_settings_are_unhealthy(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main({}, lambda settings: FakeApp([])) == 1  # type: ignore[arg-type,return-value]
    assert "OPENMOT_STORAGE_DIR" in capsys.readouterr().err


def test_the_healthcheck_does_not_pull_in_torch() -> None:
    code = "import sys; import worker.healthcheck; sys.exit(1 if 'torch' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
