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
from inference.export_onnx import DEFAULT_WEIGHTS, REPOSITORY, verify_weights
from tracking.detector import DetectorSettings
from tracking.yolox import WeightsIntegrityError

TIMING_CACHE_SUFFIX = ".timing"
FP16_FLAG_NAME = "FP16"


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildSettings:
    precision: str = "fp16"
    workspace_bytes: int = 8 * 2**30
    optimization_level: str = "default"


@dataclass(frozen=True)
class BuildDependencies:
    verify_weights: Callable[[Path], str]
    tensorrt_version: str
    gpu_name: str
    build: Callable[[Path, BuildSettings, Path], bytes | None]


def build_with_tensorrt(
    runtime: Any, onnx_path: Path, settings: BuildSettings, timing_cache_path: Path
) -> bytes | None:
    logger = runtime.Logger(runtime.Logger.WARNING)
    builder = runtime.Builder(logger)
    network = builder.create_network(0)
    parser = runtime.OnnxParser(network, logger)
    if not parser.parse(onnx_path.read_bytes()):
        errors = "; ".join(str(parser.get_error(index)) for index in range(parser.num_errors))
        raise BuildError(f"TensorRT could not parse {onnx_path}: {errors}")
    config = builder.create_builder_config()
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
    return BuildDependencies(
        verify_weights=verify_weights,
        tensorrt_version=version,
        gpu_name=torch.cuda.get_device_name(0),
        build=functools.partial(build_with_tensorrt, runtime),
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
    temporary = target.with_name(target.name + ".tmp")
    try:
        serialized = dependencies.build(onnx.path, build_settings, timing_cache)
        if serialized is None:
            raise BuildError(f"the TensorRT build of {onnx.path} failed; no engine was written")
        temporary.write_bytes(serialized)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    write_sidecar(
        sidecar_path(target),
        {
            "weights_sha256": weights_sha256,
            "onnx_sha256": onnx.sidecar["onnx_sha256"],
            "input_height": settings.input_height,
            "input_width": settings.input_width,
            "precision": build_settings.precision,
            "workspace_bytes": build_settings.workspace_bytes,
            "optimization_level": build_settings.optimization_level,
            "builder_flags": [FP16_FLAG_NAME],
            "tensorrt_version": dependencies.tensorrt_version,
            "gpu_name": dependencies.gpu_name,
            "timing_cache": timing_cache.name,
        },
    )
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m inference.build_engine",
        description="Build a TensorRT FP16 engine from the exported ONNX file.",
    )
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY / DEFAULT_ARTIFACT_DIR)
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
            DetectorSettings(backend="tensorrt"),
            BuildSettings(),
            dependencies or load_dependencies(),
        )
    except (BuildError, ArtifactError, BackendError, WeightsIntegrityError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
