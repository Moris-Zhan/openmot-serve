import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import torch

from tracking.config import SUPPORTED_PRECISIONS, ConfigError, RunMetadata, load_tracker_params
from tracking.detector import DetectorSettings, detect, load_detector
from tracking.sequence import SequenceError, list_frames, track_sequence
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256, WeightsIntegrityError

DEFAULT_WEIGHTS = Path(__file__).resolve().parents[1] / "models" / "yolox_x.pth"

DetectorLoader = Callable[[Path, str, str], Callable[[np.ndarray], np.ndarray]]


def load_default_detect_frame(
    weights: Path, precision: str, device: str
) -> Callable[[np.ndarray], np.ndarray]:
    detector = load_detector(weights, DetectorSettings(precision=precision), device)
    return lambda image: detect(detector, image)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tracking.run",
        description="Detect and track people in a folder of numbered frames and write a MOTChallenge result.",
    )
    parser.add_argument("frames_dir", type=Path, help="folder of consecutively numbered frames")
    parser.add_argument("--output", type=Path, required=True, help="result file to write, e.g. out/MOT17-09.txt")
    parser.add_argument("--config", type=Path, help="TOML file with a [tracker] table")
    parser.add_argument("--precision", choices=SUPPORTED_PRECISIONS, default="fp16")
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
        device = arguments.device or ("cuda" if torch.cuda.is_available() else "cpu")
        detect_frame = load_detect_frame(arguments.weights, arguments.precision, device)
        metadata = RunMetadata(params, WEIGHTS_ID, WEIGHTS_SHA256, arguments.precision)
        summary = track_sequence(arguments.frames_dir, arguments.output, detect_frame, metadata)
    except (SequenceError, WeightsIntegrityError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {summary.row_count} rows for {summary.frame_count} frames to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
