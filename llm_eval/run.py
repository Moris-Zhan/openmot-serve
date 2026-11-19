import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llm_eval import docker
from llm_eval.ollama import OllamaClient, OllamaError
from llm_eval.protocol import run_trial
from llm_eval.questions import DEFAULT_SEED, build_question_set, load_questions
from llm_eval.results import read_results
from llm_eval.score import Trial, aggregate
from llm_eval.thresholds import (
    ANSWER_THRESHOLD,
    REFUSAL_THRESHOLD,
    TOOL_FORMAT_THRESHOLD,
    VRAM_LIMIT_GIB,
    CandidateMetrics,
    passes,
    select_candidate,
)

DEFAULT_RUNS = 3
DEFAULT_RESULTS_ROOT = Path("data/results")
DEFAULT_BASE_URL = f"http://{docker.HOST}:{docker.DEFAULT_PORT}"
PULL_TIMEOUT_SECONDS = 7200.0
GIB_MIB = 1024
MIB = 1024 * 1024
DEFAULT_IDLE_TOLERANCE_MIB = 2048
DEFAULT_POLL_SECONDS = 30.0
DEFAULT_MAX_WAIT_SECONDS = 43200.0
MAX_BLOCK_ATTEMPTS = 3
ANNOUNCE_EVERY_POLLS = 20


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _default_client(url: str) -> OllamaClient:
    return OllamaClient(url)


def _default_gpu_reading() -> int | None:
    from eval.latency import read_gpu_memory_mib

    return read_gpu_memory_mib("cuda")


@dataclass(frozen=True)
class Dependencies:
    client_factory: Callable[[str], Any] = _default_client
    runner: docker.Runner = _subprocess_runner
    read_gpu_memory: Callable[[], int | None] = _default_gpu_reading
    sleep: Callable[[float], None] = time.sleep


def _fail(message: str, code: int = 2) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def _confirmed_models(path: Path | None) -> list[str] | str:
    if path is None:
        return "a confirmed candidates file is required (--confirmed-candidates)"
    try:
        models = json.loads(path.read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"cannot read the confirmed candidates file {path}: {error}"
    return [str(model) for model in models]


def _check_confirmed(model: str, path: Path | None) -> str | None:
    models = _confirmed_models(path)
    if isinstance(models, str):
        return models
    if model not in models:
        return f"{model} is not on the user-confirmed candidate list {models}"
    return None


def _safe_name(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def _failed_thresholds(metrics: dict[str, Any], vram_gib: float | None) -> list[str]:
    failed = []
    if metrics["answer_correctness"] < ANSWER_THRESHOLD:
        failed.append("answer_correctness")
    if metrics["refusal_correctness"] < REFUSAL_THRESHOLD:
        failed.append("refusal_correctness")
    if metrics["tool_format_correctness"] < TOOL_FORMAT_THRESHOLD:
        failed.append("tool_format_correctness")
    if vram_gib is None or vram_gib > VRAM_LIMIT_GIB:
        failed.append("vram_gib")
    return failed


def _size_vram_gib(listing: dict[str, Any], model: str) -> float | None:
    for entry in listing.get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            return entry.get("size_vram", 0) / 1024**3
    return None


@dataclass
class IdleCheck:
    waits: int = 0
    waited_seconds: float = 0.0
    discarded_blocks: int = 0


def _other_gpu_use_mib(client: Any, dependencies: Dependencies) -> int | None:
    """GPU memory in use that does not belong to the model being evaluated."""
    used = dependencies.read_gpu_memory()
    if used is None:
        return None
    ours = 0
    if client is not None:
        ours = sum(entry.get("size_vram", 0) for entry in client.ps().get("models", [])) // MIB
    return used - ours


def _gpu_is_idle(client: Any, dependencies: Dependencies, tolerance_mib: int) -> bool:
    other = _other_gpu_use_mib(client, dependencies)
    return other is not None and other <= tolerance_mib


def _wait_for_idle(
    client: Any, dependencies: Dependencies, arguments: argparse.Namespace, check: IdleCheck
) -> str | None:
    waited, polls = 0.0, 0
    while True:
        other = _other_gpu_use_mib(client, dependencies)
        if other is None:
            return "cannot verify that the GPU is idle: its memory could not be read"
        if other <= arguments.idle_tolerance_mib:
            return None
        if waited >= arguments.max_wait_seconds:
            return f"the GPU was not idle after waiting {waited:.0f}s: another process uses {other} MiB"
        if polls % ANNOUNCE_EVERY_POLLS == 0:
            print(f"waiting for the GPU to become idle: another process uses {other} MiB", file=sys.stderr)
        dependencies.sleep(arguments.poll_seconds)
        waited += arguments.poll_seconds
        polls += 1
        check.waits += 1
        check.waited_seconds += arguments.poll_seconds


def _questions(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    path = build_question_set(arguments.real, arguments.output_dir, arguments.seed)
    print(path)
    return 0


def _measure_worker(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    problem = _wait_for_idle(None, dependencies, arguments, IdleCheck())
    if problem:
        return _fail(problem, 1)
    run_name = arguments.run_name or datetime.now(UTC).strftime("llm-eval-worker-%Y%m%dT%H%M%SZ")
    command = [
        sys.executable, "-m", "eval.latency", "--precision", "fp16", "--results-root", str(arguments.results_root),
        "--run-name", run_name, "--sequences", "MOT17-09-FRCNN", "--warmup", "5", "--measured", "20",
    ]  # fmt: skip
    completed = dependencies.runner(command)
    if completed.returncode != 0:
        return _fail(f"eval.latency failed: {completed.stderr.strip()[-300:]}", 1)
    document = json.loads((arguments.results_root / "latency" / run_name / "latency.json").read_text())
    memory = document.get("gpu_memory", {})
    if not memory.get("available"):
        return _fail("GPU memory could not be read, so the worker increment is unknown", 1)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    recorded = {
        "load_delta_mib": memory["load_delta_mib"],
        "run_delta_mib": memory["run_delta_mib"],
        "source_run": run_name,
        "precision": "fp16",
    }
    (arguments.output_dir / "worker_memory.json").write_text(json.dumps(recorded, indent=2) + "\n")
    print(json.dumps(recorded))
    return 0


def _snapshot(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(docker.snapshot_to_json(docker.take_snapshot(dependencies.runner)))
    return 0


def _compare(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    before = docker.snapshot_from_json(arguments.before.read_text())
    after = docker.snapshot_from_json(arguments.after.read_text())
    violations = docker.compare(before, after)
    for violation in violations:
        print(f"violation: {violation}")
    print("snapshots match apart from the allowed resources" if not violations else "")
    return 1 if violations else 0


def _up(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    try:
        command = docker.run_command(arguments.image, arguments.port)
    except docker.ResourceError as error:
        return _fail(str(error))
    completed = dependencies.runner(command)
    if completed.returncode != 0:
        return _fail(f"docker run failed: {completed.stderr.strip()[-300:]}", 1)
    return 0


def _pull(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    problem = _check_confirmed(arguments.model, arguments.confirmed_candidates)
    if problem:
        return _fail(problem)
    client = dependencies.client_factory(arguments.base_url)
    try:
        client.pull(arguments.model)
    except OllamaError as error:
        return _fail(f"pull failed: {error}", 1)
    return 0


def _run(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    problem = _check_confirmed(arguments.model, arguments.confirmed_candidates)
    if problem:
        return _fail(problem)
    questions = load_questions(arguments.questions.read_text(encoding="utf-8"))
    paths = {"real": arguments.real}
    paths.update({q.source: arguments.synthetic_dir / f"{q.source}.txt" for q in questions if q.source != "real"})
    detections = {source: read_results(path) for source, path in paths.items()}
    client = dependencies.client_factory(arguments.base_url)
    try:
        client.unload_all()
    except OllamaError as error:
        return _fail(f"cannot unload the previously loaded models: {error}", 1)
    check = IdleCheck()
    problem = _wait_for_idle(client, dependencies, arguments, check)
    if problem:
        return _fail(problem, 1)
    gpu_before = dependencies.read_gpu_memory()
    first = questions[0]
    trials: list[Trial] = [run_trial(client, arguments.model, first, detections[first.source], 0, warmup=True)]
    gpu_after = dependencies.read_gpu_memory()
    try:
        size_gib = _size_vram_gib(client.ps(), arguments.model)
        info = client.show(arguments.model)
    except OllamaError as error:
        return _fail(f"cannot read model information: {error}", 1)
    for question in questions:
        for _ in range(MAX_BLOCK_ATTEMPTS):
            problem = _wait_for_idle(client, dependencies, arguments, check)
            if problem:
                return _fail(problem, 1)
            block = [
                run_trial(client, arguments.model, question, detections[question.source], run_index)
                for run_index in range(arguments.runs)
            ]
            if _gpu_is_idle(client, dependencies, arguments.idle_tolerance_mib):
                trials.extend(block)
                break
            check.discarded_blocks += 1
        else:
            return _fail(f"the GPU was disturbed by another process while measuring {question.id}", 1)
    metrics = asdict(aggregate(questions, trials))
    gpu_delta = None if gpu_before is None or gpu_after is None else gpu_after - gpu_before
    candidates = [
        value for value in (size_gib, None if gpu_delta is None else gpu_delta / GIB_MIB) if value is not None
    ]
    vram_gib = max(candidates) if candidates else None
    failed = _failed_thresholds(metrics, vram_gib)
    name = _safe_name(arguments.model)
    summary = {
        "model": arguments.model,
        "runs": arguments.runs,
        "metrics": metrics,
        "model_info": {"digest": info.get("digest"), "details": info.get("details")},
        "vram": {"size_vram_gib": size_gib, "gpu_delta_mib": gpu_delta, "vram_gib": vram_gib},
        "thresholds": {
            "answer_correctness": ANSWER_THRESHOLD,
            "refusal_correctness": REFUSAL_THRESHOLD,
            "tool_format_correctness": TOOL_FORMAT_THRESHOLD,
            "vram_gib": VRAM_LIMIT_GIB,
        },
        "passed": not failed,
        "failed_thresholds": failed,
        "idle_check": {
            "tolerance_mib": arguments.idle_tolerance_mib,
            "waits": check.waits,
            "waited_seconds": check.waited_seconds,
            "discarded_blocks": check.discarded_blocks,
        },
    }
    for folder, document in (
        ("raw", {"model": arguments.model, "trials": [asdict(t) for t in trials]}),
        ("summary", summary),
    ):
        target = arguments.output_dir / folder
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"model": arguments.model, "passed": not failed, "failed_thresholds": failed}))
    return 0


def _select(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    candidates, report = [], []
    for path in sorted(arguments.summaries.glob("*.json")):
        summary = json.loads(path.read_text())
        metrics, vram = summary["metrics"], summary["vram"]["vram_gib"]
        candidate = CandidateMetrics(
            summary["model"],
            metrics["answer_correctness"],
            metrics["refusal_correctness"],
            metrics["tool_format_correctness"],
            metrics["latency_median_seconds"],
            float("inf") if vram is None else vram,
        )
        candidates.append(candidate)
        report.append({"model": candidate.name, "passed": passes(candidate)})
    selection = select_candidate(candidates)
    document = {
        "chosen": selection.chosen.name if selection.chosen else None,
        "passed": [candidate.name for candidate in selection.passed],
        "candidates": report,
    }
    print(json.dumps(document, ensure_ascii=False))
    return 0


def _cleanup(arguments: argparse.Namespace, dependencies: Dependencies) -> int:
    try:
        docker.cleanup(dependencies.runner, keep_models=arguments.keep_models)
    except docker.ResourceError as error:
        return _fail(str(error))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m llm_eval.run", description="Evaluate local LLM candidates.")
    commands = parser.add_subparsers(dest="command", required=True)

    questions = commands.add_parser("questions", help="generate the question set")
    questions.add_argument("--real", type=Path, required=True, help="the real result.txt the questions are based on")
    questions.add_argument("--output-dir", type=Path, required=True)
    questions.add_argument("--seed", type=int, default=DEFAULT_SEED)
    questions.set_defaults(handler=_questions)

    worker = commands.add_parser("measure-worker", help="measure the worker's FP16 GPU memory")
    worker.add_argument("--output-dir", type=Path, required=True)
    worker.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    worker.add_argument("--run-name")
    worker.add_argument("--idle-tolerance-mib", type=int, default=DEFAULT_IDLE_TOLERANCE_MIB)
    worker.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    worker.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)
    worker.set_defaults(handler=_measure_worker)

    snapshot = commands.add_parser("snapshot", help="save docker containers, volumes and images")
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.set_defaults(handler=_snapshot)

    compare = commands.add_parser("compare", help="compare two snapshots")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.set_defaults(handler=_compare)

    up = commands.add_parser("up", help="start the dedicated Ollama container")
    up.add_argument("--image", required=True, help="a pinned ollama/ollama tag")
    up.add_argument("--port", type=int, default=docker.DEFAULT_PORT)
    up.set_defaults(handler=_up)

    pull = commands.add_parser("pull", help="pull a user-confirmed model")
    pull.add_argument("--model", required=True)
    pull.add_argument("--confirmed-candidates", type=Path)
    pull.add_argument("--base-url", default=DEFAULT_BASE_URL)
    pull.set_defaults(handler=_pull)

    evaluate = commands.add_parser("run", help="evaluate one user-confirmed candidate")
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--confirmed-candidates", type=Path)
    evaluate.add_argument("--questions", type=Path, required=True)
    evaluate.add_argument("--real", type=Path, required=True)
    evaluate.add_argument("--synthetic-dir", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--base-url", default=DEFAULT_BASE_URL)
    evaluate.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    evaluate.add_argument("--idle-tolerance-mib", type=int, default=DEFAULT_IDLE_TOLERANCE_MIB)
    evaluate.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    evaluate.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)
    evaluate.set_defaults(handler=_run)

    select = commands.add_parser("select", help="apply the thresholds and the selection rule")
    select.add_argument("--summaries", type=Path, required=True)
    select.set_defaults(handler=_select)

    cleanup = commands.add_parser("cleanup", help="remove the dedicated container and volume")
    cleanup.add_argument("--keep-models", action="store_true", help="keep the models volume (ask the user first)")
    cleanup.set_defaults(handler=_cleanup)
    return parser


def main(argv: Sequence[str] | None = None, dependencies: Dependencies | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return arguments.handler(arguments, dependencies or Dependencies())


if __name__ == "__main__":
    sys.exit(main())
