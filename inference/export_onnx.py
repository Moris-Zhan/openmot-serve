import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import torch

from inference.artifacts import (
    DEFAULT_ARTIFACT_DIR,
    ArtifactError,
    file_sha256,
    onnx_name,
    sidecar_path,
    write_sidecar,
)
from inference.backends import BackendError, import_backend_package
from tracking.detector import DetectorSettings
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256, WeightsIntegrityError, load_yolox_x, sha256_of_file

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = REPOSITORY / "models" / "yolox_x.pth"
OPSET = 17
EXPORTER = "torchscript"
EXPORT_PRECISION = "fp32"


@dataclass(frozen=True)
class ExportDependencies:
    verify_weights: Callable[[Path], str]
    load_model: Callable[[Path], torch.nn.Module]
    export: Callable[[torch.nn.Module, Path, int, int], None]
    check: Callable[[Path], None]
    torch_version: str


def verify_weights(weights: Path) -> str:
    actual = sha256_of_file(weights)
    if actual != WEIGHTS_SHA256:
        raise WeightsIntegrityError(f"{weights} has SHA-256 {actual}, expected {WEIGHTS_SHA256} ({WEIGHTS_ID})")
    return actual


def _load_model(weights: Path) -> torch.nn.Module:
    return load_yolox_x(weights, "cpu")


def _export(model: torch.nn.Module, path: Path, height: int, width: int) -> None:
    torch.onnx.export(
        model,
        (torch.zeros(1, 3, height, width),),
        str(path),
        opset_version=OPSET,
        dynamo=False,
        input_names=["images"],
        output_names=["output"],
    )


def _check(path: Path) -> None:
    import_backend_package("onnx").checker.check_model(str(path))


def default_dependencies() -> ExportDependencies:
    return ExportDependencies(verify_weights, _load_model, _export, _check, torch.__version__)


def export_onnx(
    weights: Path, directory: Path, settings: DetectorSettings, dependencies: ExportDependencies
) -> Path:
    weights_sha256 = dependencies.verify_weights(weights)
    target = directory / onnx_name(weights_sha256, settings.input_height, settings.input_width)
    if target.exists():
        raise ArtifactError(f"{target} already exists; refusing to overwrite it")
    directory.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    sidecar = sidecar_path(target)
    try:
        model = dependencies.load_model(weights)
        dependencies.export(model, temporary, settings.input_height, settings.input_width)
        dependencies.check(temporary)
        write_sidecar(
            sidecar,
            {
                "weights_sha256": weights_sha256,
                "onnx_sha256": file_sha256(temporary),
                "input_height": settings.input_height,
                "input_width": settings.input_width,
                "opset": OPSET,
                "exporter": EXPORTER,
                "torch_version": dependencies.torch_version,
                "precision": EXPORT_PRECISION,
            },
        )
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m inference.export_onnx",
        description="Export the YOLOX-X network body to a fixed-shape FP32 ONNX file.",
    )
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY / DEFAULT_ARTIFACT_DIR)
    return parser


def main(argv: Sequence[str] | None = None, dependencies: ExportDependencies | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        written = export_onnx(
            arguments.weights,
            arguments.output_dir,
            DetectorSettings(precision=EXPORT_PRECISION),
            dependencies or default_dependencies(),
        )
    except (WeightsIntegrityError, ArtifactError, BackendError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
