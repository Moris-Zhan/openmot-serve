import json
from pathlib import Path

from eval.detection_cache import CacheKey
from inference.artifacts import ArtifactInfo
from inference.backends import describe_artifact
from tests import test_latency_cli, test_run
from tracking.detector import DetectorSettings

CALIBRATION = {
    "calibrator": "IInt8EntropyCalibrator2",
    "frames": 512,
    "selection_sha256": "a" * 64,
    "cache": "m.engine.calib",
    "cache_sha256": "b" * 64,
    "source": "MOT17 train FRCNN Tuning Half",
}
INT8_SIDECAR = {
    **test_run.TENSORRT_SIDECAR,
    "precision": "int8",
    "builder_flags": ["INT8", "FP16"],
    "calibration": CALIBRATION,
}


def info(sidecar: dict) -> ArtifactInfo:
    return ArtifactInfo(Path("models/engines/m.engine"), sidecar)


def test_an_int8_record_carries_the_calibration_identity_in_the_build_block() -> None:
    record = describe_artifact("tensorrt", info(INT8_SIDECAR), lambda package: None)
    assert record is not None
    assert record["build"] == {
        "precision": "int8",
        "workspace_bytes": INT8_SIDECAR["workspace_bytes"],
        "optimization_level": INT8_SIDECAR["optimization_level"],
        "builder_flags": ["INT8", "FP16"],
        "calibration": {"frames": 512, "selection_sha256": "a" * 64, "cache_sha256": "b" * 64},
    }


def test_an_fp16_record_is_unchanged_and_has_no_calibration() -> None:
    record = describe_artifact("tensorrt", info(dict(test_run.TENSORRT_SIDECAR)), lambda package: None)
    assert record is not None and "calibration" not in record["build"]
    assert set(record["build"]) == {"precision", "workspace_bytes", "optimization_level", "builder_flags"}


def test_the_onnx_record_is_unchanged() -> None:
    record = describe_artifact("onnx", info({"onnx_sha256": "c" * 64}), lambda package: "1.22.0")
    assert record == {"file": "m.engine", "onnx_sha256": "c" * 64, "onnxruntime": "1.22.0"}


def test_the_detection_cache_key_separates_int8_from_fp16() -> None:
    fp16 = CacheKey.from_settings(DetectorSettings(backend="tensorrt", precision="fp16"), "w" * 64)
    int8 = CacheKey.from_settings(DetectorSettings(backend="tensorrt", precision="int8"), "w" * 64)
    assert fp16 != int8 and int8.precision == "int8"


def test_a_benchmark_summary_for_int8_records_the_precision_and_the_calibration(tmp_path: Path) -> None:
    harness = test_run.Harness(tmp_path)
    harness.artifact = info(INT8_SIDECAR)
    assert harness.run("int8", ("--backend", "tensorrt", "--precision", "int8")) == 0
    summary = json.loads((harness.results_root / "int8" / "summary.json").read_text())
    assert summary["precision"] == "int8" == summary["detector"]["precision"]
    assert summary["artifact"] == describe_artifact("tensorrt", harness.artifact)
    assert summary["artifact"]["build"]["calibration"]["frames"] == 512


def test_int8_does_not_reuse_the_cached_fp16_detections(tmp_path: Path) -> None:
    harness = test_run.Harness(tmp_path)
    harness.artifact = info(dict(test_run.TENSORRT_SIDECAR))
    harness.run("fp16", ("--backend", "tensorrt"))
    calls_after_fp16 = harness.detector.calls
    harness.artifact = info(INT8_SIDECAR)
    harness.run("int8", ("--backend", "tensorrt", "--precision", "int8"))
    assert harness.detector.calls > calls_after_fp16


def test_a_latency_result_for_int8_records_the_precision_and_the_calibration(tmp_path: Path) -> None:
    harness = test_latency_cli.Harness(tmp_path)
    harness.artifact = info(INT8_SIDECAR)
    assert harness.run(extra=("--backend", "tensorrt", "--precision", "int8")) == 0
    document = json.loads((harness.result_dir() / "latency.json").read_text())
    assert document["precision"] == "int8"
    assert document["artifact"] == describe_artifact("tensorrt", harness.artifact)
    assert document["artifact"]["build"]["calibration"]["selection_sha256"] == "a" * 64
