from pathlib import Path

import pytest

from llm_eval.results import Detection, ResultFormatError, read_results


def test_rows_are_read_as_integers_and_floats(tmp_path: Path) -> None:
    path = tmp_path / "result.txt"
    path.write_text("1,7,100.5,200.25,30.0,60.0,0.9,-1,-1,-1\n2,7,101.0,201.0,30.0,60.0,0.8,-1,-1,-1\n")
    assert read_results(path) == [
        Detection(1, 7, 100.5, 200.25, 30.0, 60.0),
        Detection(2, 7, 101.0, 201.0, 30.0, 60.0),
    ]


def test_an_empty_file_gives_no_detections(tmp_path: Path) -> None:
    path = tmp_path / "result.txt"
    path.write_text("")
    assert read_results(path) == []


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "result.txt"
    path.write_text("1,1,0,0,10,10,0.9,-1,-1,-1\n\n")
    assert len(read_results(path)) == 1


def test_a_row_with_too_few_fields_names_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "result.txt"
    path.write_text("1,1,0,0,10,10,0.9,-1,-1,-1\n2,1,0,0\n")
    with pytest.raises(ResultFormatError, match="line 2"):
        read_results(path)


def test_a_non_numeric_field_names_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "result.txt"
    path.write_text("x,1,0,0,10,10,0.9,-1,-1,-1\n")
    with pytest.raises(ResultFormatError, match="line 1"):
        read_results(path)
