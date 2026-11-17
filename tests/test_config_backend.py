import json
from pathlib import Path

import pytest

from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata, write_metadata
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256

TORCH_METADATA_TEXT = (
    "{\n"
    '  "precision": "fp16",\n'
    '  "tracker": {\n'
    '    "detection_threshold": 0.6,\n'
    '    "match_threshold": 0.9,\n'
    '    "track_buffer": 30\n'
    "  },\n"
    '  "weights": {\n'
    f'    "id": "{WEIGHTS_ID}",\n'
    f'    "sha256": "{WEIGHTS_SHA256}"\n'
    "  }\n"
    "}\n"
)


def write_for(directory: Path, precision: str, backend: str | None) -> dict[str, object]:
    target = directory / "SEQ.meta.json"
    arguments = {} if backend is None else {"backend": backend}
    write_metadata(
        target, RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, precision, **arguments)
    )
    return json.loads(target.read_text())


@pytest.mark.parametrize(("backend", "precision"), [("tensorrt", "fp16"), ("onnx", "fp32")])
def test_non_torch_backend_is_recorded(tmp_path: Path, backend: str, precision: str) -> None:
    assert write_for(tmp_path, precision, backend)["backend"] == backend


def test_torch_metadata_is_byte_identical_to_before(tmp_path: Path) -> None:
    write_for(tmp_path, "fp16", None)
    assert (tmp_path / "SEQ.meta.json").read_text() == TORCH_METADATA_TEXT


def test_explicit_torch_backend_leaves_no_backend_field(tmp_path: Path) -> None:
    assert "backend" not in write_for(tmp_path, "fp16", "torch")


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="backend"):
        RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, "fp16", backend="coreml")


def test_cli_records_a_non_torch_backend_and_omits_torch(tmp_path: Path) -> None:
    from tests.test_run_cli import make_sequence, run

    frames = make_sequence(tmp_path / "seq")
    assert run([str(frames), "--output", str(tmp_path / "a.txt"), "--backend", "tensorrt"]) == 0
    assert json.loads((tmp_path / "a.meta.json").read_text())["backend"] == "tensorrt"
    assert run([str(frames), "--output", str(tmp_path / "b.txt")]) == 0
    assert "backend" not in json.loads((tmp_path / "b.meta.json").read_text())
