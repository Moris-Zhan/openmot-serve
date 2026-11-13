import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECTION = GUIDE[GUIDE.index("## 延遲與吞吐") :]
SUBSECTIONS = ["### 量測定義", "### 執行", "### 基準結果（PyTorch FP16）", "### 驗證紀錄", "### 限制"]
RESULT_ROW = re.compile(r"^\| (\d) \| ([\d.]+) / ([\d.]+) \| ([\d.]+) / ([\d.]+) \| ([\d.]+) \|$", re.M)
RESULTS = ROOT / "data" / "results" / "latency"


def test_the_latency_section_has_its_subsections_in_order() -> None:
    positions = [SECTION.index(heading) for heading in SUBSECTIONS]
    assert positions == sorted(positions)


def test_the_definitions_match_what_the_command_measures() -> None:
    for phrase in ("純推論", "偵測端到端", "追蹤器不計入", "numpy.percentile", "50 張預熱", "500 張", "不在同一輪內"):
        assert phrase in SECTION


def test_the_limits_say_what_is_not_covered() -> None:
    limits = SECTION[SECTION.index("### 限制") :]
    for phrase in ("一張 RTX 5090", "閒置的 GPU", "不是整條服務", "batch 1", "CI 不覆蓋"):
        assert phrase in limits


def test_the_baseline_table_has_two_runs() -> None:
    assert [row[0] for row in RESULT_ROW.findall(SECTION)] == ["1", "2"]


def test_the_documented_old_summaries_are_read_as_torch() -> None:
    assert "沒有 `backend` 欄位，閱讀時視為 `torch`" in SECTION


def test_the_evaluation_guide_describes_the_backend_in_the_cache_key_and_summary() -> None:
    assert "推論後端" in GUIDE and "不同後端的偵測結果不會互相沿用" in GUIDE


def test_the_readme_ticks_only_the_baseline_item_of_v0_5() -> None:
    roadmap = README[README.index("### v0.5") : README.index("### Later")]
    assert "- [x] Latency and throughput baseline" in roadmap
    assert roadmap.count("- [x]") == 1 and roadmap.count("- [ ]") == 3


@pytest.mark.skipif(not (RESULTS / "baseline-torch-fp16-1").is_dir(), reason="local baseline results not present")
def test_the_documented_numbers_match_the_recorded_results() -> None:
    for number, inference_median, inference_p95, end_median, end_p95, fps in RESULT_ROW.findall(SECTION):
        document = json.loads((RESULTS / f"baseline-torch-fp16-{number}" / "latency.json").read_text())["results"]
        assert f"{document['inference']['median_ms']:.2f}" == inference_median
        assert f"{document['inference']['p95_ms']:.2f}" == inference_p95
        assert f"{document['end_to_end']['median_ms']:.2f}" == end_median
        assert f"{document['end_to_end']['p95_ms']:.2f}" == end_p95
        assert f"{document['throughput_fps']:.1f}" == fps
