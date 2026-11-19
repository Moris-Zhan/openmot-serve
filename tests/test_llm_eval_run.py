import json
import subprocess
from pathlib import Path

import pytest

from llm_eval import run
from llm_eval.questions import Question, build_question_set, load_questions
from tests.test_llm_eval_oracle import HAND_ROWS, write_rows

MODEL = "good-model:7b"


class OracleClient:
    """A model that always answers perfectly: calls the right tool, then reports the gold answer."""

    def __init__(self, questions: list[Question]) -> None:
        self.questions = questions
        self.trial = -1
        self.requests: list[dict] = []

    def _current(self) -> Question:
        # Some questions share their text across sequences, so identify the question by the run order:
        # one warm-up trial of the first question, then three trials per question.
        return self.questions[0 if self.trial == 0 else (self.trial - 1) // 3]

    def chat(self, payload: dict) -> dict:
        self.requests.append(payload)
        if len(payload["messages"]) == 2:
            self.trial += 1
        question = self._current()
        if "format" in payload:
            if question.answerable:
                final = {"refused": False, "category": None, "value": question.gold, "unit": None, "definition": "raw"}
            elif question.frames_alternative is not None:
                final = {"refused": False, "category": None, "value": question.frames_alternative, "unit": "frames"}
                final["definition"] = None
            else:
                final = {"refused": True, "category": question.category, "value": None, "unit": None}
                final["definition"] = None
            return {"message": {"role": "assistant", "content": json.dumps(final)}}
        already_called = any(message["role"] == "tool" for message in payload["messages"])
        if question.answerable and not already_called:
            call = {"function": {"name": question.tool, "arguments": question.arguments}}
            return {"message": {"role": "assistant", "content": "", "tool_calls": [call]}}
        return {"message": {"role": "assistant", "content": "done"}}

    def unload_all(self) -> None:
        self.requests.append({"unload_all": True})

    def ps(self) -> dict:
        return {"models": [{"name": MODEL, "size_vram": 6 * 1024**3}]}

    def show(self, model: str) -> dict:
        return {"details": {"family": "x"}, "digest": "sha256:abc"}

    def pull(self, model: str) -> dict:
        self.requests.append({"pull": model})
        return {"status": "success"}


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    # extend the hand sequence so sampling three distinct ids and frames is possible
    rows = HAND_ROWS + [(f, 10 + f, 20.0 * f, 50.0, 30.0, 60.0) for f in range(6, 20)]
    real = write_rows(tmp_path / "real.txt", rows)
    questions_path = build_question_set(real, tmp_path / "set", seed=1)
    confirmed = tmp_path / "confirmed.json"
    confirmed.write_text(json.dumps({"models": [MODEL]}))
    return {
        "real": real,
        "questions": questions_path,
        "synthetic": tmp_path / "set" / "synthetic",
        "confirmed": confirmed,
        "out": tmp_path / "out",
    }


def _deps(client: object) -> run.Dependencies:
    return run.Dependencies(client_factory=lambda url: client, read_gpu_memory=lambda: 3000)


def _run_args(workspace: dict[str, Path], model: str = MODEL, confirmed: bool = True) -> list[str]:
    arguments = [
        "run", "--model", model, "--questions", str(workspace["questions"]), "--real", str(workspace["real"]),
        "--synthetic-dir", str(workspace["synthetic"]), "--output-dir", str(workspace["out"]),
    ]  # fmt: skip
    return arguments + (["--confirmed-candidates", str(workspace["confirmed"])] if confirmed else [])


def test_run_without_a_confirmed_candidates_file_is_refused(workspace: dict[str, Path], capsys) -> None:
    client = OracleClient(load_questions(workspace["questions"].read_text()))
    assert run.main(_run_args(workspace, confirmed=False), _deps(client)) != 0
    assert client.requests == []
    assert "confirmed" in capsys.readouterr().err


def test_run_for_a_model_that_is_not_on_the_confirmed_list_is_refused(workspace: dict[str, Path], capsys) -> None:
    client = OracleClient(load_questions(workspace["questions"].read_text()))
    assert run.main(_run_args(workspace, model="other:1b"), _deps(client)) != 0
    assert client.requests == []
    assert "other:1b" in capsys.readouterr().err


def test_pulling_is_refused_without_confirmation_and_allowed_for_a_confirmed_model(workspace: dict[str, Path]) -> None:
    client = OracleClient([])
    confirmed = str(workspace["confirmed"])
    assert run.main(["pull", "--model", MODEL], _deps(client)) != 0
    assert run.main(["pull", "--model", "other:1b", "--confirmed-candidates", confirmed], _deps(client)) != 0
    assert client.requests == []
    assert run.main(["pull", "--model", MODEL, "--confirmed-candidates", confirmed], _deps(client)) == 0
    assert client.requests == [{"pull": MODEL}]


def test_a_confirmed_run_writes_raw_trials_and_a_summary(workspace: dict[str, Path]) -> None:
    questions = load_questions(workspace["questions"].read_text())
    client = OracleClient(questions)
    assert run.main(_run_args(workspace), _deps(client)) == 0
    raw = json.loads((workspace["out"] / "raw" / "good-model_7b.json").read_text())
    summary = json.loads((workspace["out"] / "summary" / "good-model_7b.json").read_text())
    non_warmup = [trial for trial in raw["trials"] if not trial["warmup"]]
    assert len(non_warmup) == 3 * len(questions) and any(trial["warmup"] for trial in raw["trials"])
    assert summary["model"] == MODEL and summary["runs"] == 3
    metrics = summary["metrics"]
    assert metrics["answer_correctness"] == 1.0 and metrics["refusal_correctness"] == 1.0
    assert metrics["tool_format_correctness"] == 1.0 and metrics["unstable_questions"] == 0
    assert summary["vram"]["size_vram_gib"] == 6.0 and summary["model_info"]["digest"] == "sha256:abc"
    assert summary["passed"] is True and summary["failed_thresholds"] == []


def test_a_failing_model_reports_which_thresholds_it_missed(workspace: dict[str, Path]) -> None:
    questions = load_questions(workspace["questions"].read_text())

    class Refuser(OracleClient):
        def chat(self, payload: dict) -> dict:
            if "format" in payload:
                final = {"refused": True, "category": "prediction", "value": None, "unit": None, "definition": None}
                return {"message": {"role": "assistant", "content": json.dumps(final)}}
            return {"message": {"role": "assistant", "content": "no"}}

    assert run.main(_run_args(workspace), _deps(Refuser(questions))) == 0
    summary = json.loads((workspace["out"] / "summary" / "good-model_7b.json").read_text())
    assert summary["passed"] is False
    assert {"answer_correctness", "tool_format_correctness"} <= set(summary["failed_thresholds"])


def test_select_picks_from_the_summaries_and_reports_none_when_nothing_passes(
    workspace: dict[str, Path], capsys
) -> None:
    questions = load_questions(workspace["questions"].read_text())
    assert run.main(_run_args(workspace), _deps(OracleClient(questions))) == 0
    capsys.readouterr()
    assert run.main(["select", "--summaries", str(workspace["out"] / "summary")], _deps(OracleClient([]))) == 0
    assert json.loads(capsys.readouterr().out)["chosen"] == MODEL
    failing = workspace["out"] / "summary" / "good-model_7b.json"
    document = json.loads(failing.read_text())
    document["metrics"]["answer_correctness"] = 0.1
    failing.write_text(json.dumps(document))
    assert run.main(["select", "--summaries", str(workspace["out"] / "summary")], _deps(OracleClient([]))) == 0
    assert json.loads(capsys.readouterr().out)["chosen"] is None


def test_questions_subcommand_writes_the_question_set(tmp_path: Path, workspace: dict[str, Path]) -> None:
    target = tmp_path / "generated"
    assert run.main(["questions", "--real", str(workspace["real"]), "--output-dir", str(target)], _deps(None)) == 0
    assert (target / "questions.json").is_file()


def test_measure_worker_runs_the_latency_tool_and_records_the_memory_deltas(tmp_path: Path) -> None:
    results_root, commands = tmp_path / "results", []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        run_name = command[command.index("--run-name") + 1]
        folder = results_root / "latency" / run_name
        folder.mkdir(parents=True)
        document = {"gpu_memory": {"available": True, "load_delta_mib": 2500, "run_delta_mib": 3100}}
        (folder / "latency.json").write_text(json.dumps(document))
        return subprocess.CompletedProcess(command, 0, "", "")

    arguments = ["measure-worker", "--output-dir", str(tmp_path / "out"), "--results-root", str(results_root)]
    assert run.main(arguments, run.Dependencies(runner=runner)) == 0
    assert commands[0][1:3] == ["-m", "eval.latency"] and "fp16" in commands[0]
    recorded = json.loads((tmp_path / "out" / "worker_memory.json").read_text())
    assert recorded["load_delta_mib"] == 2500 and recorded["run_delta_mib"] == 3100


def test_measure_worker_fails_when_the_gpu_memory_is_unavailable(tmp_path: Path, capsys) -> None:
    results_root = tmp_path / "results"

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        folder = results_root / "latency" / command[command.index("--run-name") + 1]
        folder.mkdir(parents=True)
        (folder / "latency.json").write_text(json.dumps({"gpu_memory": {"available": False}}))
        return subprocess.CompletedProcess(command, 0, "", "")

    arguments = ["measure-worker", "--output-dir", str(tmp_path / "out"), "--results-root", str(results_root)]
    assert run.main(arguments, run.Dependencies(runner=runner)) != 0
    assert "gpu memory" in capsys.readouterr().err.lower()


def test_cleanup_removes_only_dedicated_resources(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    assert run.main(["cleanup"], run.Dependencies(runner=runner)) == 0
    assert commands == [
        ["docker", "rm", "-f", "openmot-llm-eval"],
        ["docker", "volume", "rm", "openmot-llm-eval-models"],
    ]
    assert run.main(["cleanup", "--keep-models"], run.Dependencies(runner=runner)) == 0
    assert commands[-1] == ["docker", "rm", "-f", "openmot-llm-eval"]
