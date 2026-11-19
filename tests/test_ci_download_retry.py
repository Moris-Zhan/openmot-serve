import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
INSTALL_STEPS = ("Install kubectl, kustomize and kubeconform", "Install promtool")
FUNCTION = re.compile(r"^download\(\) \{\n.*?^\}\n", re.M | re.S)


def _step_script(name: str) -> str:
    return next(step["run"] for step in WORKFLOW["jobs"]["test"]["steps"] if step.get("name") == name)


def _function(script: str) -> str:
    matched = FUNCTION.search(script)
    assert matched, "the step does not define a download function"
    return matched.group(0)


class _Server:
    def __init__(self, failures_before_success: int | None) -> None:
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests += 1
                if failures_before_success is None or outer.requests <= failures_before_success:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"rate limited")
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"payload")

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/tool"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def _run_download(tmp_path: Path, failures_before_success: int | None) -> tuple[subprocess.CompletedProcess, _Server]:
    server = _Server(failures_before_success)
    try:
        # The real delay is seconds; the test only needs the retry behavior.
        function = _function(_step_script(INSTALL_STEPS[0])).replace("--retry-delay 5", "--retry-delay 0")
        script = f'{function}\ncd "$1"\ndownload out.bin "$2"\n'
        result = subprocess.run(
            ["bash", "-c", script, "bash", str(tmp_path), server.url],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.close()
    return result, server


@pytest.mark.parametrize("step", INSTALL_STEPS)
def test_each_install_step_defines_the_same_download_function(step: str) -> None:
    assert _function(_step_script(step)) == _function(_step_script(INSTALL_STEPS[0]))


@pytest.mark.parametrize("step", INSTALL_STEPS)
def test_no_download_bypasses_the_function(step: str) -> None:
    body = FUNCTION.sub("", _step_script(step))
    assert "curl" not in body
    assert re.findall(r"^download \S+ https://", body, re.M), "the step downloads nothing through download"


def test_every_download_is_still_checked_against_a_sha256() -> None:
    for step in INSTALL_STEPS:
        script = _step_script(step)
        downloaded = re.findall(r"^download (\S+) https://", script, re.M)
        checked = re.findall(r'^echo "[0-9a-f]{64}  (\S+)" \| sha256sum --check$', script, re.M)
        assert downloaded == checked


def test_a_transient_403_is_retried_until_the_download_succeeds(tmp_path: Path) -> None:
    result, server = _run_download(tmp_path, failures_before_success=2)
    assert result.returncode == 0, result.stderr
    assert server.requests == 3
    assert (tmp_path / "out.bin").read_bytes() == b"payload"


def test_a_permanent_403_fails_and_names_the_status_and_the_url(tmp_path: Path) -> None:
    result, server = _run_download(tmp_path, failures_before_success=None)
    assert result.returncode != 0
    assert server.requests == 6  # the first attempt plus five retries
    output = result.stdout + result.stderr
    assert "403" in output and server.url in output
