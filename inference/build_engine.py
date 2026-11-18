import argparse
import functools
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from inference.artifacts import (
    DEFAULT_ARTIFACT_DIR,
    ArtifactError,
    Observed,
    engine_name,
    resolve_artifact,
    sidecar_path,
    write_sidecar,
)
from inference.backends import BackendError, import_backend_package, installed_tensorrt_version
from inference.calibration import (
    CALIBRATION_SOURCE,
    DEFAULT_CALIBRATION_FRAMES,
    DEFAULT_DATA_ROOT,
    CalibrationError,
    CalibrationSelection,
    select_calibration_frames,
)
from inference.export_onnx import DEFAULT_WEIGHTS, REPOSITORY, verify_weights
from inference.int8_calibrator import CALIBRATOR_NAME, calibration_cache_sha256, create_entropy_calibrator
from tracking.detector import DetectorSettings, prepare_stage
from tracking.yolox import WeightsIntegrityError

TIMING_CACHE_SUFFIX = ".timing"
CALIBRATION_CACHE_SUFFIX = ".calib"
FP16_FLAG_NAME = "FP16"
INT8_FLAG_NAME = "INT8"
INT8_PRECISION = "int8"
BUILD_PRECISIONS = ("fp16", INT8_PRECISION)


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildSettings:
    precision: str = "fp16"
    workspace_bytes: int = 8 * 2**30
    optimization_level: str = "default"
    calibration_frames: int = DEFAULT_CALIBRATION_FRAMES
    data_root: Path = REPOSITORY / DEFAULT_DATA_ROOT


@dataclass(frozen=True)
class BuildDependencies:
    verify_weights: Callable[[Path], str]
    tensorrt_version: str
    gpu_name: str
    build: Callable[..., bytes | None]
    select_frames: Callable[[Path, int], CalibrationSelection] = select_calibration_frames
    create_calibrator: Callable[[CalibrationSelection, Path], Any] | None = None


def build_with_tensorrt(
    runtime: Any,
    onnx_path: Path,
    settings: BuildSettings,
    timing_cache_path: Path,
    calibrator: Any = None,
) -> bytes | None:
    if settings.precision == INT8_PRECISION and calibrator is None:
        raise BuildError("an INT8 build needs a calibrator")
    logger = runtime.Logger(runtime.Logger.WARNING)
    builder = runtime.Builder(logger)
    network = builder.create_network(0)
    parser = runtime.OnnxParser(network, logger)
    if not parser.parse(onnx_path.read_bytes()):
        errors = "; ".join(str(parser.get_error(index)) for index in range(parser.num_errors))
        raise BuildError(f"TensorRT could not parse {onnx_path}: {errors}")
    config = builder.create_builder_config()
    if settings.precision == INT8_PRECISION:
        config.set_flag(runtime.BuilderFlag.INT8)
        config.set_flag(runtime.BuilderFlag.FP16)
        config.int8_calibrator = calibrator
    else:
        config.set_flag(runtime.BuilderFlag.FP16)
    config.set_memory_pool_limit(runtime.MemoryPoolType.WORKSPACE, settings.workspace_bytes)
    previous_cache = timing_cache_path.read_bytes() if timing_cache_path.is_file() else b""
    timing_cache = config.create_timing_cache(previous_cache)
    config.set_timing_cache(timing_cache, False)
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        return None
    timing_cache_path.write_bytes(bytes(timing_cache.serialize()))
    return bytes(serialized)


def default_dependencies() -> BuildDependencies:
    runtime = import_backend_package("tensorrt")
    version = installed_tensorrt_version()
    if version is None:
        raise BackendError("the tensorrt-cu12 package is not installed")
    prepare = prepare_stage(DetectorSettings(backend="tensorrt"), "cuda")

    def create_calibrator(selection: CalibrationSelection, cache_path: Path) -> Any:
        return create_entropy_calibrator(runtime, selection, prepare, cache_path)

    return BuildDependencies(
        verify_weights=verify_weights,
        tensorrt_version=version,
        gpu_name=torch.cuda.get_device_name(0),
        build=functools.partial(build_with_tensorrt, runtime),
        create_calibrator=create_calibrator,
    )


def build_engine(
    weights: Path,
    directory: Path,
    settings: DetectorSettings,
    build_settings: BuildSettings,
    dependencies: BuildDependencies,
) -> Path:
    weights_sha256 = dependencies.verify_weights(weights)
    observed = Observed(weights_sha256, dependencies.gpu_name, dependencies.tensorrt_version)
    onnx = resolve_artifact("onnx", settings, observed, directory, None)
    if onnx is None:
        raise ArtifactError("the ONNX artifact could not be resolved")
    target = directory / engine_name(
        weights_sha256,
        settings.input_height,
        settings.input_width,
        dependencies.tensorrt_version,
        dependencies.gpu_name,
        build_settings.precision,
    )
    timing_cache = target.with_name(target.name + TIMING_CACHE_SUFFIX)
    calibration_cache = target.with_name(target.name + CALIBRATION_CACHE_SUFFIX)
    temporary = target.with_name(target.name + ".tmp")
    is_int8 = build_settings.precision == INT8_PRECISION
    selection: CalibrationSelection | None = None
    if is_int8:
        if dependencies.create_calibrator is None:
            raise BuildError("no calibrator is available for an INT8 build")
        selection = dependencies.select_frames(build_settings.data_root, build_settings.calibration_frames)
    calibration: dict[str, object] | None = None
    try:
        if selection is not None and dependencies.create_calibrator is not None:
            calibration_cache.unlink(missing_ok=True)
            calibrator = dependencies.create_calibrator(selection, calibration_cache)
            serialized = dependencies.build(onnx.path, build_settings, timing_cache, calibrator)
            if calibrator.error is not None:
                raise calibrator.error
        else:
            serialized = dependencies.build(onnx.path, build_settings, timing_cache)
        if serialized is None:
            raise BuildError(f"the TensorRT build of {onnx.path} failed; no engine was written")
        if selection is not None:
            calibration = {
                "calibrator": CALIBRATOR_NAME,
                "frames": len(selection.frames),
                "selection_sha256": selection.sha256,
                "cache": calibration_cache.name,
                "cache_sha256": calibration_cache_sha256(calibration_cache),
                "source": CALIBRATION_SOURCE,
            }
        temporary.write_bytes(serialized)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        if is_int8:
            calibration_cache.unlink(missing_ok=True)
        raise
    document: dict[str, object] = {
        "weights_sha256": weights_sha256,
        "onnx_sha256": onnx.sidecar["onnx_sha256"],
        "input_height": settings.input_height,
        "input_width": settings.input_width,
        "precision": build_settings.precision,
        "workspace_bytes": build_settings.workspace_bytes,
        "optimization_level": build_settings.optimization_level,
        "builder_flags": [INT8_FLAG_NAME, FP16_FLAG_NAME] if is_int8 else [FP16_FLAG_NAME],
        "tensorrt_version": dependencies.tensorrt_version,
        "gpu_name": dependencies.gpu_name,
        "timing_cache": timing_cache.name,
    }
    if calibration is not None:
        document["calibration"] = calibration
    write_sidecar(sidecar_path(target), document)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m inference.build_engine",
        description="Build a TensorRT FP16 or INT8 engine from the exported ONNX file.",
    )
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY / DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--precision", choices=BUILD_PRECISIONS, default="fp16")
    parser.add_argument("--calibration-frames", type=int, default=DEFAULT_CALIBRATION_FRAMES)
    parser.add_argument("--data-root", type=Path, default=REPOSITORY / DEFAULT_DATA_ROOT)
    return parser


def main(
    argv: Sequence[str] | None = None,
    dependencies: BuildDependencies | None = None,
    load_dependencies: Callable[[], BuildDependencies] = default_dependencies,
) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        written = build_engine(
            arguments.weights,
            arguments.output_dir,
            DetectorSettings(backend="tensorrt", precision=arguments.precision),
            BuildSettings(
                precision=arguments.precision,
                calibration_frames=arguments.calibration_frames,
                data_root=arguments.data_root,
            ),
            dependencies or load_dependencies(),
        )
    except (
        BuildError,
        CalibrationError,
        ArtifactError,
        BackendError,
        WeightsIntegrityError,
        FileNotFoundError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
