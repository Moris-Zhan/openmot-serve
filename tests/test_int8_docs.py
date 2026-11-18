import json
import re
from pathlib import Path

import pytest

from inference.build_engine import build_parser

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RESULTS = ROOT / "data" / "results"
LATENCY = RESULTS / "latency"
SECTION = GUIDE[GUIDE.index("## INT8 量化") :] if "## INT8 量化" in GUIDE else ""
THRESHOLD = 1.0
BASELINE_RUN = "full-fp16-3"
INT8_RUN = "full-tensorrt-int8-1"
ACCURACY_ROW = re.compile(
    r"^\| `(full-[\w-]+)` \| (\w+) \| (\w+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) "
    r"\| ([+\-\d.]+) \| ([+\-\d.]+) \| ([+\-\d.]+) \|$",
    re.M,
)
LATENCY_ROW = re.compile(
    r"^\| `(lat-[\w-]+)` \| (\w+) \| (\w+) \| ([\d.]+) / ([\d.]+) \| ([\d.]+) / ([\d.]+) \| ([\d.]+) \|$", re.M
)
VERDICT = re.compile(r"\*\*標示：(建議使用|不建議)\*\*")
recorded = pytest.mark.skipif(not (RESULTS / INT8_RUN).is_dir(), reason="local INT8 results not present")


def _combined(run: str) -> dict[str, float]:
    return json.loads((RESULTS / run / "summary.json").read_text())["scores"]["combined"]


def test_the_int8_section_follows_the_onnx_and_tensorrt_section() -> None:
    assert "## INT8 量化" in GUIDE
    assert GUIDE.index("## ONNX 與 TensorRT") < GUIDE.index("## INT8 量化")


@pytest.mark.parametrize(
    "required",
    [
        "IInt8EntropyCalibrator2",
        "512",
        "Tuning Half",
        "Validation Half",
        "INT8 搭配 FP16",
        "python -m inference.build_engine --precision int8",
        "--backend tensorrt --precision int8",
        "10.13.3.9",
        "棄用",
        "OPENMOT_PRECISION=int8",
        "服務端對端未驗證",
        "GPU 記憶體",
        "未量測",
        "inference/calibration.py",
    ],
)
def test_the_section_covers_the_required_topic(required: str) -> None:
    assert required in SECTION


def test_the_stale_statements_about_int8_and_the_supported_combinations_are_gone() -> None:
    assert "INT8 量化不在這次範圍" not in GUIDE
    assert "`tensorrt`＋`fp16`／`int8`" in GUIDE


def test_the_documented_build_options_exist_on_the_command() -> None:
    arguments = build_parser().parse_args(
        ["--precision", "int8", "--calibration-frames", "64", "--data-root", "frames"]
    )
    assert arguments.precision == "int8" and arguments.calibration_frames == 64
    assert (ROOT / "inference" / "calibration.py").is_file()


def test_the_label_follows_the_fixed_threshold_from_the_documented_numbers() -> None:
    rows = {match[0]: float(match[3]) for match in ACCURACY_ROW.findall(SECTION)}
    gap = rows[INT8_RUN] - rows[BASELINE_RUN]
    assert VERDICT.findall(SECTION) == ["建議使用" if abs(gap) <= THRESHOLD else "不建議"]


def test_the_documented_differences_are_the_differences_of_the_documented_scores() -> None:
    rows = {match[0]: match for match in ACCURACY_ROW.findall(SECTION)}
    baseline = [float(value) for value in rows[BASELINE_RUN][3:6]]
    for run, match in rows.items():
        scores = [float(value) for value in match[3:6]]
        stated = [float(value) for value in match[6:9]]
        differences = [score - base for score, base in zip(scores, baseline, strict=True)]
        assert differences == pytest.approx(stated, abs=0.011), run


@recorded
def test_the_accuracy_rows_match_the_recorded_results() -> None:
    matches = ACCURACY_ROW.findall(SECTION)
    assert {match[0] for match in matches} >= {BASELINE_RUN, INT8_RUN}
    for run, backend, precision, hota, mota, idf1, *_ in matches:
        summary = json.loads((RESULTS / run / "summary.json").read_text())
        scores = summary["scores"]["combined"]
        assert (f"{scores['hota']:.2f}", f"{scores['mota']:.2f}", f"{scores['idf1']:.2f}") == (hota, mota, idf1)
        assert summary.get("backend", "torch") == backend and summary["precision"] == precision


@recorded
def test_the_latency_rows_match_the_recorded_results() -> None:
    matches = LATENCY_ROW.findall(SECTION)
    assert len(matches) == 4
    for run, backend, precision, inference_median, inference_p95, end_median, end_p95, fps in matches:
        document = json.loads((LATENCY / run / "latency.json").read_text())
        results = document["results"]
        assert (document["backend"], document["precision"]) == (backend, precision)
        assert f"{results['inference']['median_ms']:.2f}" == inference_median
        assert f"{results['inference']['p95_ms']:.2f}" == inference_p95
        assert f"{results['end_to_end']['median_ms']:.2f}" == end_median
        assert f"{results['end_to_end']['p95_ms']:.2f}" == end_p95
        assert f"{results['throughput_fps']:.1f}" == fps


@recorded
def test_the_readme_accuracy_row_carries_the_recorded_int8_scores() -> None:
    scores = _combined(INT8_RUN)
    row = f"| YOLOX | ByteTrack | INT8 | {scores['hota']:.2f} | {scores['mota']:.2f} | {scores['idf1']:.2f} |"
    assert row in README


@recorded
def test_the_readme_performance_rows_summarise_the_recorded_latency_runs() -> None:
    def summary(runs: list[str]) -> tuple[str, str]:
        documents = [json.loads((LATENCY / run / "latency.json").read_text()) for run in runs]
        medians = [document["results"]["end_to_end"]["median_ms"] for document in documents]
        speeds = [document["results"]["throughput_fps"] for document in documents]
        return f"{min(medians):.1f}–{max(medians):.1f}", f"{min(speeds):.1f}–{max(speeds):.1f}"

    int8 = summary(["lat-tensorrt-int8-1", "lat-tensorrt-int8-2"])
    fp16 = summary(["lat-tensorrt-fp16-cmp-1", "lat-tensorrt-fp16-cmp-2"])
    assert f"| INT8 (TensorRT) | {int8[0]} | {int8[1]} | TBD |" in README
    assert f"| FP16 (TensorRT) | {fp16[0]} | {fp16[1]} | TBD |" in README


def test_the_readme_says_gpu_memory_was_not_measured() -> None:
    performance = README[README.index("### Serving performance") : README.index("### Dashboards")]
    assert "未量測" in performance and "GPU memory" in performance


def test_the_readme_roadmap_ticks_the_int8_items_and_labels_the_result_honestly() -> None:
    roadmap = README[README.index("### v0.5") : README.index("### Later")]
    assert "- [x] INT8 quantization with calibration" in roadmap
    assert "- [x] Before/after comparison of latency, throughput and HOTA" in roadmap
    assert roadmap.count("- [x]") == 4 and roadmap.count("- [ ]") == 0
    assert "not recommended" in roadmap
