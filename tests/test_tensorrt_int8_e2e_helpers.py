from pathlib import Path

from tests.test_tensorrt_int8_worker_e2e import ENGINES_VOLUME, FRAMES_VOLUME, PROJECT, _host_int8_engine

ENGINE = "yolox_x-5652330b-800x1440-trt10.13.3.9-nvidia-geforce-rtx-5090-int8.engine"


def test_no_engine_directory_or_no_int8_engine_means_nothing_to_copy(tmp_path: Path) -> None:
    assert _host_int8_engine(tmp_path / "missing") is None
    assert _host_int8_engine(tmp_path) is None
    (tmp_path / ENGINE.replace("int8", "fp16")).write_bytes(b"engine")
    assert _host_int8_engine(tmp_path) is None


def test_an_engine_without_its_sidecar_is_not_used(tmp_path: Path) -> None:
    (tmp_path / ENGINE).write_bytes(b"engine")
    assert _host_int8_engine(tmp_path) is None


def test_an_engine_with_its_sidecar_is_found(tmp_path: Path) -> None:
    (tmp_path / ENGINE).write_bytes(b"engine")
    (tmp_path / f"{ENGINE}.json").write_text("{}")
    assert _host_int8_engine(tmp_path) == tmp_path / ENGINE


def test_every_resource_the_end_to_end_test_creates_has_a_dedicated_name() -> None:
    assert PROJECT.startswith("openmot-trt-e2e")
    assert ENGINES_VOLUME.startswith("openmot-trt-e2e") and FRAMES_VOLUME.startswith("openmot-trt-e2e")
    assert "openmot-weights" not in (PROJECT, ENGINES_VOLUME, FRAMES_VOLUME)
