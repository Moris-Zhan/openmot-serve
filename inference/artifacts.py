import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tracking.detector import DetectorSettings
from tracking.files import write_text_atomic

MODEL_NAME = "yolox_x"
DEFAULT_ARTIFACT_DIR = Path("models/engines")
EXPORT_COMMAND = "python -m inference.export_onnx"
BUILD_COMMAND = "python -m inference.build_engine"
_WEIGHTS_PREFIX_LENGTH = 8
_HASH_CHUNK_BYTES = 1 << 20


class ArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class Observed:
    weights_sha256: str
    gpu_name: str | None
    tensorrt_version: str | None


@dataclass(frozen=True)
class ArtifactInfo:
    path: Path
    sidecar: Mapping[str, object]


def gpu_slug(gpu_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", gpu_name.lower()).strip("-")


def _stem(weights_sha256: str, input_height: int, input_width: int) -> str:
    if len(weights_sha256) < _WEIGHTS_PREFIX_LENGTH:
        raise ValueError(f"weights sha256 {weights_sha256!r} is too short to name an artifact")
    return f"{MODEL_NAME}-{weights_sha256[:_WEIGHTS_PREFIX_LENGTH]}-{input_height}x{input_width}"


def onnx_name(weights_sha256: str, input_height: int, input_width: int) -> str:
    return f"{_stem(weights_sha256, input_height, input_width)}.onnx"


def engine_name(
    weights_sha256: str,
    input_height: int,
    input_width: int,
    tensorrt_version: str,
    gpu_name: str,
    precision: str,
) -> str:
    stem = _stem(weights_sha256, input_height, input_width)
    return f"{stem}-trt{tensorrt_version}-{gpu_slug(gpu_name)}-{precision}.engine"


def sidecar_path(artifact: Path) -> Path:
    return artifact.with_name(artifact.name + ".json")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sidecar(path: Path, document: Mapping[str, object]) -> None:
    write_text_atomic(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def read_sidecar(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ArtifactError(f"{path} is not valid JSON: {error}") from error


def verify_sidecar(sidecar: Mapping[str, object], current: Mapping[str, object]) -> None:
    mismatches = [
        f"{item} mismatch: sidecar has {sidecar.get(item)}, current is {value}"
        for item, value in current.items()
        if sidecar.get(item) != value
    ]
    if mismatches:
        raise ArtifactError("; ".join(mismatches))


def _expected_path(backend: str, settings: DetectorSettings, observed: Observed, directory: Path) -> Path:
    if backend == "onnx":
        return directory / onnx_name(observed.weights_sha256, settings.input_height, settings.input_width)
    if observed.gpu_name is None or observed.tensorrt_version is None:
        raise ArtifactError("a TensorRT engine needs a visible GPU and an installed TensorRT to be located")
    name = engine_name(
        observed.weights_sha256,
        settings.input_height,
        settings.input_width,
        observed.tensorrt_version,
        observed.gpu_name,
        settings.precision,
    )
    return directory / name


def _current_values(
    backend: str, settings: DetectorSettings, observed: Observed, path: Path
) -> dict[str, object]:
    common = {
        "weights_sha256": observed.weights_sha256,
        "input_height": settings.input_height,
        "input_width": settings.input_width,
    }
    if backend == "onnx":
        return {**common, "onnx_sha256": file_sha256(path)}
    return {
        **common,
        "tensorrt_version": observed.tensorrt_version,
        "gpu_name": observed.gpu_name,
        "precision": settings.precision,
    }


def resolve_artifact(
    backend: str,
    settings: DetectorSettings,
    observed: Observed,
    directory: Path,
    override: Path | None,
) -> ArtifactInfo | None:
    if backend == "torch":
        return None
    command = EXPORT_COMMAND if backend == "onnx" else BUILD_COMMAND
    path = override if override is not None else _expected_path(backend, settings, observed, directory)
    for required in (path, sidecar_path(path)):
        if not required.is_file():
            raise ArtifactError(f"missing {required}; build it with: {command}")
    sidecar = read_sidecar(sidecar_path(path))
    try:
        verify_sidecar(sidecar, _current_values(backend, settings, observed, path))
    except ArtifactError as error:
        raise ArtifactError(f"{path}: {error}; rebuild it with: {command}") from error
    return ArtifactInfo(path, sidecar)
