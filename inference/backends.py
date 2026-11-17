import importlib
import importlib.metadata
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import torch

from inference.artifacts import DEFAULT_ARTIFACT_DIR, ArtifactInfo, Observed, resolve_artifact
from tracking.detector import (
    Detector,
    DetectorSettings,
    PreparedFrame,
    StagedDetector,
    finish_stage,
    load_detector,
    prepare_stage,
    synchronize_stage,
    torch_staged_detector,
)
from tracking.yolox import sha256_of_file

__all__ = ["BackendError", "Detector", "import_backend_package", "load_detector", "load_staged_detector"]

_PACKAGE_BY_MODULE = {
    "onnx": "onnx",
    "onnxruntime": "onnxruntime-gpu",
    "tensorrt": "tensorrt-cu12",
}
_INSTALL_HINT = 'pip install "openmot-serve[export]"'


class BackendError(RuntimeError):
    pass


def installed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def installed_tensorrt_version() -> str | None:
    return installed_version("tensorrt-cu12")


def import_backend_package(
    module: str, importer: Callable[[str], ModuleType] = importlib.import_module
) -> ModuleType:
    package = _PACKAGE_BY_MODULE[module]
    try:
        return importer(module)
    except ImportError as error:
        if error.name == module:
            raise BackendError(
                f"the {package} package is not installed; install it with: {_INSTALL_HINT}"
            ) from error
        raise BackendError(f"the {package} package could not be loaded: {error}") from error


def _with_shared_stages(
    settings: DetectorSettings, device: str, infer: Callable[[PreparedFrame], torch.Tensor]
) -> StagedDetector:
    return StagedDetector(
        prepare_stage(settings, device),
        infer,
        finish_stage(settings),
        synchronize_stage(device),
    )


def load_staged_detector(
    backend: str,
    weights: Path,
    settings: DetectorSettings,
    device: str,
    artifact: ArtifactInfo | None,
    importer: Callable[[str], ModuleType] = importlib.import_module,
    gpu_name: Callable[[], str] = lambda: torch.cuda.get_device_name(),
    streams: Any = None,
) -> StagedDetector:
    if backend == "torch":
        return torch_staged_detector(load_detector(weights, settings, device))
    if backend not in ("onnx", "tensorrt"):
        raise BackendError(f"unknown backend {backend!r}")
    if artifact is None:
        raise BackendError(f"the {backend} backend needs a verified artifact, but none was resolved")
    if backend == "onnx":
        from inference import onnx_backend

        runtime = import_backend_package("onnxruntime", importer)
        session = onnx_backend.create_session(runtime, artifact.path)
        return _with_shared_stages(settings, device, onnx_backend.make_infer(session, settings, device))
    from inference import tensorrt_backend

    runtime = import_backend_package("tensorrt", importer)
    infer = tensorrt_backend.load_infer(
        runtime, artifact, device, gpu_name(), streams or tensorrt_backend.TORCH_STREAMS
    )
    return _with_shared_stages(settings, device, infer)


DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / DEFAULT_ARTIFACT_DIR


def observe_environment(backend: str, weights: Path) -> Observed:
    needs_engine_facts = backend == "tensorrt"
    return Observed(
        weights_sha256=sha256_of_file(weights),
        gpu_name=torch.cuda.get_device_name() if needs_engine_facts and torch.cuda.is_available() else None,
        tensorrt_version=installed_tensorrt_version() if needs_engine_facts else None,
    )


def resolve_backend_artifact(
    backend: str,
    settings: DetectorSettings,
    weights: Path,
    engine: Path | None,
    directory: Path = DEFAULT_DIRECTORY,
    observe: Callable[[str, Path], Observed] = observe_environment,
) -> ArtifactInfo | None:
    if backend == "torch":
        return None
    observed = observe(backend, weights)
    if backend == "tensorrt" and observed.tensorrt_version is None:
        raise BackendError(
            f"the {_PACKAGE_BY_MODULE['tensorrt']} package is not installed; install it with: {_INSTALL_HINT}"
        )
    return resolve_artifact(backend, settings, observed, directory, engine)


def describe_artifact(
    backend: str,
    artifact: ArtifactInfo | None,
    version_of: Callable[[str], str | None] = installed_version,
) -> dict[str, object] | None:
    if artifact is None:
        return None
    record: dict[str, object] = {"file": artifact.path.name, "onnx_sha256": artifact.sidecar["onnx_sha256"]}
    if backend == "onnx":
        return {**record, "onnxruntime": version_of("onnxruntime-gpu")}
    return {
        **record,
        "tensorrt": artifact.sidecar["tensorrt_version"],
        "gpu_name": artifact.sidecar["gpu_name"],
        "build": {
            key: artifact.sidecar[key]
            for key in ("precision", "workspace_bytes", "optimization_level", "builder_flags")
        },
    }
