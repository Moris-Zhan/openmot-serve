import json
from pathlib import Path

import pytest

from tracking.bytetrack import TrackerParams
from tracking.config import (
    ConfigError,
    RunMetadata,
    load_tracker_params,
    metadata_path_for,
    write_metadata,
)
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256


def write_config(directory: Path, text: str) -> Path:
    path = directory / "tracking.toml"
    path.write_text(text)
    return path


def metadata_for(params: TrackerParams, precision: str = "fp16") -> RunMetadata:
    return RunMetadata(
        tracker=params, weights_id=WEIGHTS_ID, weights_sha256=WEIGHTS_SHA256, precision=precision
    )


def test_no_config_uses_defaults() -> None:
    assert load_tracker_params(None) == TrackerParams()


def test_custom_values_are_loaded(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "[tracker]\ndetection_threshold = 0.45\nmatch_threshold = 0.8\ntrack_buffer = 50\n",
    )
    assert load_tracker_params(path) == TrackerParams(0.45, 0.8, 50)


def test_partial_config_keeps_other_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[tracker]\ndetection_threshold = 0.45\n")
    params = load_tracker_params(path)
    assert params.detection_threshold == 0.45
    assert params.match_threshold == TrackerParams().match_threshold
    assert params.track_buffer == TrackerParams().track_buffer


def test_empty_config_file_means_defaults(tmp_path: Path) -> None:
    assert load_tracker_params(write_config(tmp_path, "")) == TrackerParams()


def test_integer_threshold_is_accepted_as_float(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[tracker]\ndetection_threshold = 1\n")
    assert load_tracker_params(path).detection_threshold == 1.0


@pytest.mark.parametrize(
    "text",
    [
        "[tracker]\ndetection_treshold = 0.5\n",
        "[tracker]\ndetection_threshold = \"high\"\n",
        "[tracker]\ntrack_buffer = 30.5\n",
        "[tracker]\ntrack_buffer = true\n",
        "[tracker]\ndetection_threshold = 0.05\n",
        "[tracker]\ntrack_buffer = 0\n",
        "[unknown]\nvalue = 1\n",
        "[tracker\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        load_tracker_params(write_config(tmp_path, text))


def test_missing_config_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_tracker_params(tmp_path / "absent.toml")


def test_metadata_sits_next_to_the_result_file() -> None:
    assert metadata_path_for(Path("out/MOT17-02.txt")) == Path("out/MOT17-02.meta.json")


def test_metadata_records_custom_parameters(tmp_path: Path) -> None:
    params = TrackerParams(detection_threshold=0.45, match_threshold=0.8, track_buffer=50)
    target = tmp_path / "SEQ.meta.json"
    write_metadata(target, metadata_for(params))
    recorded = json.loads(target.read_text())
    assert recorded["tracker"] == {
        "detection_threshold": 0.45,
        "match_threshold": 0.8,
        "track_buffer": 50,
    }


def test_metadata_records_default_parameters_when_none_given(tmp_path: Path) -> None:
    target = tmp_path / "SEQ.meta.json"
    write_metadata(target, metadata_for(load_tracker_params(None)))
    recorded = json.loads(target.read_text())["tracker"]
    defaults = TrackerParams()
    assert recorded == {
        "detection_threshold": defaults.detection_threshold,
        "match_threshold": defaults.match_threshold,
        "track_buffer": defaults.track_buffer,
    }


def test_metadata_identifies_weights_and_precision(tmp_path: Path) -> None:
    target = tmp_path / "SEQ.meta.json"
    write_metadata(target, metadata_for(TrackerParams(), precision="fp32"))
    recorded = json.loads(target.read_text())
    assert recorded["weights"] == {"id": WEIGHTS_ID, "sha256": WEIGHTS_SHA256}
    assert recorded["precision"] == "fp32"


def test_metadata_bytes_are_deterministic_and_leave_no_temp_files(tmp_path: Path) -> None:
    first, second = tmp_path / "a.meta.json", tmp_path / "b.meta.json"
    write_metadata(first, metadata_for(TrackerParams()))
    write_metadata(second, metadata_for(TrackerParams()))
    assert first.read_bytes() == second.read_bytes()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.meta.json", "b.meta.json"]


def test_metadata_file_is_world_readable(tmp_path: Path) -> None:
    target = tmp_path / "SEQ.meta.json"
    write_metadata(target, metadata_for(TrackerParams()))
    assert target.stat().st_mode & 0o777 == 0o644


def test_unsupported_precision_is_rejected() -> None:
    with pytest.raises(ValueError, match="precision"):
        metadata_for(TrackerParams(), precision="int4")
