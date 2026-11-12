from pathlib import Path

import pytest

import eval.vendor.trackeval as trackeval
from eval.scoring import ScoreReport, Scores, ScoringError, score_sequences
from tracking.motchallenge import ResultRow, format_result

FRAME_COUNT = 20
PEDESTRIANS = ((1, 0.0), (2, 400.0))


def _box(frame: int, offset: float) -> tuple[float, float, float, float]:
    return (100.0 + 3 * frame + offset, 50.0 + offset, 40.0, 80.0)


def _write_sequence(
    root: Path, name: str, distractor: bool = False, frame_count: int = FRAME_COUNT
) -> Path:
    sequence = root / name
    (sequence / "gt").mkdir(parents=True)
    (sequence / "seqinfo.ini").write_text(
        f"[Sequence]\nname={name}\nimDir=img1\nframeRate=30\n"
        f"seqLength={frame_count}\nimWidth=1920\nimHeight=1080\nimExt=.jpg\n"
    )
    lines = []
    for frame in range(1, frame_count + 1):
        for identity, offset in PEDESTRIANS:
            left, top, width, height = _box(frame, offset)
            lines.append(f"{frame},{identity},{left},{top},{width},{height},1,1,1")
        if distractor:
            left, top, width, height = _box(frame, 900.0)
            lines.append(f"{frame},3,{left},{top},{width},{height},0,8,1")
    (sequence / "gt" / "gt.txt").write_text("\n".join(lines) + "\n")
    return sequence


def _rows(
    frames: range, identities: tuple[tuple[int, float], ...] = PEDESTRIANS
) -> list[ResultRow]:
    rows = []
    for frame in frames:
        for identity, offset in identities:
            left, top, width, height = _box(frame, offset)
            rows.append(ResultRow(frame, identity, left, top, width, height, 1.0))
    return rows


def _write_result(directory: Path, name: str, rows: list[ResultRow]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.txt"
    path.write_text(format_result(rows))
    return path


def _score(case_dir: Path, rows: list[ResultRow], distractor: bool = False) -> Scores:
    sequence = _write_sequence(case_dir / "halves", "SYN-01", distractor)
    result = _write_result(case_dir / "results", "SYN-01", rows)
    report = score_sequences({"SYN-01": sequence}, {"SYN-01": result}, case_dir / "work")
    return report.combined


def test_ground_truth_used_as_prediction_scores_close_to_100(tmp_path: Path) -> None:
    scores = _score(tmp_path, _rows(range(1, FRAME_COUNT + 1)))
    assert scores.hota == pytest.approx(100.0, abs=0.01)
    assert scores.mota == pytest.approx(100.0, abs=0.01)
    assert scores.idf1 == pytest.approx(100.0, abs=0.01)


def test_missing_pedestrians_lower_the_scores(tmp_path: Path) -> None:
    scores = _score(tmp_path, _rows(range(1, FRAME_COUNT + 1), ((1, 0.0),)))
    assert scores.mota == pytest.approx(50.0, abs=0.01)
    assert scores.hota < 90.0


def test_unpredicted_ignored_ground_truth_is_not_a_miss(tmp_path: Path) -> None:
    scores = _score(tmp_path, _rows(range(1, FRAME_COUNT + 1)), distractor=True)
    assert scores.mota == pytest.approx(100.0, abs=0.01)
    assert scores.idf1 == pytest.approx(100.0, abs=0.01)


def test_predicting_ignored_ground_truth_is_not_a_false_positive(tmp_path: Path) -> None:
    rows = _rows(range(1, FRAME_COUNT + 1), (*PEDESTRIANS, (3, 900.0)))
    scores = _score(tmp_path, rows, distractor=True)
    assert scores.mota == pytest.approx(100.0, abs=0.01)
    assert scores.idf1 == pytest.approx(100.0, abs=0.01)


def test_a_prediction_where_nothing_exists_is_a_false_positive(tmp_path: Path) -> None:
    rows = _rows(range(1, FRAME_COUNT + 1), (*PEDESTRIANS, (3, 1500.0)))
    scores = _score(tmp_path, rows, distractor=True)
    assert scores.mota < 100.0


def test_frames_beyond_the_sequence_do_not_change_the_scores(tmp_path: Path) -> None:
    inside = _rows(range(1, FRAME_COUNT + 1))
    outside = _rows(range(FRAME_COUNT + 1, FRAME_COUNT + 8), ((7, 0.0), (8, 123.0)))
    baseline = _score(tmp_path / "a", inside)
    padded = _score(tmp_path / "b", inside + outside)
    assert padded == baseline


def test_report_has_each_sequence_and_a_combined_score(tmp_path: Path) -> None:
    halves = tmp_path / "halves"
    results = tmp_path / "results"
    perfect = _write_sequence(halves, "SYN-01")
    empty = _write_sequence(halves, "SYN-02")
    report = score_sequences(
        {"SYN-01": perfect, "SYN-02": empty},
        {
            "SYN-01": _write_result(results, "SYN-01", _rows(range(1, FRAME_COUNT + 1))),
            "SYN-02": _write_result(results, "SYN-02", []),
        },
        tmp_path / "work",
    )
    assert isinstance(report, ScoreReport)
    assert set(report.per_sequence) == {"SYN-01", "SYN-02"}
    assert report.per_sequence["SYN-01"].mota == pytest.approx(100.0, abs=0.01)
    assert report.per_sequence["SYN-02"].mota == pytest.approx(0.0, abs=0.01)
    assert 0.0 < report.combined.mota < 100.0


def test_a_sequence_without_a_result_is_refused(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path / "halves", "SYN-01")
    with pytest.raises(ScoringError, match="SYN-01"):
        score_sequences({"SYN-01": sequence}, {}, tmp_path / "work")


def test_a_malformed_result_file_is_refused(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path / "halves", "SYN-01")
    bad = tmp_path / "bad.txt"
    bad.write_text("1,1,10,10,5,5,0.9\n")
    with pytest.raises(ScoringError, match="SYN-01"):
        score_sequences({"SYN-01": sequence}, {"SYN-01": bad}, tmp_path / "work")


def test_a_missing_ground_truth_is_refused(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path / "halves", "SYN-01")
    (sequence / "gt" / "gt.txt").unlink()
    result = _write_result(tmp_path / "results", "SYN-01", _rows(range(1, FRAME_COUNT + 1)))
    with pytest.raises(ScoringError, match="gt.txt"):
        score_sequences({"SYN-01": sequence}, {"SYN-01": result}, tmp_path / "work")


def test_inputs_are_not_modified(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path / "halves", "SYN-01")
    result = _write_result(tmp_path / "results", "SYN-01", _rows(range(1, FRAME_COUNT + 3)))
    before = (result.read_bytes(), (sequence / "gt" / "gt.txt").read_bytes())
    score_sequences({"SYN-01": sequence}, {"SYN-01": result}, tmp_path / "work")
    assert (result.read_bytes(), (sequence / "gt" / "gt.txt").read_bytes()) == before


def test_a_scoring_failure_leaves_no_log_file_in_the_vendored_directory(tmp_path: Path) -> None:
    sequence = _write_sequence(tmp_path / "halves", "SYN-01")
    seqinfo = sequence / "seqinfo.ini"
    seqinfo.write_text(seqinfo.read_text().replace(f"seqLength={FRAME_COUNT}", "seqLength=10"))
    result = _write_result(tmp_path / "results", "SYN-01", _rows(range(1, 11)))
    with pytest.raises(ScoringError):
        score_sequences({"SYN-01": sequence}, {"SYN-01": result}, tmp_path / "work")
    assert not (Path(trackeval.__file__).parent.parent / "error_log.txt").exists()
