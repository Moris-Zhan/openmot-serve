import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.run import build_app, main
from jobs.settings import load_settings


class StubRedis:
    def __init__(self, ping_error: Exception | None = None) -> None:
        self.ping_error = ping_error
        self.pings = 0

    def ping(self) -> bool:
        self.pings += 1
        if self.ping_error is not None:
            raise self.ping_error
        return True

    def register_script(self, script: str):  # type: ignore[no-untyped-def]
        return lambda **kwargs: 0


def _environ(tmp_path: Path) -> dict[str, str]:
    return {"OPENMOT_STORAGE_DIR": str(tmp_path / "storage")}


def test_health_goes_through_the_redis_connection(tmp_path: Path) -> None:
    redis_client = StubRedis()
    app = build_app(load_settings(_environ(tmp_path)), redis_client)  # type: ignore[arg-type]
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert redis_client.pings == 1


def test_an_unreachable_redis_makes_health_unavailable(tmp_path: Path) -> None:
    app = build_app(load_settings(_environ(tmp_path)), StubRedis(ConnectionError("refused")))  # type: ignore[arg-type]
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert "refused" in response.json()["problems"][0]


def test_limits_come_from_the_settings(tmp_path: Path) -> None:
    environ = _environ(tmp_path) | {"OPENMOT_MAX_UPLOAD_BYTES": "10"}
    app = build_app(load_settings(environ), StubRedis())  # type: ignore[arg-type]
    response = TestClient(app).post("/jobs", files={"file": ("f.zip", b"x" * 100)})
    assert response.status_code == 413


def test_bad_settings_exit_with_two_and_do_not_serve(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    served: list[object] = []
    assert main({}, serve=lambda app: served.append(app)) == 2
    assert served == []
    assert "OPENMOT_STORAGE_DIR" in capsys.readouterr().err


def test_good_settings_serve_the_app(tmp_path: Path) -> None:
    served: list[object] = []
    code = main(_environ(tmp_path), serve=lambda app: served.append(app), redis_factory=lambda url: StubRedis())  # type: ignore[arg-type,return-value]
    assert code == 0
    assert len(served) == 1


def test_the_api_entrypoint_does_not_pull_in_torch() -> None:
    code = "import sys; import app.run; sys.exit(1 if 'torch' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
