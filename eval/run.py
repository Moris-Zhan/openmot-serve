import argparse
import json
import platform
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch

from eval.detection_cache import CacheKey, detect_with_cache
from eval.halves import HalfError, materialize_half
from eval.scoring import ScoreReport, Scores, ScoringError, score_sequences
from eval.split import split_sequence
from eval.tuning import TuningError, TuningResult, TuningSequence, tune_parameters, write_params
from tracking.bytetrack import run_tracker
from tracking.config import SUPPORTED_PRECISIONS
from tracking.detector import DetectorSettings
from tracking.env_check import (
    CheckOutcome,
    EnvProbe,
    format_outcome,
    read_torch_report,
    run_env_check,
    torch_gpu_matches_cpu,
)
from tracking.files import write_text_atomic
from tracking.motchallenge import format_result
from tracking.run import DEFAULT_WEIGHTS, DetectorLoader, load_default_detect_frame
from tracking.sequence import DetectFrame, SequenceError, list_frames
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256, WeightsIntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEQUENCES = tuple(f"MOT17-{number}-FRCNN" for number in ("02", "04", "05", "09", "10", "11", "13"))


class BenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True)
class BenchmarkConfig:
    data_root: Path
    results_root: Path
    run_name: str
    precision: str
    weights: Path
    device: str
    sequences: tuple[str, ...] = DEFAULT_SEQUENCES


@dataclass(frozen=True)
class Dependencies:
    check_environment: Callable[[], CheckOutcome]
    load_detect_frame: DetectorLoader


def default_dependencies() -> Dependencies:
    return Dependencies(
        check_environment=lambda: run_env_check(EnvProbe(read_torch_report, torch_gpu_matches_cpu)),
        load_detect_frame=load_default_detect_frame,
    )


def _missing_sequences(config: BenchmarkConfig) -> list[str]:
    def complete(name: str) -> bool:
        sequence = config.data_root / "train" / name
        return all(
            (sequence / part).exists() for part in ("seqinfo.ini", "gt/gt.txt", "img1")
        )

    return [name for name in config.sequences if not complete(name)]


def _lazy_detect_frame(loader: DetectorLoader, config: BenchmarkConfig) -> DetectFrame:
    loaded: list[DetectFrame] = []

    def detect_frame(image: np.ndarray) -> np.ndarray:
        if not loaded:
            loaded.append(loader(config.weights, config.precision, config.device))
        return loaded[0](image)

    return detect_frame


def _frame_count(config: BenchmarkConfig, name: str) -> int:
    return len(list_frames(config.data_root / "train" / name / "img1"))


def _tune(
    config: BenchmarkConfig, run_dir: Path, key: CacheKey, cache_dir: Path, detect_frame: DetectFrame
) -> TuningResult:
    sequences = []
    for name in config.sequences:
        tuning_half, _ = split_sequence(_frame_count(config, name))
        destination = run_dir / "halves" / name / "tuning"
        materialize_half(config.data_root / "train" / name, tuning_half, destination)
        detections = detect_with_cache(
            cache_dir, key, f"{name}-tuning", destination / "img1", detect_frame
        )
        sequences.append(TuningSequence(name, tuning_half, destination, detections))
    return tune_parameters(sequences, run_dir / "tuning")


def _validate(
    config: BenchmarkConfig,
    run_dir: Path,
    key: CacheKey,
    cache_dir: Path,
    detect_frame: DetectFrame,
    tuning: TuningResult,
) -> ScoreReport:
    sequence_dirs: dict[str, Path] = {}
    result_files: dict[str, Path] = {}
    for name in config.sequences:
        _, validation_half = split_sequence(_frame_count(config, name))
        destination = run_dir / "halves" / name / "validation"
        materialize_half(config.data_root / "train" / name, validation_half, destination)
        detections = detect_with_cache(
            cache_dir, key, f"{name}-validation", destination / "img1", detect_frame
        )
        result_file = run_dir / "results" / f"{name}.txt"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(result_file, format_result(run_tracker(detections, tuning.best)))
        sequence_dirs[name] = destination
        result_files[name] = result_file
    return score_sequences(sequence_dirs, result_files, run_dir / "score")


def _scores_document(scores: Scores) -> dict[str, float]:
    return {"hota": scores.hota, "mota": scores.mota, "idf1": scores.idf1}


def _summary_document(
    settings: DetectorSettings, tuning: TuningResult, report: ScoreReport, outcome: CheckOutcome
) -> dict[str, object]:
    environment = outcome.report
    return {
        "weights": {"id": WEIGHTS_ID, "sha256": WEIGHTS_SHA256},
        "precision": settings.precision,
        "detector": asdict(settings),
        "tracker_params": asdict(tuning.best),
        "tuning": {"combined_hota": tuning.best_hota, "combinations": len(tuning.trials)},
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "torch": environment.torch_version,
            "cuda": environment.cuda_version,
            "driver": environment.driver_version,
        },
        "gpu": environment.gpu_name,
        "scores": {
            "per_sequence": {name: _scores_document(scores) for name, scores in report.per_sequence.items()},
            "combined": _scores_document(report.combined),
        },
    }


def _markdown_table(report: ScoreReport) -> str:
    def row(label: str, scores: Scores) -> str:
        return f"| {label} | {scores.hota:.2f} | {scores.mota:.2f} | {scores.idf1:.2f} |"

    lines = ["| 序列 | HOTA | MOTA | IDF1 |", "| --- | ---: | ---: | ---: |"]
    lines += [row(name, scores) for name, scores in report.per_sequence.items()]
    lines.append(row("合併", report.combined))
    return "\n".join(lines) + "\n"


def execute(config: BenchmarkConfig, dependencies: Dependencies) -> str:
    missing = _missing_sequences(config)
    if missing:
        raise BenchmarkError(
            f"MOT17 sequences not found under {config.data_root}: {', '.join(missing)}. "
            "Run data/download_mot.sh to download them."
        )
    outcome = dependencies.check_environment()
    if not outcome.passed:
        raise BenchmarkError("environment check failed:\n" + format_outcome(outcome))
    run_dir = config.results_root / config.run_name
    if run_dir.exists():
        raise BenchmarkError(f"run folder already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    settings = DetectorSettings(precision=config.precision)
    key = CacheKey.from_settings(settings, WEIGHTS_SHA256)
    cache_dir = config.results_root / "cache"
    detect_frame = _lazy_detect_frame(dependencies.load_detect_frame, config)

    tuning = _tune(config, run_dir, key, cache_dir, detect_frame)
    write_params(run_dir / "params.json", tuning.best)
    report = _validate(config, run_dir, key, cache_dir, detect_frame, tuning)

    summary = _summary_document(settings, tuning, report, outcome)
    write_text_atomic(run_dir / "summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    table = _markdown_table(report)
    write_text_atomic(run_dir / "summary.md", table)
    return table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.run",
        description="Tune on the Tuning Half, score on the Validation Half and write summary.json.",
    )
    parser.add_argument("--precision", choices=SUPPORTED_PRECISIONS, default="fp16")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--device", help="torch device, default cuda when available")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data" / "MOT17")
    parser.add_argument("--results-root", type=Path, default=PROJECT_ROOT / "data" / "results")
    parser.add_argument("--run-name", help="folder name under the results root, default a UTC timestamp")
    parser.add_argument("--sequences", nargs="+", default=list(DEFAULT_SEQUENCES))
    return parser


def main(argv: Sequence[str] | None = None, dependencies: Dependencies | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config = BenchmarkConfig(
        data_root=arguments.data_root,
        results_root=arguments.results_root,
        run_name=arguments.run_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        precision=arguments.precision,
        weights=arguments.weights,
        device=arguments.device or ("cuda" if torch.cuda.is_available() else "cpu"),
        sequences=tuple(arguments.sequences),
    )
    try:
        table = execute(config, dependencies or default_dependencies())
    except (
        BenchmarkError,
        HalfError,
        ScoringError,
        TuningError,
        SequenceError,
        WeightsIntegrityError,
        FileNotFoundError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(table, end="")
    print(f"summary written to {config.results_root / config.run_name / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
