from pathlib import Path

import pytest

from inference.artifacts import file_sha256, read_sidecar, sidecar_path
from inference.export_onnx import DEFAULT_WEIGHTS, default_dependencies, export_onnx
from tracking.detector import DetectorSettings
from tracking.yolox import WEIGHTS_SHA256

onnx = pytest.importorskip("onnx")

pytestmark = pytest.mark.gpu


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("engines")
    return export_onnx(DEFAULT_WEIGHTS, directory, DetectorSettings(precision="fp32"), default_dependencies())


def test_the_exported_file_passes_the_onnx_checker(exported: Path) -> None:
    onnx.checker.check_model(str(exported))


def test_the_input_is_fixed_to_one_by_three_by_800_by_1440(exported: Path) -> None:
    graph = onnx.load(str(exported)).graph
    dimensions = [dimension.dim_value for dimension in graph.input[0].type.tensor_type.shape.dim]
    assert dimensions == [1, 3, 800, 1440]


def test_the_output_has_23625_candidates_of_85_channels(exported: Path) -> None:
    graph = onnx.load(str(exported)).graph
    dimensions = [dimension.dim_value for dimension in graph.output[0].type.tensor_type.shape.dim]
    assert dimensions == [1, 23625, 85]


def test_the_sidecar_agrees_with_the_file_it_describes(exported: Path) -> None:
    sidecar = read_sidecar(sidecar_path(exported))
    assert sidecar["onnx_sha256"] == file_sha256(exported)
    assert sidecar["weights_sha256"] == WEIGHTS_SHA256
    assert (sidecar["input_height"], sidecar["input_width"]) == (800, 1440)
