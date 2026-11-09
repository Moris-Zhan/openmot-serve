import configparser
import contextlib
import io
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from eval.vendor.trackeval import Evaluator
from eval.vendor.trackeval.datasets import MotChallenge2DBox
from eval.vendor.trackeval.metrics import CLEAR, HOTA, Identity
from tracking.files import write_text_atomic
from tracking.motchallenge import ResultFormatError, format_result, parse_result

TRACKER_NAME = "result"


class ScoringError(RuntimeError):
    pass


@dataclass(frozen=True)
class Scores:
    hota: float
    mota: float
    idf1: float


@dataclass(frozen=True)
class ScoreReport:
    per_sequence: Mapping[str, Scores]
    combined: Scores


def _sequence_length(sequence_dir: Path) -> int:
    seqinfo = sequence_dir / "seqinfo.ini"
    if not seqinfo.is_file():
        raise ScoringError(f"seqinfo.ini not found: {seqinfo}")
    parser = configparser.ConfigParser()
    parser.read(seqinfo)
    try:
        return int(parser["Sequence"]["seqLength"])
    except (KeyError, ValueError) as error:
        raise ScoringError(f"{seqinfo} has no valid [Sequence] seqLength") from error


def _frames_inside_sequence(name: str, result_file: Path, length: int) -> str:
    try:
        rows = parse_result(result_file.read_text())
    except (OSError, ResultFormatError) as error:
        raise ScoringError(f"cannot use the result for {name}: {error}") from error
    return format_result([row for row in rows if row.frame <= length])


def _stage(
    sequence_dirs: Mapping[str, Path], result_files: Mapping[str, Path], workspace: Path
) -> None:
    if workspace.exists() and any(workspace.iterdir()):
        raise ScoringError(f"scoring workspace is not empty: {workspace}")
    gt_root = workspace / "gt"
    data_dir = workspace / "trackers" / TRACKER_NAME / "data"
    gt_root.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    for name, sequence_dir in sequence_dirs.items():
        if name not in result_files:
            raise ScoringError(f"no result file given for sequence {name}")
        if not (sequence_dir / "gt" / "gt.txt").is_file():
            raise ScoringError(f"gt.txt not found for sequence {name}: {sequence_dir / 'gt'}")
        length = _sequence_length(sequence_dir)
        os.symlink(sequence_dir.resolve(), gt_root / name)
        write_text_atomic(
            data_dir / f"{name}.txt", _frames_inside_sequence(name, result_files[name], length)
        )
    write_text_atomic(workspace / "seqmap.txt", "name\n" + "".join(f"{name}\n" for name in sequence_dirs))


def _scores_from(metrics: Mapping[str, Mapping[str, object]]) -> Scores:
    return Scores(
        hota=float(np.mean(metrics["HOTA"]["HOTA"])) * 100.0,
        mota=float(metrics["CLEAR"]["MOTA"]) * 100.0,
        idf1=float(metrics["Identity"]["IDF1"]) * 100.0,
    )


def score_sequences(
    sequence_dirs: Mapping[str, Path], result_files: Mapping[str, Path], workspace: Path
) -> ScoreReport:
    if not sequence_dirs:
        raise ScoringError("no sequences to score")
    _stage(sequence_dirs, result_files, workspace)
    dataset = MotChallenge2DBox(
        {
            "GT_FOLDER": str(workspace / "gt"),
            "TRACKERS_FOLDER": str(workspace / "trackers"),
            "OUTPUT_FOLDER": str(workspace / "out"),
            "SEQMAP_FILE": str(workspace / "seqmap.txt"),
            "TRACKERS_TO_EVAL": [TRACKER_NAME],
            "BENCHMARK": "MOT17",
            "SPLIT_TO_EVAL": "train",
            "SKIP_SPLIT_FOL": True,
            "PRINT_CONFIG": False,
        }
    )
    evaluator = Evaluator(
        {
            "USE_PARALLEL": False,
            "LOG_ON_ERROR": None,
            "PRINT_RESULTS": False,
            "PRINT_CONFIG": False,
            "TIME_PROGRESS": False,
            "OUTPUT_SUMMARY": False,
            "OUTPUT_DETAILED": False,
            "PLOT_CURVES": False,
        }
    )
    metrics = [HOTA({"PRINT_CONFIG": False}), CLEAR({"PRINT_CONFIG": False}), Identity({"PRINT_CONFIG": False})]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            results, messages = evaluator.evaluate([dataset], metrics)
    except Exception as error:
        raise ScoringError(f"TrackEval failed: {error}") from error
    status = messages["MotChallenge2DBox"][TRACKER_NAME]
    if status != "Success":
        raise ScoringError(f"TrackEval failed: {status}")
    tracker_results = results["MotChallenge2DBox"][TRACKER_NAME]
    return ScoreReport(
        per_sequence={name: _scores_from(tracker_results[name]["pedestrian"]) for name in sequence_dirs},
        combined=_scores_from(tracker_results["COMBINED_SEQ"]["pedestrian"]),
    )
