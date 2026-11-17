from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from inference.backends import BackendError
from inference.onnx_backend import CUDA_PROVIDER, create_session, make_infer
from tracking.detector import DetectorSettings, PreparedFrame

CANDIDATES, CHANNELS = 23625, 85


class FakeSession:
    def __init__(self, providers_after_load: list[str]) -> None:
        self.providers_after_load = providers_after_load

    def get_providers(self) -> list[str]:
        return self.providers_after_load


class FakeOnnxRuntime:
    def __init__(self, providers_after_load: list[str]) -> None:
        self.providers_after_load = providers_after_load
        self.requests: list[tuple[str, list[object]]] = []
        self.InferenceSession = self._create

    def _create(self, path: str, providers: list[object]) -> FakeSession:
        self.requests.append((path, providers))
        return FakeSession(self.providers_after_load)


def test_only_the_cuda_execution_provider_is_requested() -> None:
    runtime = FakeOnnxRuntime([CUDA_PROVIDER, "CPUExecutionProvider"])
    create_session(runtime, Path("model.onnx"))
    assert len(runtime.requests) == 1
    path, providers = runtime.requests[0]
    assert path == "model.onnx"
    assert [provider[0] for provider in providers] == [CUDA_PROVIDER]


def test_tf32_is_disabled_so_that_fp32_means_strict_fp32() -> None:
    runtime = FakeOnnxRuntime([CUDA_PROVIDER, "CPUExecutionProvider"])
    create_session(runtime, Path("model.onnx"))
    _, providers = runtime.requests[0]
    assert providers == [(CUDA_PROVIDER, {"use_tf32": "0"})]


def test_a_session_that_kept_the_cuda_provider_first_is_returned() -> None:
    session = create_session(FakeOnnxRuntime([CUDA_PROVIDER, "CPUExecutionProvider"]), Path("model.onnx"))
    assert session.get_providers()[0] == CUDA_PROVIDER


def test_a_silent_fallback_to_the_cpu_fails_the_load_with_the_reason() -> None:
    with pytest.raises(BackendError) as raised:
        create_session(FakeOnnxRuntime(["CPUExecutionProvider"]), Path("model.onnx"))
    message = str(raised.value)
    assert CUDA_PROVIDER in message and "CPUExecutionProvider" in message and "CUDA" in message


def test_a_provider_list_without_cuda_first_is_rejected_even_if_cuda_is_present() -> None:
    with pytest.raises(BackendError):
        create_session(FakeOnnxRuntime(["CPUExecutionProvider", CUDA_PROVIDER]), Path("model.onnx"))


def test_an_empty_provider_list_is_rejected() -> None:
    with pytest.raises(BackendError):
        create_session(FakeOnnxRuntime([]), Path("model.onnx"))


def test_the_error_does_not_return_a_session() -> None:
    runtime = SimpleNamespace(
        InferenceSession=lambda path, providers: FakeSession(["CPUExecutionProvider"]),
    )
    with pytest.raises(BackendError):
        create_session(runtime, Path("model.onnx"))


class FakeBinding:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.inputs: list[dict[str, object]] = []
        self.outputs: list[dict[str, object]] = []

    def bind_input(self, **arguments: object) -> None:
        self.inputs.append(arguments)

    def bind_output(self, **arguments: object) -> None:
        self.outputs.append(arguments)

    def synchronize_outputs(self) -> None:
        self.log.append("synchronize_outputs")


class FakeRunSession:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.bindings: list[FakeBinding] = []

    def io_binding(self) -> FakeBinding:
        binding = FakeBinding(self.log)
        self.bindings.append(binding)
        return binding

    def run_with_iobinding(self, binding: FakeBinding) -> None:
        self.log.append("run_with_iobinding")


def _batch() -> torch.Tensor:
    return torch.zeros(1, 3, 800, 1440, dtype=torch.float32)


def _infer(session: FakeRunSession):
    return make_infer(session, DetectorSettings(precision="fp32", backend="onnx"), "cpu")


def test_the_input_is_bound_to_the_batch_memory_without_copying() -> None:
    session = FakeRunSession()
    batch = _batch()
    _infer(session)(PreparedFrame(batch, 1.0))
    bound = session.bindings[0].inputs[0]
    assert bound["name"] == "images" and bound["buffer_ptr"] == batch.data_ptr()
    assert bound["shape"] == (1, 3, 800, 1440) and bound["element_type"] == np.float32
    assert bound["device_type"] == "cpu" and bound["device_id"] == 0


def test_the_output_is_bound_to_a_preallocated_tensor_of_the_expected_shape() -> None:
    session = FakeRunSession()
    infer = _infer(session)
    result = infer(PreparedFrame(_batch(), 1.0))
    bound = session.bindings[0].outputs[0]
    assert bound["name"] == "output" and bound["shape"] == (1, CANDIDATES, CHANNELS)
    assert bound["buffer_ptr"] == result.data_ptr()


def test_the_result_is_the_23625_by_85_float32_tensor_on_the_input_device() -> None:
    result = _infer(FakeRunSession())(PreparedFrame(_batch(), 1.0))
    assert result.shape == (CANDIDATES, CHANNELS) and result.dtype == torch.float32
    assert result.device == _batch().device


def test_the_run_finishes_and_synchronises_the_outputs_before_returning() -> None:
    session = FakeRunSession()
    _infer(session)(PreparedFrame(_batch(), 1.0))
    assert session.log == ["run_with_iobinding", "synchronize_outputs"]


def test_the_output_buffer_is_allocated_once_and_reused() -> None:
    session = FakeRunSession()
    infer = _infer(session)
    first = infer(PreparedFrame(_batch(), 1.0))
    second = infer(PreparedFrame(_batch(), 1.0))
    assert first.data_ptr() == second.data_ptr()


def test_inference_makes_no_host_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("the inference stage must not copy tensors to the host")

    infer = _infer(FakeRunSession())
    monkeypatch.setattr(torch.Tensor, "cpu", forbidden)
    monkeypatch.setattr(torch.Tensor, "numpy", forbidden)
    infer(PreparedFrame(_batch(), 1.0))


def test_a_non_contiguous_batch_is_made_contiguous_before_binding() -> None:
    session = FakeRunSession()
    image = torch.zeros(800, 1440, 3, dtype=torch.uint8)
    batch = image.permute(2, 0, 1)[None].float()
    assert not batch.is_contiguous()
    _infer(session)(PreparedFrame(batch, 1.0))
    assert session.bindings[0].inputs[0]["buffer_ptr"] != batch.data_ptr()
