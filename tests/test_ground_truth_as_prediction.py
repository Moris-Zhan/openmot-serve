from pathlib import Path

import pytest

from eval.halves import materialize_half
from eval.run import DEFAULT_SEQUENCES
from eval.scoring import ScoreReport, score_sequences
from eval.split import split_sequence
from tracking.motchallenge import ResultRow, format_result
from tracking.sequence import list_frames

DATA_TRAIN = Path(__file__).resolve().parents[1] / "data" / "MOT17" / "train"
PEDESTRIAN_CLASS = 1

pytestmark = pytest.mark.data


def _frame_count(name: str) -> int:
    return len(list_frames(DATA_TRAIN / name / "img1"))


def _gt_lines(path: Path) -> list[list[str]]:
    return [line.split(",") for line in path.read_text().splitlines() if line.strip()]


def _pedestrian_rows(gt_path: Path) -> list[ResultRow]:
    return [
        ResultRow(int(frame), int(identity), float(left), float(top), float(width), float(height), 1.0)
        for frame, identity, left, top, width, height, considered, category, _ in _gt_lines(gt_path)
        if int(considered) == 1 and int(category) == PEDESTRIAN_CLASS
    ]


@pytest.fixture(scope="module")
def validation_report(tmp_path_factory: pytest.TempPathFactory) -> ScoreReport:
    if not DATA_TRAIN.is_dir():
        pytest.skip("MOT17 is not downloaded; run data/download_mot.sh")
    root = tmp_path_factory.mktemp("gt_as_prediction")
    sequence_dirs: dict[str, Path] = {}
    result_files: dict[str, Path] = {}
    for name in DEFAULT_SEQUENCES:
        _, validation_half = split_sequence(_frame_count(name))
        destination = root / "halves" / name
        materialize_half(DATA_TRAIN / name, validation_half, destination)
        result = root / "results" / f"{name}.txt"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(format_result(_pedestrian_rows(destination / "gt" / "gt.txt")))
        sequence_dirs[name] = destination
        result_files[name] = result
    return score_sequences(sequence_dirs, result_files, root / "work")


@pytest.mark.parametrize("name", DEFAULT_SEQUENCES)
def test_each_validation_half_scores_100_when_the_ground_truth_is_the_prediction(
    validation_report: ScoreReport, name: str
) -> None:
    scores = validation_report.per_sequence[name]
    assert scores.hota == pytest.approx(100.0, abs=0.01)
    assert scores.mota == pytest.approx(100.0, abs=0.01)
    assert scores.idf1 == pytest.approx(100.0, abs=0.01)


def test_the_combined_score_is_100_too(validation_report: ScoreReport) -> None:
    combined = validation_report.combined
    assert combined.hota == pytest.approx(100.0, abs=0.01)
    assert combined.mota == pytest.approx(100.0, abs=0.01)
    assert combined.idf1 == pytest.approx(100.0, abs=0.01)


@pytest.mark.parametrize("name", DEFAULT_SEQUENCES)
def test_the_two_halves_together_hold_every_original_ground_truth_row(
    tmp_path: Path, name: str
) -> None:
    tuning_half, validation_half = split_sequence(_frame_count(name))
    row_counts = []
    for half in (tuning_half, validation_half):
        destination = tmp_path / type(half).__name__
        materialize_half(DATA_TRAIN / name, half, destination)
        lines = _gt_lines(destination / "gt" / "gt.txt")
        assert min(int(line[0]) for line in lines) >= 1
        assert max(int(line[0]) for line in lines) <= half.count
        row_counts.append(len(lines))
    assert sum(row_counts) == len(_gt_lines(DATA_TRAIN / name / "gt" / "gt.txt"))
