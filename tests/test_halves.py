import configparser
import os
from pathlib import Path

import pytest

from eval.halves import HalfError, materialize_half
from eval.split import FrameRange, split_sequence

SEQUENCE_LENGTH = 7


def _write_sequence(root: Path, length: int = SEQUENCE_LENGTH) -> Path:
    sequence = root / "MOT17-99-FRCNN"
    (sequence / "img1").mkdir(parents=True)
    (sequence / "gt").mkdir()
    for number in range(1, length + 1):
        (sequence / "img1" / f"{number:06d}.jpg").write_bytes(f"frame-{number}".encode())
    rows = [f"{number},{identity},10,20,30,40,{flag},1,0.5" for number in range(1, length + 1)
            for identity, flag in ((1, 1), (2, 0))]
    (sequence / "gt" / "gt.txt").write_text("\n".join(rows) + "\n")
    (sequence / "seqinfo.ini").write_text(
        "[Sequence]\nname=MOT17-99-FRCNN\nimDir=img1\nframeRate=30\n"
        f"seqLength={length}\nimWidth=1920\nimHeight=1080\nimExt=.jpg\n"
    )
    return sequence


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    entries: dict[str, tuple[int, bytes | None]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        content = path.read_bytes() if path.is_file() else None
        entries[str(path.relative_to(root))] = (path.stat().st_mtime_ns, content)
    return entries


def _gt_rows(half_dir: Path) -> list[list[str]]:
    text = (half_dir / "gt" / "gt.txt").read_text()
    return [line.split(",") for line in text.splitlines()]


def test_frames_are_renumbered_from_one_and_link_to_the_right_originals(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    _, validation = split_sequence(SEQUENCE_LENGTH)
    destination = tmp_path / "halves" / "validation"
    materialize_half(sequence, validation, destination)
    names = sorted(path.name for path in (destination / "img1").iterdir())
    assert names == ["000001.jpg", "000002.jpg", "000003.jpg", "000004.jpg"]
    linked = [(destination / "img1" / name).read_bytes() for name in names]
    assert linked == [b"frame-4", b"frame-5", b"frame-6", b"frame-7"]
    assert all((destination / "img1" / name).is_symlink() for name in names)


def test_ground_truth_contains_only_rows_in_range_with_frames_starting_at_one(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    tuning, validation = split_sequence(SEQUENCE_LENGTH)
    materialize_half(sequence, tuning, tmp_path / "t")
    materialize_half(sequence, validation, tmp_path / "v")
    tuning_rows = _gt_rows(tmp_path / "t")
    validation_rows = _gt_rows(tmp_path / "v")
    assert sorted({int(row[0]) for row in tuning_rows}) == [1, 2, 3]
    assert sorted({int(row[0]) for row in validation_rows}) == [1, 2, 3, 4]
    assert len(tuning_rows) == 3 * 2
    assert len(validation_rows) == 4 * 2


def test_ground_truth_keeps_every_other_column_verbatim(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    _, validation = split_sequence(SEQUENCE_LENGTH)
    materialize_half(sequence, validation, tmp_path / "v")
    rows = _gt_rows(tmp_path / "v")
    assert rows[0] == ["1", "1", "10", "20", "30", "40", "1", "1", "0.5"]
    assert rows[1] == ["1", "2", "10", "20", "30", "40", "0", "1", "0.5"]


def test_seqinfo_reports_the_half_length_and_keeps_other_fields(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    tuning, _ = split_sequence(SEQUENCE_LENGTH)
    materialize_half(sequence, tuning, tmp_path / "t")
    parser = configparser.ConfigParser()
    parser.read(tmp_path / "t" / "seqinfo.ini")
    section = parser["Sequence"]
    assert section["seqLength"] == "3"
    assert section["imWidth"] == "1920"
    assert section["imDir"] == "img1"
    assert section["frameRate"] == "30"


def test_half_without_ground_truth_rows_gets_an_empty_file(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    rows = [line for line in (sequence / "gt" / "gt.txt").read_text().splitlines() if int(line.split(",")[0]) <= 3]
    (sequence / "gt" / "gt.txt").write_text("\n".join(rows) + "\n")
    _, validation = split_sequence(SEQUENCE_LENGTH)
    materialize_half(sequence, validation, tmp_path / "v")
    assert (tmp_path / "v" / "gt" / "gt.txt").read_text() == ""


def test_original_sequence_is_not_touched(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    before = _snapshot(sequence)
    tuning, validation = split_sequence(SEQUENCE_LENGTH)
    materialize_half(sequence, tuning, tmp_path / "out" / "t")
    materialize_half(sequence, validation, tmp_path / "out" / "v")
    assert _snapshot(sequence) == before


def test_existing_destination_is_refused(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    tuning, _ = split_sequence(SEQUENCE_LENGTH)
    destination = tmp_path / "t"
    destination.mkdir()
    with pytest.raises(HalfError, match="already exists"):
        materialize_half(sequence, tuning, destination)


def test_range_beyond_the_sequence_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    destination = tmp_path / "out" / "x"
    with pytest.raises(HalfError, match="outside"):
        materialize_half(sequence, FrameRange(5, SEQUENCE_LENGTH + 1), destination)
    assert not destination.exists()


def test_missing_frame_file_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    (sequence / "img1" / "000002.jpg").unlink()
    tuning, _ = split_sequence(SEQUENCE_LENGTH)
    destination = tmp_path / "t"
    with pytest.raises(HalfError, match="000002.jpg"):
        materialize_half(sequence, tuning, destination)
    assert not destination.exists()


def test_missing_ground_truth_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    (sequence / "gt" / "gt.txt").unlink()
    tuning, _ = split_sequence(SEQUENCE_LENGTH)
    destination = tmp_path / "t"
    with pytest.raises(HalfError, match="gt.txt"):
        materialize_half(sequence, tuning, destination)
    assert not destination.exists()


def test_seqinfo_length_must_match_the_frames_on_disk(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    (sequence / "img1" / "000007.jpg").unlink()
    tuning, _ = split_sequence(SEQUENCE_LENGTH)
    with pytest.raises(HalfError, match="seqLength"):
        materialize_half(sequence, tuning, tmp_path / "t")


def test_symlinks_are_absolute_so_the_half_can_be_moved(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path)
    tuning, _ = split_sequence(SEQUENCE_LENGTH)
    destination = tmp_path / "t"
    materialize_half(sequence, tuning, destination)
    target = os.readlink(destination / "img1" / "000001.jpg")
    assert Path(target).is_absolute()
