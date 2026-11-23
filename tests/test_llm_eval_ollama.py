import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from llm_eval.ollama import OllamaClient, OllamaError
from llm_eval.protocol import MAX_TOOL_ROUNDS, SEED, run_trial
from llm_eval.questions import Question
from llm_eval.results import read_results
from llm_eval.tools import TOOL_SCHEMAS
from tests.test_llm_eval_oracle import HAND_ROWS, write_rows

FINAL = {"refused": False, "category": None, "value": 2, "unit": None, "definition": "raw count"}


def _tool_call(name: str, arguments: dict) -> dict:
    call = {"function": {"name": name, "arguments": arguments}}
    return {"message": {"role": "assistant", "content": "", "tool_calls": [call]}}


def _plain(text: str = "ok") -> dict:
    return {"message": {"role": "assistant", "content": text}}


def _final(payload: dict) -> dict:
    return {"message": {"role": "assistant", "content": json.dumps(payload)}}


class FakeOllama(ThreadingHTTPServer):
    def __init__(self, script: list) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.script = list(script)
        self.requests: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    server: FakeOllama

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        step = self.server.script.pop(0) if self.server.script else {"status": 500, "body": {"error": "no script"}}
        if isinstance(step, dict) and "status" in step:
            status, payload, delay = step["status"], step["body"], step.get("delay", 0)
        else:
            status, payload, delay = 200, step, 0
        time.sleep(delay)
        data = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def serve() -> Iterator:
    servers: list[FakeOllama] = []

    def start(script: list) -> tuple[FakeOllama, OllamaClient]:
        server = FakeOllama(script)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server, OllamaClient(f"http://127.0.0.1:{server.server_address[1]}", timeout=2.0)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def detections(tmp_path: Path):
    return read_results(write_rows(tmp_path / "r.txt", HAND_ROWS))


QUESTION = Question("q01", "real", "共有幾個不同的身分？", True, "count_unique_ids", {}, 3)


def test_the_request_pins_temperature_seed_tools_and_disables_streaming(serve, detections) -> None:
    server, client = serve([_tool_call("count_unique_ids", {}), _plain(), _final(FINAL)])
    run_trial(client, "m:1", QUESTION, detections, run=0)
    first = server.requests[0]
    assert first["model"] == "m:1" and first["stream"] is False
    assert first["options"]["temperature"] == 0 and first["options"]["seed"] == SEED
    assert [item["function"]["name"] for item in first["tools"]] == [item["function"]["name"] for item in TOOL_SCHEMAS]
    assert first["messages"][-1] == {"role": "user", "content": QUESTION.text}


def test_tool_calls_are_executed_and_the_result_is_sent_back_with_the_tool_role(serve, detections) -> None:
    server, client = serve([_tool_call("count_unique_ids", {"min_frames": 2}), _plain(), _final(FINAL)])
    trial = run_trial(client, "m", QUESTION, detections, run=1)
    assert trial.error is None and trial.run == 1 and trial.final == FINAL
    assert trial.calls[0].valid and trial.calls[0].result == {"count": 2}
    second_messages = server.requests[1]["messages"]
    tool_messages = [message for message in second_messages if message["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"]) == {"count": 2}


def test_the_last_request_asks_for_a_structured_answer_without_tools(serve, detections) -> None:
    server, client = serve([_tool_call("count_unique_ids", {}), _plain(), _final(FINAL)])
    run_trial(client, "m", QUESTION, detections, run=0)
    last = server.requests[-1]
    assert "tools" not in last
    assert last["format"]["type"] == "object" and "refused" in last["format"]["properties"]


def test_an_unknown_tool_is_recorded_as_invalid_and_the_model_is_told(serve, detections) -> None:
    server, client = serve([_tool_call("no_such_tool", {}), _plain(), _final(FINAL)])
    trial = run_trial(client, "m", QUESTION, detections, run=0)
    assert not trial.calls[0].valid and "unknown tool" in " ".join(trial.calls[0].problems)
    sent = [message for message in server.requests[1]["messages"] if message["role"] == "tool"]
    assert "unknown tool" in sent[0]["content"]


def test_more_than_four_tool_rounds_is_a_failed_trial_with_the_raw_responses_kept(serve, detections) -> None:
    script = [_tool_call("count_unique_ids", {}) for _ in range(MAX_TOOL_ROUNDS + 3)]
    server, client = serve(script)
    trial = run_trial(client, "m", QUESTION, detections, run=0)
    assert trial.error is not None and "tool rounds" in trial.error and trial.final is None
    assert len(server.requests) == MAX_TOOL_ROUNDS
    assert len(trial.raw) == MAX_TOOL_ROUNDS


def test_a_timeout_is_recorded_on_the_trial_and_does_not_raise(serve, detections) -> None:
    server, client = serve([{"status": 200, "body": _plain(), "delay": 1.0}])
    slow = OllamaClient(client.base_url, timeout=0.2)
    trial = run_trial(slow, "m", QUESTION, detections, run=0)
    assert trial.error is not None and "timed out" in trial.error.lower()


def test_a_non_200_response_is_recorded_on_the_trial(serve, detections) -> None:
    _, client = serve([{"status": 500, "body": {"error": "model not found"}}])
    trial = run_trial(client, "m", QUESTION, detections, run=0)
    assert trial.error is not None and "500" in trial.error and trial.final is None


def test_an_unparseable_final_answer_is_a_failed_trial(serve, detections) -> None:
    _, client = serve([_plain(), _final({"refused": False})])
    trial = run_trial(client, "m", QUESTION, detections, run=0)
    assert trial.error is not None and "final answer" in trial.error


def test_a_model_that_answers_without_tools_still_gets_the_structured_final_request(serve, detections) -> None:
    server, client = serve([_plain("我覺得是 3"), _final(FINAL)])
    trial = run_trial(client, "m", QUESTION, detections, run=0)
    assert trial.calls == () and trial.final == FINAL and len(server.requests) == 2


def test_every_raw_response_is_kept_and_the_latency_uses_the_injected_clock(serve, detections) -> None:
    ticks = iter([10.0, 14.5])
    _, client = serve([_tool_call("count_unique_ids", {}), _plain(), _final(FINAL)])
    trial = run_trial(client, "m", QUESTION, detections, run=0, clock=lambda: next(ticks))
    assert trial.latency_seconds == 4.5 and len(trial.raw) == 3


def test_the_client_wraps_connection_errors() -> None:
    with pytest.raises(OllamaError):
        OllamaClient("http://127.0.0.1:1", timeout=0.5).chat({"model": "m"})


def test_pulling_a_model_waits_much_longer_than_a_chat_request(monkeypatch) -> None:
    import urllib.request

    seen: dict[str, float] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request, timeout):
        seen[request.full_url.rsplit("/", 1)[-1]] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OllamaClient("http://127.0.0.1:1", timeout=30.0)
    client.chat({"model": "m"})
    client.pull("m")
    assert seen["chat"] == 30.0 and seen["pull"] >= 3600.0


def test_unload_all_unloads_every_loaded_model_and_waits_until_none_remain(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    listings = iter([{"models": [{"name": "a:1"}, {"name": "b:2"}]}, {"models": [{"name": "b:2"}]}, {"models": []}])

    def fake_request(self, method, path, payload=None, timeout=None):
        calls.append((method, path, payload))
        return next(listings) if path == "/api/ps" else {}

    monkeypatch.setattr(OllamaClient, "_request", fake_request)
    OllamaClient("http://127.0.0.1:1").unload_all(poll_seconds=0.0)
    generates = [payload for _, path, payload in calls if path == "/api/generate"]
    assert generates == [{"model": "a:1", "keep_alive": 0}, {"model": "b:2", "keep_alive": 0}]
    assert [path for _, path, _ in calls].count("/api/ps") == 3
