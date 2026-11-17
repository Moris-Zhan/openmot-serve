from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import torch  # must load before onnxruntime so torch's CUDA libraries are found first

from inference.backends import BackendError
from tracking.detector import DetectorSettings, PreparedFrame
from tracking.yolox import CLASS_COUNT, grid_count

CUDA_PROVIDER = "CUDAExecutionProvider"
CUDA_PROVIDER_OPTIONS = {"use_tf32": "0"}
INPUT_NAME = "images"
OUTPUT_NAME = "output"
_CHANNELS = 5 + CLASS_COUNT


def create_session(runtime: Any, path: Path) -> Any:
    session = runtime.InferenceSession(str(path), providers=[(CUDA_PROVIDER, CUDA_PROVIDER_OPTIONS)])
    providers = session.get_providers()
    if not providers or providers[0] != CUDA_PROVIDER:
        raise BackendError(
            f"onnxruntime could not use {CUDA_PROVIDER} (active providers: {providers}); "
            "it fell back instead of running on the GPU. The installed onnxruntime-gpu probably targets a "
            "different CUDA version than this environment"
        )
    return session


def _device_id(tensor: torch.Tensor) -> int:
    return tensor.device.index or 0


def make_infer(
    session: Any, settings: DetectorSettings, device: str
) -> Callable[[PreparedFrame], torch.Tensor]:
    candidates = grid_count(settings.input_height, settings.input_width)
    output_shape = (1, candidates, _CHANNELS)
    output = torch.empty(output_shape, dtype=torch.float32, device=device)

    def infer(prepared: PreparedFrame) -> torch.Tensor:
        batch = prepared.batch.contiguous()
        if batch.is_cuda:
            torch.cuda.current_stream(batch.device).synchronize()
        binding = session.io_binding()
        binding.bind_input(
            name=INPUT_NAME,
            device_type=batch.device.type,
            device_id=_device_id(batch),
            element_type=np.float32,
            shape=tuple(batch.shape),
            buffer_ptr=batch.data_ptr(),
        )
        binding.bind_output(
            name=OUTPUT_NAME,
            device_type=output.device.type,
            device_id=_device_id(output),
            element_type=np.float32,
            shape=output_shape,
            buffer_ptr=output.data_ptr(),
        )
        session.run_with_iobinding(binding)
        binding.synchronize_outputs()
        return output[0]

    return infer
