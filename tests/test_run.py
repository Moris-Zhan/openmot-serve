import hashlib
import json
import platform
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from eval.detection_cache import CacheKey, detect_with_cache
from eval.run import BenchmarkConfig, Dependencies, main
from eval.tuning import read_params
from inference.artifacts import ArtifactError, ArtifactInfo
from inference.backends import describe_artifact
from tracking.bytetrack import TrackerParams, run_tracker
from tracking.detector import DetectorSettings
from tracking.env_check import CheckOutcome, EnvReport
from tracking.motchallenge import format_result
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256

SEQUENCES = ("SYN-01-FRCNN", "SYN-02-FRCNN")
LENGTH = 20
PASSING = CheckOutcome(EnvReport("Fake GPU", "555.00", "2.7.1+cu128", "12.8", True), ())


def _write_sequence(data_root: Path, name: str) -> None:
    sequence = data_root / "train" / name
    (sequence / "img1").mkdir(parents=True)
    (sequence / "gt").mkdir()
    for number in range(1, LENGTH + 1):
        cv2.imwrite(str(sequence / "img1" / f"{number:06d}.jpg"), np.full((32, 48, 3), 90, np.uint8))
    (sequence / "seqinfo.ini").write_text(
        f"[Sequence]\nname={name}\nimDir=img1\nframeRate=30\nseqLength={LENGTH}\n"
        "imWidth=48\nimHeight=32\nimExt=.jpg\n"
    )
    lines = [
        f"{frame},{identity},{100 + 3 * frame + offset},200,40,100,1,1,1"
        for frame in range(1, LENGTH + 1)
        for identity, offset in ((1, 0), (2, 300))
    ]
    (sequence / "gt" / "gt.txt").write_text("\n".join(lines) + "\n")


class FakeDetector:
    def __init__(self) -> None:
        self.calls = 0
        self.on_call: list = []

    def __call__(self, image: np.ndarray) -> np.ndarray:
        for hook in self.on_call:
            hook(self.calls)
        self.calls += 1
        step = self.calls % 10
        left = 100.0 + 3.0 * step
        return np.array(
            [[left, 200.0, left + 40.0, 300.0, 0.9], [left + 300.0, 200.0, left + 340.0, 300.0, 0.65]]
        )


class Harness:
    def __init__(self, tmp_path: Path, outcome: CheckOutcome = PASSING) -> None:
        self.data_root = tmp_path / "data" / "MOT17"
        self.results_root = tmp_path / "data" / "results"
        self.detector = FakeDetector()
        self.loads = 0
        self.loaded_with: list[tuple[str, str]] = []
        self.load_calls: list[tuple[str, Path | None]] = []
        self.resolutions: list[tuple[str, DetectorSettings, Path | None]] = []
        self.artifact: ArtifactInfo | None = None
        self.resolve_error: Exception | None = None
        for name in SEQUENCES:
            _write_sequence(self.data_root, name)
        self.dependencies = Dependencies(lambda: outcome, self._load, self._resolve)

    def _load(
        self, weights: Path, precision: str, device: str, backend: str = "torch", engine: Path | None = None
    ) -> FakeDetector:
        self.loads += 1
        self.loaded_with.append((precision, device))
        self.load_calls.append((backend, engine))
        return self.detector

    def _resolve(
        self, backend: str, settings: DetectorSettings, weights: Path, engine: Path | None
    ) -> ArtifactInfo | None:
        self.resolutions.append((backend, settings, engine))
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.artifact if backend != "torch" else None

    def run(self, run_name: str, extra: tuple[str, ...] = ()) -> int:
        argv = [
            "--data-root", str(self.data_root),
            "--results-root", str(self.results_root),
            "--run-name", run_name,
            "--device", "cpu",
            "--sequences", *SEQUENCES,
            *extra,
        ]
        return main(argv, self.dependencies)


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_run_writes_the_params_results_and_summary(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    assert harness.run("first") == 0
    run_dir = harness.results_root / "first"
    for name in SEQUENCES:
        assert (run_dir / "results" / f"{name}.txt").is_file()
    assert (run_dir / "params.json").is_file()
    assert (run_dir / "summary.md").is_file()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert set(summary["scores"]["per_sequence"]) == set(SEQUENCES)
    for scores in (*summary["scores"]["per_sequence"].values(), summary["scores"]["combined"]):
        assert set(scores) == {"hota", "mota", "idf1"}


def test_the_markdown_table_lists_each_sequence_and_the_combined_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    harness.run("first")
    table = (harness.results_root / "first" / "summary.md").read_text()
    assert "| 序列 | HOTA | MOTA | IDF1 |" in table
    assert all(f"| {name} |" in table for name in SEQUENCES)
    assert "| 合併 |" in table
    assert table in capsys.readouterr().out


def test_the_validation_scores_use_the_tuned_parameters(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run("first")
    run_dir = harness.results_root / "first"
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["tracker_params"] == json.loads((run_dir / "params.json").read_text())


def test_validation_results_are_tracked_with_the_tuned_parameters(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run("first")
    run_dir = harness.results_root / "first"
    tuned = read_params(run_dir / "params.json")
    assert tuned != TrackerParams()
    key = CacheKey.from_settings(DetectorSettings(), WEIGHTS_SHA256)

    def refuse(image: np.ndarray) -> np.ndarray:
        raise AssertionError("the cache should have been hit")

    for name in SEQUENCES:
        cached = detect_with_cache(
            harness.results_root / "cache", key, f"{name}-validation",
            run_dir / "halves" / name / "validation" / "img1", refuse,
        )
        expected = format_result(run_tracker(cached, tuned))
        assert (run_dir / "results" / f"{name}.txt").read_text() == expected
        assert expected != format_result(run_tracker(cached, TrackerParams()))


def test_each_half_gets_its_own_cache_entry(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run("first")
    entries = sorted(path.name.rsplit("-", 1)[0] for path in (harness.results_root / "cache").glob("*.npz"))
    assert entries == sorted(f"{name}-{half}" for name in SEQUENCES for half in ("tuning", "validation"))


@pytest.mark.parametrize("precision", ["fp16", "fp32"])
def test_the_summary_matches_the_settings_actually_used(tmp_path: Path, precision: str) -> None:
    environment = CheckOutcome(EnvReport("Test GPU 9000", "999.1", "9.9.9+cuX", "12.9", True), ())
    harness = Harness(tmp_path, environment)
    harness.run("first", ("--precision", precision))
    run_dir = harness.results_root / "first"
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["weights"] == {"id": WEIGHTS_ID, "sha256": WEIGHTS_SHA256}
    assert summary["precision"] == precision == harness.loaded_with[0][0]
    assert summary["detector"]["precision"] == precision
    assert summary["backend"] == "torch" == summary["detector"]["backend"]
    assert summary["tracker_params"] == json.loads((run_dir / "params.json").read_text())
    assert summary["gpu"] == "Test GPU 9000"
    assert summary["software"] == {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "torch": "9.9.9+cuX",
        "cuda": "12.9",
        "driver": "999.1",
    }


def test_no_validation_half_exists_while_tuning_detects(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    tuning_calls = len(SEQUENCES) * (LENGTH // 2)
    seen: list[bool] = []
    run_dir = harness.results_root / "first"
    harness.detector.on_call.append(
        lambda index: seen.append(any(run_dir.glob("halves/*/validation"))) if index < tuning_calls else None
    )
    harness.run("first")
    assert len(seen) == tuning_calls and not any(seen)
    assert any(run_dir.glob("halves/*/validation"))


def test_missing_data_fails_with_a_pointer_to_the_download_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    harness.data_root = tmp_path / "nowhere"
    assert harness.run("first") != 0
    assert "data/download_mot.sh" in capsys.readouterr().err
    assert not harness.results_root.exists()


def test_a_failed_environment_check_stops_before_any_detection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = CheckOutcome(PASSING.report, ("CUDA is not available to PyTorch (2.7.1)",))
    harness = Harness(tmp_path, failing)
    assert harness.run("first") == 1
    assert "CUDA is not available" in capsys.readouterr().err
    assert harness.loads == 0 and harness.detector.calls == 0


def test_an_existing_run_folder_is_never_reused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    harness = Harness(tmp_path)
    assert harness.run("first") == 0
    before = _tree_digest(harness.results_root / "first")
    assert harness.run("first") == 1
    assert "already exists" in capsys.readouterr().err
    assert _tree_digest(harness.results_root / "first") == before


def test_two_runs_leave_git_clean_and_the_raw_data_untouched(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("data/MOT17/\ndata/results/\n")
    for command in (
        ["git", "init", "-q"],
        ["git", "add", ".gitignore"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)
    harness = Harness(tmp_path)
    raw_before = _tree_digest(harness.data_root)
    assert harness.run("first") == 0
    assert harness.run("second") == 0
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert status.stdout == ""
    assert _tree_digest(harness.data_root) == raw_before


def test_a_second_run_reuses_the_cache_and_reproduces_the_outputs(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run("first")
    calls_after_first, loads_after_first = harness.detector.calls, harness.loads
    harness.run("second")
    assert harness.detector.calls == calls_after_first
    assert harness.loads == loads_after_first
    first, second = (harness.results_root / name for name in ("first", "second"))
    for relative in ("summary.json", "summary.md", "params.json"):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
    for name in SEQUENCES:
        assert (first / "results" / f"{name}.txt").read_bytes() == (second / "results" / f"{name}.txt").read_bytes()


def test_a_different_precision_does_not_reuse_the_cache(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run("first")
    calls = harness.detector.calls
    harness.run("second", ("--precision", "fp32"))
    assert harness.detector.calls == 2 * calls


def test_invalid_precision_is_rejected_by_the_parser(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        Harness(tmp_path).run("first", ("--precision", "int4"))


def test_config_is_a_frozen_dataclass() -> None:
    config = BenchmarkConfig(Path("a"), Path("b"), "run", "fp16", Path("w"), "cpu")
    with pytest.raises(AttributeError):
        config.run_name = "other"  # type: ignore[misc]


TENSORRT_SIDECAR = {
    "onnx_sha256": "c" * 64,
    "tensorrt_version": "10.13.3.9",
    "gpu_name": "Fake GPU",
    "precision": "fp16",
    "workspace_bytes": 8 * 2**30,
    "optimization_level": "default",
    "builder_flags": ["FP16"],
}
ONNX_SIDECAR = {"onnx_sha256": "c" * 64}


def test_the_default_backend_is_torch_with_no_artifact_record(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    assert harness.run("first") == 0
    summary = json.loads((harness.results_root / "first" / "summary.json").read_text())
    assert summary["backend"] == "torch" and "artifact" not in summary
    assert harness.load_calls == [("torch", None)] and [call[0] for call in harness.resolutions] == ["torch"]


def test_an_exported_backend_is_resolved_loaded_and_recorded_in_the_summary(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("models/engines/m.engine"), TENSORRT_SIDECAR)
    assert harness.run("trt", ("--backend", "tensorrt")) == 0
    summary = json.loads((harness.results_root / "trt" / "summary.json").read_text())
    assert summary["backend"] == "tensorrt" and summary["detector"]["backend"] == "tensorrt"
    assert summary["artifact"] == describe_artifact("tensorrt", harness.artifact)
    assert summary["artifact"]["file"] == "m.engine" and summary["artifact"]["tensorrt"] == "10.13.3.9"
    assert harness.load_calls == [("tensorrt", Path("models/engines/m.engine"))]


def test_the_onnx_summary_records_the_file_hash_and_the_runtime_version(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("m.onnx"), ONNX_SIDECAR)
    assert harness.run("onnx", ("--backend", "onnx", "--precision", "fp32")) == 0
    summary = json.loads((harness.results_root / "onnx" / "summary.json").read_text())
    assert summary["artifact"] == describe_artifact("onnx", harness.artifact)
    assert summary["artifact"]["onnx_sha256"] == "c" * 64 and summary["precision"] == "fp32"


def test_an_explicit_engine_path_is_handed_to_the_resolver(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("custom.engine"), TENSORRT_SIDECAR)
    assert harness.run("trt", ("--backend", "tensorrt", "--engine", "custom.engine")) == 0
    assert harness.resolutions[0][2] == Path("custom.engine")


def test_an_unsupported_combination_is_rejected_before_anything_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    assert harness.run("bad", ("--backend", "onnx")) != 0
    error = capsys.readouterr().err
    assert "onnx" in error and "fp32" in error
    assert harness.loads == 0 and harness.resolutions == [] and not harness.results_root.exists()


def test_a_missing_or_mismatched_artifact_writes_no_result_folder_and_loads_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    harness.resolve_error = ArtifactError("weights_sha256 mismatch: sidecar has a, current is b")
    assert harness.run("stale", ("--backend", "tensorrt")) != 0
    assert "weights_sha256 mismatch" in capsys.readouterr().err
    assert harness.loads == 0 and harness.detector.calls == 0 and not (harness.results_root / "stale").exists()


def test_a_stale_artifact_is_caught_even_when_the_detection_cache_would_fully_hit(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("m.engine"), TENSORRT_SIDECAR)
    assert harness.run("first", ("--backend", "tensorrt")) == 0
    loads = harness.loads
    harness.resolve_error = ArtifactError("gpu_name mismatch")
    assert harness.run("second", ("--backend", "tensorrt")) != 0
    assert harness.loads == loads and not (harness.results_root / "second").exists()


def test_a_cache_hit_still_resolves_the_artifact_but_loads_no_model(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("m.engine"), TENSORRT_SIDECAR)
    harness.run("first", ("--backend", "tensorrt"))
    loads, resolutions = harness.loads, len(harness.resolutions)
    assert harness.run("second", ("--backend", "tensorrt")) == 0
    assert harness.loads == loads and len(harness.resolutions) == resolutions + 1


def test_backends_do_not_share_cached_detections(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.artifact = ArtifactInfo(Path("m.engine"), TENSORRT_SIDECAR)
    harness.run("torch-run")
    calls_after_torch = harness.detector.calls
    harness.run("trt-run", ("--backend", "tensorrt"))
    assert harness.detector.calls > calls_after_torch
