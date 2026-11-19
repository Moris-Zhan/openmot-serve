import json
import math
from pathlib import Path

import pytest
import torch

from eval.run import main
from tracking.run import DEFAULT_WEIGHTS

REPOSITORY = Path(__file__).resolve().parents[1]
DATA_ROOT = REPOSITORY / "data" / "MOT20"
SEQUENCE = "MOT20-01"
MOT17_PARAMS = REPOSITORY / "data" / "results" / "full-fp16-1" / "params.json"

pytestmark = [pytest.mark.gpu, pytest.mark.data]


def _require_environment() -> None:
    if not torch.cuda.is_available():
        pytest.skip("a CUDA GPU is required")
    if not Path(DEFAULT_WEIGHTS).is_file():
        pytest.skip(f"the weights {DEFAULT_WEIGHTS} are required")
    if not (DATA_ROOT / "train" / SEQUENCE / "img1").is_dir():
        pytest.skip(f"MOT20 not found under {DATA_ROOT}; run data/download_mot.sh --with-mot20")
    if not MOT17_PARAMS.is_file():
        pytest.skip(f"{MOT17_PARAMS} is required; run python -m eval.run --run-name full-fp16-1 on MOT17")


def test_a_real_mot20_run_reuses_the_mot17_parameters_and_scores_the_validation_half(tmp_path: Path) -> None:
    _require_environment()
    code = main(
        [
            "--benchmark", "MOT20",
            "--data-root", str(DATA_ROOT),
            "--sequences", SEQUENCE,
            "--params", str(MOT17_PARAMS),
            "--results-root", str(tmp_path),
            "--run-name", "mot20-check",
        ]
    )  # fmt: skip
    assert code == 0
    run_dir = tmp_path / "mot20-check"
    summary = json.loads((run_dir / "summary.json").read_text())
    print(f"\nMOT20 {SEQUENCE} validation half: {summary['scores']['combined']}")
    assert summary["benchmark"] == "MOT20"
    assert summary["tuning"] == {"skipped": True, "params_file": str(MOT17_PARAMS)}
    assert summary["tracker_params"] == json.loads(MOT17_PARAMS.read_text())
    assert set(summary["scores"]["per_sequence"]) == {SEQUENCE}
    assert all(math.isfinite(value) for value in summary["scores"]["combined"].values())
    assert not (run_dir / "tuning").exists()
