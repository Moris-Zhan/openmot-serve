import subprocess
import sys
from pathlib import Path

import pytest

from jobs.settings import SettingsError, load_settings

MINIMAL = {"OPENMOT_STORAGE_DIR": "/srv/openmot"}


def test_defaults_keep_the_pytorch_backend_and_no_engine() -> None:
    settings = load_settings(MINIMAL)
    assert settings.backend == "torch"
    assert settings.engine is None
    assert settings.precision == "fp16"


def test_tensorrt_with_the_default_fp16_and_an_engine_path_is_accepted() -> None:
    settings = load_settings(
        {**MINIMAL, "OPENMOT_BACKEND": "tensorrt", "OPENMOT_ENGINE": "/models/engines/x.engine"}
    )
    assert settings.backend == "tensorrt"
    assert settings.engine == Path("/models/engines/x.engine")


def test_onnx_with_fp32_is_accepted() -> None:
    settings = load_settings({**MINIMAL, "OPENMOT_BACKEND": "onnx", "OPENMOT_PRECISION": "fp32"})
    assert settings.backend == "onnx"


def test_onnx_with_the_default_fp16_is_rejected_and_names_both_variables() -> None:
    with pytest.raises(SettingsError) as error:
        load_settings({**MINIMAL, "OPENMOT_BACKEND": "onnx"})
    message = str(error.value)
    assert "OPENMOT_BACKEND" in message and "OPENMOT_PRECISION" in message
    assert "fp32" in message


def test_tensorrt_with_fp32_is_rejected() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_PRECISION"):
        load_settings({**MINIMAL, "OPENMOT_BACKEND": "tensorrt", "OPENMOT_PRECISION": "fp32"})


def test_unknown_backend_names_the_variable_and_lists_the_supported_ones() -> None:
    with pytest.raises(SettingsError) as error:
        load_settings({**MINIMAL, "OPENMOT_BACKEND": "coreml"})
    message = str(error.value)
    assert "OPENMOT_BACKEND" in message
    assert all(name in message for name in ("torch", "onnx", "tensorrt"))


def test_a_relative_engine_path_is_rejected() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_ENGINE.*absolute"):
        load_settings({**MINIMAL, "OPENMOT_BACKEND": "tensorrt", "OPENMOT_ENGINE": "engines/x.engine"})


@pytest.mark.parametrize("environ", [{}, {"OPENMOT_BACKEND": "torch"}])
def test_an_engine_with_the_pytorch_backend_is_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(SettingsError) as error:
        load_settings({**MINIMAL, **environ, "OPENMOT_ENGINE": "/models/engines/x.engine"})
    assert "OPENMOT_ENGINE" in str(error.value) and "OPENMOT_BACKEND" in str(error.value)


def test_loading_settings_does_not_import_torch() -> None:
    code = (
        "import sys\n"
        "from jobs.settings import load_settings\n"
        "load_settings({'OPENMOT_STORAGE_DIR': '/s', 'OPENMOT_BACKEND': 'tensorrt'})\n"
        "assert 'torch' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
