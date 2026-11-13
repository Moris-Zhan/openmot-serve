import argparse
import json
import platform
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import cycle, islice
from pathlib import Path

import cv2
import numpy as np
import torch

from eval.split import split_sequence
from tracking.config import SUPPORTED_PRECISIONS
from tracking.detector import DetectorSettings, StagedDetector, load_detector, torch_staged_detector
from tracking.env_check import (
    CheckOutcome,
    EnvProbe,
    format_outcome,
    read_torch_report,
    run_env_check,
    torch_gpu_matches_cpu,
)
from tracking.files import write_text_atomic
from tracking.run import DEFAULT_WEIGHTS
from tracking.sequence import SequenceError, list_frames
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256, WeightsIntegrityError

MILLISECONDS = 1000.0
P95 = 95
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEQUENCES = tuple(f"MOT17-{number}-FRCNN" for number in ("02", "04", "05", "09", "10", "11", "13"))
DEFAULT_WARMUP = 50
DEFAULT_MEASURED = 500

StagedLoader = Callable[[Path, str, str], StagedDetector]


class LatencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class LatencyStats:
    median_ms: float
    p95_ms: float


@dataclass(frozen=True)
class LatencyResult:
    end_to_end: LatencyStats
    inference: LatencyStats
    throughput_fps: float
    warmup_frames: int
    measured_frames: int


def _stats(seconds: Sequence[float]) -> LatencyStats:
    milliseconds = np.asarray(seconds) * MILLISECONDS
    return LatencyStats(float(np.median(milliseconds)), float(np.percentile(milliseconds, P95)))


def _end_to_end_seconds(
    staged: StagedDetector, frames: Sequence[np.ndarray], clock: Callable[[], float]
) -> list[float]:
    elapsed = []
    for image in frames:
        staged.synchronize()
        started = clock()
        prepared = staged.prepare(image)
        staged.finish(staged.infer(prepared), prepared)
        staged.synchronize()
        elapsed.append(clock() - started)
    return elapsed


def _inference_seconds(
    staged: StagedDetector, frames: Sequence[np.ndarray], clock: Callable[[], float]
) -> list[float]:
    elapsed = []
    for image in frames:
        prepared = staged.prepare(image)
        staged.synchronize()
        started = clock()
        staged.infer(prepared)
        staged.synchronize()
        elapsed.append(clock() - started)
    return elapsed


def measure_latency(
    staged: StagedDetector,
    frames: Sequence[np.ndarray],
    warmup: int,
    measured: int,
    clock: Callable[[], float] = time.perf_counter,
) -> LatencyResult:
    if not frames:
        raise LatencyError("no frames to measure")
    for image in islice(cycle(frames), warmup):
        prepared = staged.prepare(image)
        staged.finish(staged.infer(prepared), prepared)
    staged.synchronize()
    measured_frames = list(islice(cycle(frames), measured))
    end_to_end = _end_to_end_seconds(staged, measured_frames, clock)
    inference = _inference_seconds(staged, measured_frames, clock)
    return LatencyResult(
        end_to_end=_stats(end_to_end),
        inference=_stats(inference),
        throughput_fps=measured / sum(end_to_end),
        warmup_frames=warmup,
        measured_frames=measured,
    )


def validation_frame_paths(data_root: Path, sequences: Sequence[str], count: int) -> tuple[Path, ...]:
    paths: list[Path] = []
    for name in sequences:
        frames_dir = data_root / "train" / name / "img1"
        _, validation_half = split_sequence(len(list_frames(frames_dir)))
        paths.extend(frames_dir / f"{number:06d}.jpg" for number in validation_half.frames())
    return tuple(islice(cycle(paths), count))


def load_frames(paths: Sequence[Path]) -> list[np.ndarray]:
    loaded: dict[Path, np.ndarray] = {}
    for path in paths:
        if path not in loaded:
            image = cv2.imread(str(path))
            if image is None:
                raise LatencyError(f"cannot read frame {path}")
            loaded[path] = image
    return [loaded[path] for path in paths]


@dataclass(frozen=True)
class Dependencies:
    check_environment: Callable[[], CheckOutcome]
    load_staged: StagedLoader
    clock: Callable[[], float] = time.perf_counter


@dataclass(frozen=True)
class LatencyConfig:
    data_root: Path
    results_root: Path
    run_name: str
    precision: str
    weights: Path
    device: str
    sequences: tuple[str, ...]
    warmup: int
    measured: int

    @property
    def result_dir(self) -> Path:
        return self.results_root / "latency" / self.run_name


def _load_torch_staged(weights: Path, precision: str, device: str) -> StagedDetector:
    return torch_staged_detector(load_detector(weights, DetectorSettings(precision=precision), device))


def default_dependencies() -> Dependencies:
    return Dependencies(
        check_environment=lambda: run_env_check(EnvProbe(read_torch_report, torch_gpu_matches_cpu)),
        load_staged=_load_torch_staged,
    )


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive integer")
    return value


def _stats_document(stats: LatencyStats) -> dict[str, float]:
    return {"median_ms": stats.median_ms, "p95_ms": stats.p95_ms}


def _result_document(
    config: LatencyConfig, settings: DetectorSettings, result: LatencyResult, outcome: CheckOutcome
) -> dict[str, object]:
    environment = outcome.report
    return {
        "backend": settings.backend,
        "precision": settings.precision,
        "detector": asdict(settings),
        "weights": {"id": WEIGHTS_ID, "sha256": WEIGHTS_SHA256},
        "sequences": list(config.sequences),
        "warmup_frames": result.warmup_frames,
        "measured_frames": result.measured_frames,
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "torch": environment.torch_version,
            "cuda": environment.cuda_version,
            "driver": environment.driver_version,
        },
        "gpu": environment.gpu_name,
        "results": {
            "inference": _stats_document(result.inference),
            "end_to_end": _stats_document(result.end_to_end),
            "throughput_fps": result.throughput_fps,
        },
    }


def _markdown_table(result: LatencyResult) -> str:
    def row(label: str, stats: LatencyStats) -> str:
        return f"| {label} | {stats.median_ms:.2f} | {stats.p95_ms:.2f} |"

    lines = [
        "| 層級 | 中位數 (ms) | p95 (ms) |",
        "| --- | ---: | ---: |",
        row("純推論", result.inference),
        row("偵測端到端", result.end_to_end),
        "",
        f"吞吐（單串流、batch 1）：{result.throughput_fps:.1f} FPS",
    ]
    return "\n".join(lines) + "\n"


def _missing_sequences(config: LatencyConfig) -> list[str]:
    return [name for name in config.sequences if not (config.data_root / "train" / name / "img1").is_dir()]


def execute(config: LatencyConfig, dependencies: Dependencies) -> str:
    missing = _missing_sequences(config)
    if missing:
        raise LatencyError(
            f"MOT17 sequences not found under {config.data_root}: {', '.join(missing)}. "
            "Run data/download_mot.sh to download them."
        )
    outcome = dependencies.check_environment()
    if not outcome.passed:
        raise LatencyError("environment check failed:\n" + format_outcome(outcome))
    if config.result_dir.exists():
        raise LatencyError(f"result folder already exists: {config.result_dir}")

    settings = DetectorSettings(precision=config.precision)
    paths = validation_frame_paths(config.data_root, config.sequences, config.warmup + config.measured)
    frames = load_frames(paths)
    staged = dependencies.load_staged(config.weights, config.precision, config.device)
    result = measure_latency(staged, frames, config.warmup, config.measured, dependencies.clock)

    config.result_dir.mkdir(parents=True)
    document = _result_document(config, settings, result, outcome)
    write_text_atomic(config.result_dir / "latency.json", json.dumps(document, indent=2, sort_keys=True) + "\n")
    table = _markdown_table(result)
    write_text_atomic(config.result_dir / "latency.md", table)
    return table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.latency",
        description="Measure detector latency and throughput on Validation Half frames, one frame at a time.",
    )
    parser.add_argument("--precision", choices=SUPPORTED_PRECISIONS, default="fp16")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--device", help="torch device, default cuda when available")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data" / "MOT17")
    parser.add_argument("--results-root", type=Path, default=PROJECT_ROOT / "data" / "results")
    parser.add_argument("--run-name", help="folder name under <results-root>/latency, default a UTC timestamp")
    parser.add_argument("--sequences", nargs="+", default=list(DEFAULT_SEQUENCES))
    parser.add_argument("--warmup", type=_positive_int, default=DEFAULT_WARMUP, help="frames run before timing")
    parser.add_argument("--measured", type=_positive_int, default=DEFAULT_MEASURED, help="frames timed")
    return parser


def main(argv: Sequence[str] | None = None, dependencies: Dependencies | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config = LatencyConfig(
        data_root=arguments.data_root,
        results_root=arguments.results_root,
        run_name=arguments.run_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        precision=arguments.precision,
        weights=arguments.weights,
        device=arguments.device or ("cuda" if torch.cuda.is_available() else "cpu"),
        sequences=tuple(arguments.sequences),
        warmup=arguments.warmup,
        measured=arguments.measured,
    )
    try:
        table = execute(config, dependencies or default_dependencies())
    except (LatencyError, SequenceError, WeightsIntegrityError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(table, end="")
    print(f"result written to {config.result_dir / 'latency.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
