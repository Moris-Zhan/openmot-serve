import pytest

from jobs.settings import SettingsError, load_settings
from tracking.bytetrack import TrackerParams
from tracking.config import SUPPORTED_PRECISIONS_BY_BACKEND, RunMetadata
from tracking.detector import DetectorSettings
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256

MINIMAL = {"OPENMOT_STORAGE_DIR": "/srv/openmot"}


def test_the_precision_table_gives_tensorrt_fp16_and_int8() -> None:
    assert SUPPORTED_PRECISIONS_BY_BACKEND["tensorrt"] == ("fp16", "int8")
    assert SUPPORTED_PRECISIONS_BY_BACKEND["torch"] == ("fp16", "fp32")
    assert SUPPORTED_PRECISIONS_BY_BACKEND["onnx"] == ("fp32",)


@pytest.mark.parametrize("precision", ["fp16", "int8"])
def test_detector_settings_accept_tensorrt_with_fp16_and_int8(precision: str) -> None:
    assert DetectorSettings(backend="tensorrt", precision=precision).precision == precision


@pytest.mark.parametrize(("backend", "supported"), [("torch", "fp16.*fp32"), ("onnx", "fp32")])
def test_detector_settings_reject_int8_outside_tensorrt(backend: str, supported: str) -> None:
    with pytest.raises(ValueError, match=rf"backend {backend!r}.*precision 'int8'.*{supported}"):
        DetectorSettings(backend=backend, precision="int8")


def test_tensorrt_with_fp32_lists_both_supported_precisions() -> None:
    with pytest.raises(ValueError, match=r"backend 'tensorrt'.*precision 'fp32'.*fp16.*int8"):
        DetectorSettings(backend="tensorrt", precision="fp32")


@pytest.mark.parametrize("precision", ["int8", "fp16"])
def test_run_metadata_accepts_tensorrt_precisions(precision: str) -> None:
    metadata = RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, precision, backend="tensorrt")
    assert metadata.precision == precision


def test_settings_accept_tensorrt_with_int8() -> None:
    settings = load_settings(
        {
            **MINIMAL,
            "OPENMOT_BACKEND": "tensorrt",
            "OPENMOT_PRECISION": "int8",
            "OPENMOT_ENGINE": "/models/engines/x.engine",
        }
    )
    assert (settings.backend, settings.precision) == ("tensorrt", "int8")


@pytest.mark.parametrize("backend", ["onnx", "torch"])
def test_settings_reject_int8_outside_tensorrt_and_name_both_variables(backend: str) -> None:
    with pytest.raises(SettingsError) as error:
        load_settings({**MINIMAL, "OPENMOT_BACKEND": backend, "OPENMOT_PRECISION": "int8"})
    message = str(error.value)
    assert "OPENMOT_BACKEND" in message and "OPENMOT_PRECISION" in message


def test_settings_tensorrt_fp32_message_lists_fp16_and_int8() -> None:
    with pytest.raises(SettingsError) as error:
        load_settings({**MINIMAL, "OPENMOT_BACKEND": "tensorrt", "OPENMOT_PRECISION": "fp32"})
    assert "fp16" in str(error.value) and "int8" in str(error.value)
