from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import inference.backends as backends
from inference.artifacts import ArtifactError, ArtifactInfo, Observed, file_sha256
from inference.backends import (
    BackendError,
    describe_artifact,
    import_backend_package,
    load_staged_detector,
    resolve_backend_artifact,
)
from tracking.detector import DetectorSettings, finish_stage, prepare_stage


def _missing(name: str) -> object:
    raise ImportError(f"No module named {name!r}", name=name)


@pytest.mark.parametrize(
    ("module", "package"),
    [("tensorrt", "tensorrt-cu12"), ("onnxruntime", "onnxruntime-gpu"), ("onnx", "onnx")],
)
def test_a_missing_package_is_named_with_the_extra_to_install(module: str, package: str) -> None:
    with pytest.raises(BackendError) as raised:
        import_backend_package(module, _missing)
    message = str(raised.value)
    assert package in message and "openmot-serve[export]" in message


def test_a_missing_package_is_never_replaced_by_another_backend() -> None:
    with pytest.raises(BackendError, match="not installed"):
        import_backend_package("tensorrt", _missing)


def test_an_installed_package_is_returned_as_imported() -> None:
    sentinel = object()
    assert import_backend_package("onnx", lambda name: sentinel) is sentinel


def test_an_import_error_inside_the_package_is_not_reported_as_missing() -> None:
    def broken(name: str) -> object:
        raise ImportError("libcudnn.so.9: cannot open shared object file", name="libcudnn")

    with pytest.raises(BackendError) as raised:
        import_backend_package("onnxruntime", broken)
    assert "libcudnn" in str(raised.value) and "not installed" not in str(raised.value)


CANDIDATES, CHANNELS = 23625, 85


def _raw_with_two_people() -> torch.Tensor:
    raw = torch.zeros(1, CANDIDATES, CHANNELS)
    for row, center_x in enumerate((100.0, 400.0)):
        raw[0, row, :5] = torch.tensor([center_x, 100.0, 40.0, 100.0, 0.9])
        raw[0, row, 5] = 0.9
    return raw


class RunSession:
    def __init__(self, providers: list[str]) -> None:
        self.providers = providers
        self.runs = 0

    def get_providers(self) -> list[str]:
        return self.providers

    def io_binding(self) -> SimpleNamespace:
        return SimpleNamespace(
            bind_input=lambda **_: None, bind_output=lambda **_: None, synchronize_outputs=lambda: None
        )

    def run_with_iobinding(self, binding: object) -> None:
        self.runs += 1


def _onnx_importer(session: RunSession):
    runtime = SimpleNamespace(InferenceSession=lambda path, providers: session)
    return lambda name: runtime


def _artifact() -> ArtifactInfo:
    return ArtifactInfo(Path("model.onnx"), {})


def _forbid_the_pytorch_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("an exported backend must not load the PyTorch model")

    monkeypatch.setattr(backends, "load_detector", forbidden)


def test_the_onnx_backend_loads_without_the_pytorch_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_the_pytorch_model(monkeypatch)
    session = RunSession(["CUDAExecutionProvider"])
    settings = DetectorSettings(backend="onnx", precision="fp32")
    staged = load_staged_detector(
        "onnx", Path("w.pth"), settings, "cpu", _artifact(), importer=_onnx_importer(session)
    )
    assert callable(staged.infer) and callable(staged.prepare)


def test_the_onnx_backend_reuses_the_shared_prepare_and_finish_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_the_pytorch_model(monkeypatch)
    settings = DetectorSettings(backend="onnx", precision="fp32")
    staged = load_staged_detector(
        "onnx", Path("w.pth"), settings, "cpu", _artifact(),
        importer=_onnx_importer(RunSession(["CUDAExecutionProvider"])),
    )
    image = np.random.default_rng(0).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    prepared = staged.prepare(image)
    expected = prepare_stage(settings, "cpu")(image)
    assert torch.equal(prepared.batch, expected.batch) and prepared.scale == expected.scale
    raw = _raw_with_two_people()[0]
    assert np.array_equal(staged.finish(raw, prepared), finish_stage(settings)(raw, prepared))


def test_the_onnx_infer_stage_runs_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_the_pytorch_model(monkeypatch)
    session = RunSession(["CUDAExecutionProvider"])
    settings = DetectorSettings(backend="onnx", precision="fp32")
    staged = load_staged_detector("onnx", Path("w.pth"), settings, "cpu", _artifact(), importer=_onnx_importer(session))
    raw = staged.infer(staged.prepare(np.zeros((360, 640, 3), dtype=np.uint8)))
    assert session.runs == 1 and raw.shape == (CANDIDATES, CHANNELS)


def test_the_onnx_backend_fails_when_the_session_fell_back_to_the_cpu() -> None:
    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(BackendError, match="CUDAExecutionProvider"):
        load_staged_detector(
            "onnx", Path("w.pth"), settings, "cpu", _artifact(),
            importer=_onnx_importer(RunSession(["CPUExecutionProvider"])),
        )


def test_the_onnx_backend_without_onnxruntime_names_the_package() -> None:
    def missing(name: str) -> object:
        raise ImportError("no module", name=name)

    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(BackendError, match="onnxruntime-gpu"):
        load_staged_detector("onnx", Path("w.pth"), settings, "cpu", _artifact(), importer=missing)


def test_the_torch_backend_loads_the_model_and_reuses_the_torch_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[tuple[Path, DetectorSettings, str]] = []

    class Model:
        def __call__(self, batch: torch.Tensor) -> torch.Tensor:
            return _raw_with_two_people()

    def fake_load(weights: Path, settings: DetectorSettings, device: str) -> object:
        loaded.append((weights, settings, device))
        return backends.Detector(Model(), settings, device)

    monkeypatch.setattr(backends, "load_detector", fake_load)
    settings = DetectorSettings(precision="fp32")
    staged = load_staged_detector("torch", Path("w.pth"), settings, "cpu", None)
    assert loaded == [(Path("w.pth"), settings, "cpu")]
    assert staged.infer(staged.prepare(np.zeros((360, 640, 3), dtype=np.uint8))).shape == (CANDIDATES, CHANNELS)


def test_an_exported_backend_needs_a_verified_artifact() -> None:
    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(BackendError, match="artifact"):
        load_staged_detector("onnx", Path("w.pth"), settings, "cpu", None)


def test_an_unknown_backend_is_rejected() -> None:
    with pytest.raises(BackendError, match="unknown backend"):
        load_staged_detector("openvino", Path("w.pth"), DetectorSettings(), "cpu", None)


WEIGHTS_SHA256 = "ab12cd34" + "0" * 56
ONNX_SHA256 = "c" * 64
GPU = "NVIDIA GeForce RTX 5090"
TENSORRT = "10.13.3.9"


def _observed(backend: str, weights: Path) -> Observed:
    return Observed(WEIGHTS_SHA256, GPU if backend == "tensorrt" else None, TENSORRT if backend == "tensorrt" else None)


def _place_artifacts(directory: Path) -> None:
    from inference.artifacts import engine_name, onnx_name, sidecar_path, write_sidecar

    onnx = directory / onnx_name(WEIGHTS_SHA256, 800, 1440)
    onnx.write_bytes(b"onnx-bytes")
    onnx_sidecar = {
        "weights_sha256": WEIGHTS_SHA256,
        "onnx_sha256": file_sha256(onnx),
        "input_height": 800,
        "input_width": 1440,
    }
    write_sidecar(sidecar_path(onnx), onnx_sidecar)
    engine = directory / engine_name(WEIGHTS_SHA256, 800, 1440, TENSORRT, GPU, "fp16")
    engine.write_bytes(b"engine-bytes")
    engine_sidecar = {
        **onnx_sidecar,
        "tensorrt_version": TENSORRT,
        "gpu_name": GPU,
        "precision": "fp16",
        "workspace_bytes": 8 * 2**30,
        "optimization_level": "default",
        "builder_flags": ["FP16"],
    }
    write_sidecar(sidecar_path(engine), engine_sidecar)


def test_the_torch_backend_resolves_no_artifact_and_never_hashes_the_weights(tmp_path: Path) -> None:
    def forbidden(backend: str, weights: Path) -> Observed:
        raise AssertionError("the torch backend must not observe the environment")

    assert resolve_backend_artifact("torch", DetectorSettings(), Path("w.pth"), None, tmp_path, forbidden) is None


def test_an_onnx_artifact_is_resolved_against_the_observed_weights_hash(tmp_path: Path) -> None:
    _place_artifacts(tmp_path)
    settings = DetectorSettings(backend="onnx", precision="fp32")
    info = resolve_backend_artifact("onnx", settings, Path("w.pth"), None, tmp_path, _observed)
    assert info is not None and info.path.suffix == ".onnx"


def test_a_changed_weights_file_is_caught_even_though_no_pytorch_model_is_loaded(tmp_path: Path) -> None:
    _place_artifacts(tmp_path)
    settings = DetectorSettings(backend="onnx", precision="fp32")

    def changed(backend: str, weights: Path) -> Observed:
        return Observed("ab12cd34" + "1" * 56, None, None)

    with pytest.raises(ArtifactError):
        resolve_backend_artifact("onnx", settings, Path("w.pth"), None, tmp_path, changed)


def test_an_engine_is_resolved_with_the_observed_gpu_and_tensorrt_version(tmp_path: Path) -> None:
    _place_artifacts(tmp_path)
    info = resolve_backend_artifact(
        "tensorrt", DetectorSettings(backend="tensorrt"), Path("w.pth"), None, tmp_path, _observed
    )
    assert info is not None and info.path.suffix == ".engine"


def test_the_explicit_engine_path_is_used_instead_of_the_convention(tmp_path: Path) -> None:
    _place_artifacts(tmp_path)
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    for source in tmp_path.glob("*.onnx*"):
        (custom / "my.onnx" if source.suffix == ".onnx" else custom / "my.onnx.json").write_bytes(source.read_bytes())
    settings = DetectorSettings(backend="onnx", precision="fp32")
    info = resolve_backend_artifact("onnx", settings, Path("w.pth"), custom / "my.onnx", tmp_path, _observed)
    assert info is not None and info.path == custom / "my.onnx"


def test_the_onnx_record_names_the_file_hash_and_runtime_version(tmp_path: Path) -> None:
    _place_artifacts(tmp_path)
    settings = DetectorSettings(backend="onnx", precision="fp32")
    info = resolve_backend_artifact("onnx", settings, Path("w.pth"), None, tmp_path, _observed)
    record = describe_artifact("onnx", info, lambda package: {"onnxruntime-gpu": "1.22.0"}[package])
    assert record == {
        "file": info.path.name,
        "onnx_sha256": info.sidecar["onnx_sha256"],
        "onnxruntime": "1.22.0",
    }


def test_the_tensorrt_record_adds_the_build_parameters_and_the_gpu(tmp_path: Path) -> None:
    _place_artifacts(tmp_path)
    info = resolve_backend_artifact(
        "tensorrt", DetectorSettings(backend="tensorrt"), Path("w.pth"), None, tmp_path, _observed
    )
    record = describe_artifact("tensorrt", info, lambda package: None)
    assert record == {
        "file": info.path.name,
        "onnx_sha256": info.sidecar["onnx_sha256"],
        "tensorrt": TENSORRT,
        "gpu_name": GPU,
        "build": {
            "precision": "fp16",
            "workspace_bytes": 8 * 2**30,
            "optimization_level": "default",
            "builder_flags": ["FP16"],
        },
    }


def test_the_torch_backend_has_no_artifact_record() -> None:
    assert describe_artifact("torch", None, lambda package: None) is None
