import hashlib
import json
import platform
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from eval.latency import Dependencies, main
from tracking.detector import PreparedFrame, StagedDetector
from tracking.env_check import CheckOutcome, EnvReport
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256

SEQUENCES = ("SYN-01-FRCNN", "SYN-02-FRCNN")
LENGTH = 20
PASSING = CheckOutcome(EnvReport("Fake GPU", "555.00", "2.7.1+cu128", "12.8", True), ())
FAILING = CheckOutcome(
    EnvReport("n/a", "n/a", "2.7.1+cpu", None, False), ("CUDA is not available to PyTorch (2.7.1+cpu)",)
)


def _write_sequence(data_root: Path, name: str) -> None:
    images = data_root / "train" / name / "img1"
    images.mkdir(parents=True)
    for number in range(1, LENGTH + 1):
        cv2.imwrite(str(images / f"{number:06d}.jpg"), np.full((32, 48, 3), 90, np.uint8))


class Ticking:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.010
        return self.now


class Harness:
    def __init__(self, tmp_path: Path, outcome: CheckOutcome = PASSING) -> None:
        self.data_root = tmp_path / "data" / "MOT17"
        self.results_root = tmp_path / "data" / "results"
        self.loads: list[tuple[str, str]] = []
        for name in SEQUENCES:
            _write_sequence(self.data_root, name)
        self.dependencies = Dependencies(lambda: outcome, self._load, Ticking())

    def _load(self, weights: Path, precision: str, device: str) -> StagedDetector:
        self.loads.append((precision, device))
        return StagedDetector(
            prepare=lambda image: PreparedFrame(torch.zeros(1), 1.0),
            infer=lambda prepared: torch.zeros(1),
            finish=lambda raw, prepared: np.empty((0, 5)),
            synchronize=lambda: None,
        )

    def run(self, run_name: str = "first", extra: tuple[str, ...] = ()) -> int:
        argv = [
            "--data-root", str(self.data_root),
            "--results-root", str(self.results_root),
            "--run-name", run_name,
            "--device", "cpu",
            "--sequences", *SEQUENCES,
            "--warmup", "3",
            "--measured", "10",
            *extra,
        ]
        return main(argv, self.dependencies)

    def result_dir(self, run_name: str = "first") -> Path:
        return self.results_root / "latency" / run_name


def _digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_successful_run_writes_json_and_markdown(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    assert harness.run() == 0
    assert (harness.result_dir() / "latency.json").is_file()
    assert (harness.result_dir() / "latency.md").is_file()


def test_the_json_records_how_it_was_produced(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run(extra=("--precision", "fp32"))
    document = json.loads((harness.result_dir() / "latency.json").read_text())
    assert document["backend"] == "torch"
    assert document["precision"] == "fp32" == harness.loads[0][0]
    assert document["detector"]["input_height"] == 800 and document["detector"]["input_width"] == 1440
    assert document["weights"] == {"id": WEIGHTS_ID, "sha256": WEIGHTS_SHA256}
    assert document["warmup_frames"] == 3 and document["measured_frames"] == 10
    assert document["sequences"] == list(SEQUENCES)
    assert document["gpu"] == "Fake GPU"
    assert document["software"] == {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "torch": "2.7.1+cu128",
        "cuda": "12.8",
        "driver": "555.00",
    }


def test_the_json_numbers_come_from_the_measurement(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run()
    results = json.loads((harness.result_dir() / "latency.json").read_text())["results"]
    assert results["end_to_end"]["median_ms"] == pytest.approx(10.0)
    assert results["inference"]["median_ms"] == pytest.approx(10.0)
    assert results["throughput_fps"] == pytest.approx(100.0)


def test_the_markdown_is_a_chinese_table_with_both_layers_and_the_throughput(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.run()
    table = (harness.result_dir() / "latency.md").read_text()
    assert "| 層級 | 中位數 (ms) | p95 (ms) |" in table
    assert "| 純推論 | 10.00 |" in table and "| 偵測端到端 | 10.00 |" in table
    assert "100.0 FPS" in table


@pytest.mark.parametrize("option", ["--warmup", "--measured"])
@pytest.mark.parametrize("value", ["0", "-3", "abc", "2.5"])
def test_an_invalid_count_is_rejected_before_loading_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, value: str
) -> None:
    harness = Harness(tmp_path)
    with pytest.raises(SystemExit) as stop:
        harness.run(extra=(option, value))
    assert stop.value.code != 0
    assert option in capsys.readouterr().err
    assert harness.loads == []


def test_missing_data_fails_with_a_pointer_to_the_download_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    assert harness.run(extra=("--data-root", str(tmp_path / "nowhere"))) == 1
    assert "data/download_mot.sh" in capsys.readouterr().err
    assert harness.loads == [] and not harness.results_root.exists()


def test_a_failed_environment_check_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path, FAILING)
    assert harness.run() == 1
    assert "CUDA is not available" in capsys.readouterr().err
    assert harness.loads == [] and not harness.results_root.exists()


def test_an_existing_result_folder_is_never_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    assert harness.run() == 0
    before = _digest(harness.results_root)
    assert harness.run() == 1
    assert "already exists" in capsys.readouterr().err
    assert _digest(harness.results_root) == before


def test_a_run_leaves_the_raw_data_untouched(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    before = _digest(harness.data_root)
    harness.run("first")
    harness.run("second")
    assert _digest(harness.data_root) == before
