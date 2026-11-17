from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from inference.artifacts import BUILD_COMMAND, ArtifactInfo, verify_sidecar
from inference.backends import BackendError
from tracking.detector import PreparedFrame

INPUT_NAME = "images"
OUTPUT_NAME = "output"


@dataclass(frozen=True)
class Streams:
    create: Callable[[str], Any]
    current: Callable[[str], Any]


TORCH_STREAMS = Streams(
    create=lambda device: torch.cuda.Stream(device=device),
    current=lambda device: torch.cuda.current_stream(device),
)


def load_engine(runtime_module: Any, path: Path) -> tuple[Any, Any]:
    runtime = runtime_module.Runtime(runtime_module.Logger(runtime_module.Logger.WARNING))
    engine = runtime.deserialize_cuda_engine(path.read_bytes())
    if engine is None:
        raise BackendError(f"TensorRT could not deserialize {path}; rebuild it with: {BUILD_COMMAND}")
    context = engine.create_execution_context()
    if context is None:
        raise BackendError(f"TensorRT could not create an execution context for {path}")
    return engine, context


def make_infer(
    context: Any, engine: Any, device: str, streams: Streams = TORCH_STREAMS
) -> Callable[[PreparedFrame], torch.Tensor]:
    output = torch.empty(tuple(engine.get_tensor_shape(OUTPUT_NAME)), dtype=torch.float32, device=device)
    stream = streams.create(device)

    def infer(prepared: PreparedFrame) -> torch.Tensor:
        batch = prepared.batch.contiguous()
        current = streams.current(device)
        stream.wait_stream(current)
        context.set_tensor_address(INPUT_NAME, batch.data_ptr())
        context.set_tensor_address(OUTPUT_NAME, output.data_ptr())
        if not context.execute_async_v3(stream.cuda_stream):
            raise BackendError("TensorRT failed to execute the engine")
        current.wait_stream(stream)
        return output[0]

    return infer


def load_infer(
    runtime_module: Any,
    artifact: ArtifactInfo,
    device: str,
    gpu_name: str,
    streams: Streams = TORCH_STREAMS,
) -> Callable[[PreparedFrame], torch.Tensor]:
    verify_sidecar(
        artifact.sidecar,
        {"tensorrt_version": runtime_module.__version__, "gpu_name": gpu_name},
    )
    engine, context = load_engine(runtime_module, artifact.path)
    return make_infer(context, engine, device, streams)
