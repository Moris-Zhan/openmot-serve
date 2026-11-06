from pathlib import Path

import pytest

from eval.vendor.trackeval.datasets import MotChallenge2DBox
from tracking.motchallenge import (
    ResultFormatError,
    ResultRow,
    format_result,
    format_result_line,
    parse_result,
)

SAMPLE_ROWS = (
    ResultRow(frame=1, identity=1, left=100.0, top=50.5, width=40.0, height=80.25, confidence=0.9),
    ResultRow(frame=1, identity=2, left=300.0, top=60.0, width=35.0, height=90.0, confidence=0.5),
    ResultRow(frame=2, identity=1, left=102.0, top=51.0, width=40.0, height=80.0, confidence=0.88),
)


def test_line_has_ten_fields_with_placeholders() -> None:
    fields = format_result_line(SAMPLE_ROWS[0]).split(",")
    assert len(fields) == 10
    assert fields[:2] == ["1", "1"]
    assert fields[7:] == ["-1", "-1", "-1"]


def test_frame_and_identity_are_written_as_integers() -> None:
    fields = format_result_line(SAMPLE_ROWS[1]).split(",")
    assert fields[0] == "1" and fields[1] == "2"


def test_result_round_trips_through_text() -> None:
    assert parse_result(format_result(SAMPLE_ROWS)) == SAMPLE_ROWS


def test_format_is_byte_identical_for_identical_input() -> None:
    assert format_result(SAMPLE_ROWS) == format_result(tuple(SAMPLE_ROWS))


def test_empty_result_is_an_empty_file() -> None:
    assert format_result(()) == ""
    assert parse_result("") == ()


@pytest.mark.parametrize("frame", [0, -3])
def test_frame_must_start_at_one(frame: int) -> None:
    bad = ResultRow(frame, 1, 0.0, 0.0, 10.0, 10.0, 0.5)
    with pytest.raises(ResultFormatError, match="frame"):
        format_result((bad,))


@pytest.mark.parametrize("identity", [0, -1])
def test_identity_must_be_positive(identity: int) -> None:
    bad = ResultRow(1, identity, 0.0, 0.0, 10.0, 10.0, 0.5)
    with pytest.raises(ResultFormatError, match="identity"):
        format_result((bad,))


@pytest.mark.parametrize(
    "text",
    [
        "1,1,10,10,5,5,0.9,-1,-1\n",
        "1,1,10,10,5,5,0.9,-1,-1,-1,-1\n",
        "1,x,10,10,5,5,0.9,-1,-1,-1\n",
        "0,1,10,10,5,5,0.9,-1,-1,-1\n",
        "1.5,1,10,10,5,5,0.9,-1,-1,-1\n",
    ],
)
def test_parse_rejects_malformed_lines(text: str) -> None:
    with pytest.raises(ResultFormatError):
        parse_result(text)


def build_trackeval_folders(root: Path, result_text: str, frame_count: int) -> MotChallenge2DBox:
    sequence = "SYN-01"
    (root / "gt" / sequence / "gt").mkdir(parents=True)
    (root / "trackers" / "unit" / "data").mkdir(parents=True)
    (root / "gt" / sequence / "seqinfo.ini").write_text(
        f"[Sequence]\nname={sequence}\nimDir=img1\nframeRate=30\n"
        f"seqLength={frame_count}\nimWidth=1920\nimHeight=1080\nimExt=.jpg\n"
    )
    gt_lines = [f"{f},1,100,50,40,80,1,1,1" for f in range(1, frame_count + 1)]
    (root / "gt" / sequence / "gt" / "gt.txt").write_text("\n".join(gt_lines) + "\n")
    (root / "trackers" / "unit" / "data" / f"{sequence}.txt").write_text(result_text)
    (root / "seqmap.txt").write_text(f"name\n{sequence}\n")
    return MotChallenge2DBox(
        {
            "GT_FOLDER": str(root / "gt"),
            "TRACKERS_FOLDER": str(root / "trackers"),
            "OUTPUT_FOLDER": str(root / "out"),
            "SEQMAP_FILE": str(root / "seqmap.txt"),
            "TRACKERS_TO_EVAL": ["unit"],
            "BENCHMARK": "MOT17",
            "SPLIT_TO_EVAL": "train",
            "SKIP_SPLIT_FOL": True,
            "PRINT_CONFIG": False,
        }
    )


def test_trackeval_reads_every_line_we_write(tmp_path: Path) -> None:
    rows = tuple(
        ResultRow(frame, identity, 100.0 + frame, 50.0, 40.0, 80.0, 0.9)
        for frame in range(1, 6)
        for identity in (1, 2, 3)
    )
    dataset = build_trackeval_folders(tmp_path, format_result(rows), frame_count=5)
    raw = dataset.get_raw_seq_data("unit", "SYN-01")
    assert sum(len(ids) for ids in raw["tracker_ids"]) == len(rows)
    assert raw["num_timesteps"] == 5


def test_first_frame_is_frame_one_for_trackeval(tmp_path: Path) -> None:
    rows = (ResultRow(1, 1, 100.0, 50.0, 40.0, 80.0, 0.9),)
    dataset = build_trackeval_folders(tmp_path, format_result(rows), frame_count=3)
    raw = dataset.get_raw_seq_data("unit", "SYN-01")
    assert len(raw["tracker_ids"][0]) == 1
    assert len(raw["tracker_ids"][1]) == 0
