import hashlib
import json
from pathlib import Path

import pytest

from inference.artifacts import (
    ArtifactError,
    Observed,
    engine_name,
    file_sha256,
    gpu_slug,
    onnx_name,
    read_sidecar,
    resolve_artifact,
    sidecar_path,
    verify_sidecar,
    write_sidecar,
)
from tracking.detector import DetectorSettings

WEIGHTS_SHA256 = "ab12cd34" + "0" * 56
GPU = "NVIDIA GeForce RTX 5090"
TENSORRT = "10.13.3.9"
ONNX_BYTES = b"onnx-bytes"
ONNX_SHA256 = hashlib.sha256(ONNX_BYTES).hexdigest()


def _observed(**changes: object) -> Observed:
    base = {"weights_sha256": WEIGHTS_SHA256, "gpu_name": GPU, "tensorrt_version": TENSORRT}
    return Observed(**{**base, **changes})


def _onnx_sidecar(**changes: object) -> dict[str, object]:
    base = {
        "weights_sha256": WEIGHTS_SHA256,
        "onnx_sha256": ONNX_SHA256,
        "input_height": 800,
        "input_width": 1440,
    }
    return {**base, **changes}


def _engine_sidecar(**changes: object) -> dict[str, object]:
    base = {
        "weights_sha256": WEIGHTS_SHA256,
        "onnx_sha256": ONNX_SHA256,
        "input_height": 800,
        "input_width": 1440,
        "tensorrt_version": TENSORRT,
        "gpu_name": GPU,
        "precision": "fp16",
    }
    return {**base, **changes}


def _place_onnx(directory: Path, content: bytes = ONNX_BYTES, sidecar: dict[str, object] | None = None) -> Path:
    path = directory / onnx_name(WEIGHTS_SHA256, 800, 1440)
    path.write_bytes(content)
    write_sidecar(sidecar_path(path), sidecar if sidecar is not None else _onnx_sidecar())
    return path


def _place_engine(directory: Path, sidecar: dict[str, object] | None = None) -> Path:
    path = directory / engine_name(WEIGHTS_SHA256, 800, 1440, TENSORRT, GPU, "fp16")
    path.write_bytes(b"engine-bytes")
    write_sidecar(sidecar_path(path), sidecar if sidecar is not None else _engine_sidecar())
    return path


def test_gpu_slug_lowercases_and_replaces_non_alphanumerics() -> None:
    assert gpu_slug("NVIDIA GeForce RTX 5090") == "nvidia-geforce-rtx-5090"
    assert gpu_slug("  Tesla T4 (16GB)  ") == "tesla-t4-16gb"


def test_onnx_name_is_made_of_the_model_weights_prefix_and_input_size() -> None:
    assert onnx_name(WEIGHTS_SHA256, 800, 1440) == "yolox_x-ab12cd34-800x1440.onnx"


def test_engine_name_adds_the_tensorrt_version_gpu_and_precision() -> None:
    name = engine_name(WEIGHTS_SHA256, 800, 1440, "10.13.3.9", "NVIDIA GeForce RTX 5090", "fp16")
    assert name == "yolox_x-ab12cd34-800x1440-trt10.13.3.9-nvidia-geforce-rtx-5090-fp16.engine"


def test_different_weights_get_different_names() -> None:
    other = "ffffffff" + "0" * 56
    assert onnx_name(WEIGHTS_SHA256, 800, 1440) != onnx_name(other, 800, 1440)
    first = engine_name(WEIGHTS_SHA256, 800, 1440, "10.13.3.9", "GPU A", "fp16")
    assert first != engine_name(other, 800, 1440, "10.13.3.9", "GPU A", "fp16")


def test_a_different_gpu_or_tensorrt_version_gives_a_different_engine_name() -> None:
    base = engine_name(WEIGHTS_SHA256, 800, 1440, "10.13.3.9", "GPU A", "fp16")
    assert base != engine_name(WEIGHTS_SHA256, 800, 1440, "10.13.3.9", "GPU B", "fp16")
    assert base != engine_name(WEIGHTS_SHA256, 800, 1440, "10.14.0.1", "GPU A", "fp16")


def test_a_different_input_size_gives_a_different_name() -> None:
    assert onnx_name(WEIGHTS_SHA256, 800, 1440) != onnx_name(WEIGHTS_SHA256, 640, 1152)


def test_the_sidecar_sits_beside_the_artifact_with_a_json_suffix_added() -> None:
    assert sidecar_path(Path("models/engines/a.onnx")) == Path("models/engines/a.onnx.json")
    assert sidecar_path(Path("models/engines/a.engine")) == Path("models/engines/a.engine.json")


@pytest.mark.parametrize("sha256", ["abc", ""])
def test_a_too_short_weights_hash_is_rejected(sha256: str) -> None:
    with pytest.raises(ValueError, match="sha256"):
        onnx_name(sha256, 800, 1440)


def test_a_sidecar_is_written_with_sorted_keys_and_reads_back_equal(tmp_path: Path) -> None:
    path = tmp_path / "a.onnx.json"
    document = {"b": 2, "a": {"z": 1, "y": [3, 4]}}
    write_sidecar(path, document)
    text = path.read_text()
    assert text.endswith("\n") and text.index('"a"') < text.index('"b"')
    assert read_sidecar(path) == document


def test_file_sha256_hashes_the_file_content(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(ONNX_BYTES)
    assert file_sha256(path) == ONNX_SHA256


def test_a_matching_sidecar_passes_verification() -> None:
    verify_sidecar(_engine_sidecar(), _engine_sidecar())


@pytest.mark.parametrize(
    ("item", "current"),
    [
        ("weights_sha256", "f" * 64),
        ("onnx_sha256", "e" * 64),
        ("input_height", 640),
        ("input_width", 1152),
        ("tensorrt_version", "10.14.0.1"),
        ("gpu_name", "Tesla T4"),
    ],
)
def test_each_mismatched_item_is_named_with_both_values(item: str, current: object) -> None:
    with pytest.raises(ArtifactError, match=rf"{item} mismatch: sidecar has .*, current is .*{current}"):
        verify_sidecar(_engine_sidecar(), _engine_sidecar(**{item: current}))


def test_every_mismatched_item_is_reported_not_just_the_first() -> None:
    with pytest.raises(ArtifactError) as raised:
        verify_sidecar(_engine_sidecar(), _engine_sidecar(gpu_name="Tesla T4", tensorrt_version="9.0"))
    assert "gpu_name mismatch" in str(raised.value) and "tensorrt_version mismatch" in str(raised.value)


def test_a_sidecar_lacking_a_checked_item_is_a_mismatch() -> None:
    sidecar = _engine_sidecar()
    del sidecar["gpu_name"]
    with pytest.raises(ArtifactError, match="gpu_name mismatch"):
        verify_sidecar(sidecar, _engine_sidecar())


def test_the_torch_backend_needs_no_artifact(tmp_path: Path) -> None:
    assert resolve_artifact("torch", DetectorSettings(), _observed(), tmp_path, None) is None


def test_a_matching_onnx_artifact_resolves_by_naming_convention(tmp_path: Path) -> None:
    path = _place_onnx(tmp_path)
    info = resolve_artifact("onnx", DetectorSettings(backend="onnx", precision="fp32"), _observed(), tmp_path, None)
    assert info is not None and info.path == path and info.sidecar == _onnx_sidecar()


def test_a_matching_engine_resolves_by_naming_convention(tmp_path: Path) -> None:
    path = _place_engine(tmp_path)
    info = resolve_artifact("tensorrt", DetectorSettings(backend="tensorrt"), _observed(), tmp_path, None)
    assert info is not None and info.path == path and info.sidecar == _engine_sidecar()


def test_a_missing_onnx_names_the_expected_file_and_the_export_command(tmp_path: Path) -> None:
    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(ArtifactError) as raised:
        resolve_artifact("onnx", settings, _observed(), tmp_path, None)
    message = str(raised.value)
    assert onnx_name(WEIGHTS_SHA256, 800, 1440) in message and "python -m inference.export_onnx" in message


def test_a_missing_engine_names_the_expected_file_and_the_build_command(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError) as raised:
        resolve_artifact("tensorrt", DetectorSettings(backend="tensorrt"), _observed(), tmp_path, None)
    message = str(raised.value)
    assert engine_name(WEIGHTS_SHA256, 800, 1440, TENSORRT, GPU, "fp16") in message
    assert "python -m inference.build_engine" in message


def test_a_missing_sidecar_is_reported_with_its_name(tmp_path: Path) -> None:
    path = _place_onnx(tmp_path)
    sidecar_path(path).unlink()
    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(ArtifactError, match=r"\.onnx\.json"):
        resolve_artifact("onnx", settings, _observed(), tmp_path, None)


def test_an_explicit_path_overrides_the_default_and_is_verified_too(tmp_path: Path) -> None:
    elsewhere = tmp_path / "custom" / "my.onnx"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(ONNX_BYTES)
    write_sidecar(sidecar_path(elsewhere), _onnx_sidecar())
    settings = DetectorSettings(backend="onnx", precision="fp32")
    info = resolve_artifact("onnx", settings, _observed(), tmp_path, elsewhere)
    assert info is not None and info.path == elsewhere
    write_sidecar(sidecar_path(elsewhere), _onnx_sidecar(weights_sha256="f" * 64))
    with pytest.raises(ArtifactError, match="weights_sha256 mismatch"):
        resolve_artifact("onnx", settings, _observed(), tmp_path, elsewhere)


def test_the_onnx_hash_is_recomputed_so_a_tampered_file_fails(tmp_path: Path) -> None:
    _place_onnx(tmp_path, content=b"tampered", sidecar=_onnx_sidecar())
    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(ArtifactError, match="onnx_sha256 mismatch"):
        resolve_artifact("onnx", settings, _observed(), tmp_path, None)


def test_changed_weights_are_reported_as_a_weights_mismatch(tmp_path: Path) -> None:
    _place_onnx(tmp_path, sidecar=_onnx_sidecar(weights_sha256="ab12cd34" + "1" * 56))
    settings = DetectorSettings(backend="onnx", precision="fp32")
    with pytest.raises(ArtifactError, match="weights_sha256 mismatch"):
        resolve_artifact("onnx", settings, _observed(), tmp_path, None)


def test_an_engine_built_for_another_gpu_or_tensorrt_asks_for_a_rebuild(tmp_path: Path) -> None:
    _place_engine(tmp_path, sidecar=_engine_sidecar(gpu_name="Tesla T4"))
    with pytest.raises(ArtifactError, match=r"gpu_name mismatch.*rebuild"):
        resolve_artifact("tensorrt", DetectorSettings(backend="tensorrt"), _observed(), tmp_path, None)


def test_sidecar_json_is_valid_json_on_disk(tmp_path: Path) -> None:
    path = _place_onnx(tmp_path)
    assert json.loads(sidecar_path(path).read_text())["input_width"] == 1440
