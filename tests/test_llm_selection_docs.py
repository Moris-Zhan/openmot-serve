import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "llm-selection.md"
RESULTS = ROOT / "docs" / "llm-selection-results"
README = (ROOT / "README.md").read_text(encoding="utf-8")
REPORT = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.is_file() else ""
CANDIDATES = ("qwen3:14b", "mimo-v2.6:9b-q4_K_M", "gpt-oss:latest", "granite4:3b")


def _section(title: str) -> str:
    return REPORT.split(title, 1)[1].split("\n## ", 1)[0] if title in REPORT else ""


def test_the_report_exists() -> None:
    assert REPORT_PATH.is_file()


def test_the_thresholds_are_written_with_their_values() -> None:
    section = _section("## 門檻")
    for value in ("90%", "95%", "16 GiB"):
        assert value in section, value
    assert section.count("90%") >= 2


def test_the_candidate_table_has_the_required_columns_and_every_candidate() -> None:
    section = _section("## 候選")
    for column in ("來源", "授權", "摘要", "大小", "載入後顯存"):
        assert column in section, column
    for model in CANDIDATES:
        assert model in section, model


def test_the_four_metrics_are_named() -> None:
    for name in ("工具呼叫格式正確率", "答案正確率", "拒答正確率", "延遲中位數"):
        assert name in REPORT, name


def test_the_worker_vram_increments_are_reported() -> None:
    assert "922" in REPORT and "1360" in REPORT


def test_the_conclusion_states_the_outcome_without_lowering_the_thresholds() -> None:
    section = _section("## 結論")
    assert "沒有候選通過" in section and "不降低門檻" in section


def test_the_limits_section_lists_every_limit() -> None:
    section = _section("## 限制")
    for phrase in ("名單內", "一個真實序列", "結構化輸出", "雲端", "映像大小", "請求內容"):
        assert phrase in section, phrase


def test_the_failure_cases_include_the_empty_structured_answer_of_thinking_models() -> None:
    section = _section("## 失敗案例")
    assert "granite4:3b" in section and "q25" in section
    assert "gpt-oss" in section and "not valid JSON" in section


def test_the_raw_results_are_committed_for_every_candidate() -> None:
    assert "docs/llm-selection-results" in REPORT
    for model in CANDIDATES:
        safe = model.replace(":", "_")
        raw, summary = RESULTS / "raw" / f"{safe}.json", RESULTS / "summary" / f"{safe}.json"
        assert raw.is_file() and summary.is_file(), model
        assert json.loads(summary.read_text())["model"] == model
        assert "idle_check" in json.loads(summary.read_text())
    assert (RESULTS / "questions.json").is_file() and (RESULTS / "worker_memory.json").is_file()


def test_the_readme_links_the_report_without_ticking_the_roadmap_line() -> None:
    line = next(item for item in README.splitlines() if "LLM agent" in item and item.lstrip().startswith("- ["))
    assert line.lstrip().startswith("- [ ]") and "docs/llm-selection.md" in line
