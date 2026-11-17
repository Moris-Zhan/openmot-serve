import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import torch

from inference.artifacts import ArtifactError
from inference.backends import BackendError
from tracking.config import (
    SUPPORTED_BACKENDS,
    SUPPORTED_PRECISIONS,
    ConfigError,
    RunMetadata,
    load_tracker_params,
)
from tracking.detector import DetectorSettings, detect, load_detector
from tracking.sequence import SequenceError, list_frames, track_sequence
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256, WeightsIntegrityError

DEFAULT_WEIGHTS = Path(__file__).resolve().parents[1] / "models" / "yolox_x.pth"

class DetectorLoader(Protocol):
    def __call__(
        self, weights: Path, precision: str, device: str, backend: str = "torch", engine: Path | None = None
    ) -> Callable[[np.ndarray], np.ndarray]: ...


def load_default_detect_frame(
    weights: Path, precision: str, device: str, backend: str = "torch", engine: Path | None = None
) -> Callable[[np.ndarray], np.ndarray]:
    settings = DetectorSettings(precision=precision, backend=backend)
    if backend == "torch":
        detector = load_detector(weights, settings, device)
        return lambda image: detect(detector, image)
    from inference import backends
    from tracking.detector import run_staged

    artifact = backends.resolve_backend_artifact(backend, settings, weights, engine)
    staged = backends.load_staged_detector(backend, weights, settings, device, artifact)
    return lambda image: run_staged(staged, image)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tracking.run",
        description="Detect and track people in a folder of numbered frames and write a MOTChallenge result.",
    )
    parser.add_argument("frames_dir", type=Path, help="folder of consecutively numbered frames")
    parser.add_argument("--output", type=Path, required=True, help="result file to write, e.g. out/MOT17-09.txt")
    parser.add_argument("--config", type=Path, help="TOML file with a [tracker] table")
    parser.add_argument("--precision", choices=SUPPORTED_PRECISIONS, default="fp16")
    parser.add_argument("--backend", choices=SUPPORTED_BACKENDS, default="torch")
    parser.add_argument("--engine", type=Path, help="explicit ONNX or engine file, default found by naming convention")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--device", help="torch device, default cuda when available")
    return parser


def main(
    argv: Sequence[str] | None = None, load_detect_frame: DetectorLoader = load_default_detect_frame
) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        params = load_tracker_params(arguments.config)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        list_frames(arguments.frames_dir)
        DetectorSettings(precision=arguments.precision, backend=arguments.backend)
        device = arguments.device or ("cuda" if torch.cuda.is_available() else "cpu")
        detect_frame = load_detect_frame(
            arguments.weights, arguments.precision, device, arguments.backend, arguments.engine
        )
        metadata = RunMetadata(
            params, WEIGHTS_ID, WEIGHTS_SHA256, arguments.precision, arguments.backend
        )
        summary = track_sequence(arguments.frames_dir, arguments.output, detect_frame, metadata)
    except (
        SequenceError,
        WeightsIntegrityError,
        ArtifactError,
        BackendError,
        FileNotFoundError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {summary.row_count} rows for {summary.frame_count} frames to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
