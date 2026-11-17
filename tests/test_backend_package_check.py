from pathlib import Path

import pytest

from inference.artifacts import Observed
from inference.backends import BackendError, resolve_backend_artifact
from tracking.detector import DetectorSettings


def observe_without_tensorrt(backend: str, weights: Path) -> Observed:
    return Observed(weights_sha256="a" * 64, gpu_name="Fake GPU", tensorrt_version=None)


def test_a_missing_tensorrt_package_is_named_before_the_artifact_is_compared(tmp_path: Path) -> None:
    engine = tmp_path / "x.engine"
    engine.write_bytes(b"engine")
    engine.with_name("x.engine.json").write_text('{"tensorrt_version": "10.13.3.9"}')
    with pytest.raises(BackendError) as error:
        resolve_backend_artifact(
            "tensorrt",
            DetectorSettings(precision="fp16", backend="tensorrt"),
            tmp_path / "w.pth",
            engine,
            observe=observe_without_tensorrt,
        )
    message = str(error.value)
    assert "tensorrt-cu12" in message and "not installed" in message
    assert "openmot-serve[export]" in message
    assert "mismatch" not in message
