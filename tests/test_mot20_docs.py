import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
GUIDE = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
RUNS = ("mot20-fp16-3", "mot20-fp16-4")
SUMMARY = ROOT / "data" / "results" / RUNS[0] / "summary.json"
SEQUENCES = ("MOT20-01", "MOT20-02", "MOT20-03", "MOT20-05")
recorded = pytest.mark.skipif(not SUMMARY.is_file(), reason="local MOT20 results not present")


def _readme_section() -> str:
    assert "### Tracking accuracy — MOT20 validation half" in README
    return README.split("### Tracking accuracy — MOT20 validation half")[1].split("\n### ")[0]


def _guide_section() -> str:
    assert "## MOT20" in GUIDE
    return GUIDE.split("## MOT20")[1].split("\n## ")[0]


def _scores() -> dict:
    return json.loads(SUMMARY.read_text())["scores"]


def test_the_protocol_no_longer_defers_mot20_to_after_v0_5() -> None:
    assert "MOT20 is evaluated after v0.5" not in README


def test_the_roadmap_item_is_ticked() -> None:
    assert "- [x] MOT20 benchmark" in README
    assert "- [ ] MOT20 benchmark" not in README


def test_the_readme_section_states_every_limit_of_the_numbers() -> None:
    section = _readme_section()
    for fragment in ("PyTorch FP16", "參數沿用 MOT17", "未針對 MOT20", "未微調", "不可與", "mot20-fp16-3"):
        assert fragment in section, fragment
    assert "延遲" in section and "未量測" in section


@recorded
def test_the_readme_row_matches_the_recorded_combined_scores() -> None:
    combined = _scores()["combined"]
    numbers = " | ".join(f"{combined[key]:.2f}" for key in ("hota", "mota", "idf1"))
    expected = f"| YOLOX | ByteTrack | FP16 | {numbers} |"
    assert expected in _readme_section()


def test_the_guide_documents_the_commands_the_sequences_and_the_scoring_difference() -> None:
    section = _guide_section()
    for fragment in ("--benchmark MOT20", "--params", "non_mot_vehicle", "--with-mot20", *SEQUENCES, *RUNS):
        assert fragment in section, fragment


def test_the_option_table_lists_the_new_options() -> None:
    assert re.search(r"^\| `--benchmark` \|", GUIDE, re.M)
    assert re.search(r"^\| `--params` \|", GUIDE, re.M)


@recorded
def test_the_guide_table_matches_the_recorded_scores() -> None:
    scores = _scores()
    section = _guide_section()
    for name in SEQUENCES:
        row = scores["per_sequence"][name]
        assert f"| {name} | {row['hota']:.2f} | {row['mota']:.2f} | {row['idf1']:.2f} |" in section
    combined = scores["combined"]
    assert f"| 合併 | {combined['hota']:.2f} | {combined['mota']:.2f} | {combined['idf1']:.2f} |" in section


@recorded
def test_the_two_recorded_runs_agree() -> None:
    other = ROOT / "data" / "results" / RUNS[1] / "summary.json"
    if not other.is_file():
        pytest.skip("the second MOT20 run is not present")
    assert json.loads(other.read_text())["scores"] == _scores()
