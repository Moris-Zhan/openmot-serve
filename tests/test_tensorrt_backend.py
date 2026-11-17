from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import inference.backends as backends
from inference.artifacts import ArtifactError, ArtifactInfo
from inference.backends import BackendError, load_staged_detector
from inference.tensorrt_backend import INPUT_NAME, OUTPUT_NAME, Streams, load_engine, make_infer
from tracking.detector import DetectorSettings, PreparedFrame

CANDIDATES, CHANNELS = 23625, 85
GPU = "NVIDIA GeForce RTX 5090"
TENSORRT = "10.13.3.9"


class FakeStream:
    def __init__(self, name: str, log: list[str], cuda_stream: int) -> None:
        self.name = name
        self.log = log
        self.cuda_stream = cuda_stream

    def wait_stream(self, other: "FakeStream") -> None:
        self.log.append(f"{self.name}.wait({other.name})")


class FakeContext:
    def __init__(self, log: list[str], succeeds: bool = True) -> None:
        self.log = log
        self.succeeds = succeeds
        self.addresses: dict[str, int] = {}
        self.stream_handles: list[int] = []

    def set_tensor_address(self, name: str, address: int) -> None:
        self.addresses[name] = address

    def execute_async_v3(self, stream_handle: int) -> bool:
        self.log.append("execute")
        self.stream_handles.append(stream_handle)
        return self.succeeds


class FakeEngine:
    def __init__(self, context: FakeContext | None = None) -> None:
        self.context = context
        self.shapes = {OUTPUT_NAME: (1, CANDIDATES, CHANNELS)}

    def get_tensor_shape(self, name: str) -> tuple[int, ...]:
        return self.shapes[name]

    def create_execution_context(self) -> FakeContext | None:
        return self.context


def _streams(log: list[str]) -> tuple[Streams, FakeStream, FakeStream]:
    dedicated, current = FakeStream("trt", log, 777), FakeStream("current", log, 111)
    return Streams(create=lambda device: dedicated, current=lambda device: current), dedicated, current


def _settings() -> DetectorSettings:
    return DetectorSettings(backend="tensorrt", precision="fp16")


def _batch() -> torch.Tensor:
    return torch.zeros(1, 3, 800, 1440, dtype=torch.float32)


def _infer(log: list[str], succeeds: bool = True):
    context = FakeContext(log, succeeds)
    streams, dedicated, current = _streams(log)
    infer = make_infer(context, FakeEngine(context), "cpu", streams)
    return infer, context, dedicated, current


def test_input_and_output_addresses_are_the_torch_tensor_pointers() -> None:
    infer, context, *_ = _infer([])
    batch = _batch()
    result = infer(PreparedFrame(batch, 1.0))
    assert context.addresses[INPUT_NAME] == batch.data_ptr()
    assert context.addresses[OUTPUT_NAME] == result.data_ptr()


def test_the_engine_runs_on_its_own_stream_not_the_current_one() -> None:
    infer, context, dedicated, current = _infer([])
    infer(PreparedFrame(_batch(), 1.0))
    assert context.stream_handles == [dedicated.cuda_stream] and dedicated.cuda_stream != current.cuda_stream


def test_the_streams_wait_for_each_other_in_both_directions() -> None:
    log: list[str] = []
    infer, *_ = _infer(log)
    infer(PreparedFrame(_batch(), 1.0))
    assert log == ["trt.wait(current)", "execute", "current.wait(trt)"]


def test_the_result_is_the_23625_by_85_float32_tensor_on_the_input_device() -> None:
    infer, *_ = _infer([])
    result = infer(PreparedFrame(_batch(), 1.0))
    assert result.shape == (CANDIDATES, CHANNELS) and result.dtype == torch.float32
    assert result.device == _batch().device


def test_the_output_buffer_is_allocated_once_and_reused() -> None:
    infer, *_ = _infer([])
    assert infer(PreparedFrame(_batch(), 1.0)).data_ptr() == infer(PreparedFrame(_batch(), 1.0)).data_ptr()


def test_inference_makes_no_host_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("the inference stage must not copy tensors to the host")

    infer, *_ = _infer([])
    monkeypatch.setattr(torch.Tensor, "cpu", forbidden)
    monkeypatch.setattr(torch.Tensor, "numpy", forbidden)
    infer(PreparedFrame(_batch(), 1.0))


def test_a_non_contiguous_batch_is_made_contiguous_first() -> None:
    infer, context, *_ = _infer([])
    batch = torch.zeros(800, 1440, 3, dtype=torch.uint8).permute(2, 0, 1)[None].float()
    assert not batch.is_contiguous()
    infer(PreparedFrame(batch, 1.0))
    assert context.addresses[INPUT_NAME] != batch.data_ptr()


def test_a_failed_launch_is_an_error_not_a_silent_empty_result() -> None:
    infer, *_ = _infer([], succeeds=False)
    with pytest.raises(BackendError, match="execute"):
        infer(PreparedFrame(_batch(), 1.0))


class FakeRuntime:
    def __init__(self, engine: FakeEngine | None) -> None:
        self.engine = engine
        self.deserialized: list[bytes] = []

    def deserialize_cuda_engine(self, data: bytes) -> FakeEngine | None:
        self.deserialized.append(data)
        return self.engine


class FakeLogger:
    WARNING = "warning"

    def __init__(self, severity: object) -> None:
        self.severity = severity


def _tensorrt_module(engine: FakeEngine | None) -> SimpleNamespace:
    runtime = FakeRuntime(engine)
    return SimpleNamespace(__version__=TENSORRT, Logger=FakeLogger, Runtime=lambda logger: runtime, runtime=runtime)


def test_load_engine_deserializes_the_file_and_creates_an_execution_context(tmp_path: Path) -> None:
    path = tmp_path / "m.engine"
    path.write_bytes(b"engine-bytes")
    context = FakeContext([])
    module = _tensorrt_module(FakeEngine(context))
    engine, loaded_context = load_engine(module, path)
    assert module.runtime.deserialized == [b"engine-bytes"] and loaded_context is context
    assert engine.get_tensor_shape(OUTPUT_NAME) == (1, CANDIDATES, CHANNELS)


def test_load_engine_reports_a_deserialization_failure(tmp_path: Path) -> None:
    path = tmp_path / "m.engine"
    path.write_bytes(b"corrupt")
    with pytest.raises(BackendError, match="deserialize"):
        load_engine(_tensorrt_module(None), path)


def test_load_engine_reports_a_missing_execution_context(tmp_path: Path) -> None:
    path = tmp_path / "m.engine"
    path.write_bytes(b"x")
    with pytest.raises(BackendError, match="execution context"):
        load_engine(_tensorrt_module(FakeEngine(None)), path)


def _artifact(tmp_path: Path, **sidecar: object) -> ArtifactInfo:
    path = tmp_path / "m.engine"
    path.write_bytes(b"engine-bytes")
    return ArtifactInfo(path, {"tensorrt_version": TENSORRT, "gpu_name": GPU, **sidecar})


def _forbid_the_pytorch_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("an exported backend must not load the PyTorch model")

    monkeypatch.setattr(backends, "load_detector", forbidden)


def _load(tmp_path: Path, module: SimpleNamespace, gpu: str = GPU, **sidecar: object):
    return load_staged_detector(
        "tensorrt",
        Path("w.pth"),
        _settings(),
        "cpu",
        _artifact(tmp_path, **sidecar),
        importer=lambda name: module,
        gpu_name=lambda: gpu,
        streams=_streams([])[0],
    )


def test_the_tensorrt_backend_loads_without_the_pytorch_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_the_pytorch_model(monkeypatch)
    staged = _load(tmp_path, _tensorrt_module(FakeEngine(FakeContext([]))))
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    assert staged.prepare(image).batch.shape == (1, 3, 800, 1440)


def test_loading_refuses_an_engine_built_for_another_tensorrt_version(tmp_path: Path) -> None:
    module = _tensorrt_module(FakeEngine(FakeContext([])))
    with pytest.raises(ArtifactError, match="tensorrt_version mismatch"):
        _load(tmp_path, module, tensorrt_version="10.0.0.0")


def test_loading_refuses_an_engine_built_for_another_gpu(tmp_path: Path) -> None:
    module = _tensorrt_module(FakeEngine(FakeContext([])))
    with pytest.raises(ArtifactError, match="gpu_name mismatch"):
        _load(tmp_path, module, gpu="Tesla T4")


def test_loading_without_tensorrt_names_the_package(tmp_path: Path) -> None:
    def missing(name: str) -> object:
        raise ImportError("no module", name=name)

    with pytest.raises(BackendError, match="tensorrt-cu12"):
        load_staged_detector(
            "tensorrt",
            Path("w.pth"),
            _settings(),
            "cpu",
            _artifact(tmp_path),
            importer=missing,
            gpu_name=lambda: GPU,
        )
