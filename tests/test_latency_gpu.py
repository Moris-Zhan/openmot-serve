import json
from pathlib import Path

import pytest

from eval.latency import main

REPOSITORY = Path(__file__).resolve().parents[1]
DATA_ROOT = REPOSITORY / "data" / "MOT17"
WARMUP, MEASURED = 3, 12


@pytest.mark.gpu
@pytest.mark.data
def test_the_real_detector_is_measured_on_the_gpu(tmp_path: Path) -> None:
    code = main(
        [
            "--data-root", str(DATA_ROOT),
            "--results-root", str(tmp_path),
            "--run-name", "gpu-check",
            "--sequences", "MOT17-09-FRCNN",
            "--warmup", str(WARMUP),
            "--measured", str(MEASURED),
        ]
    )
    assert code == 0
    result_dir = tmp_path / "latency" / "gpu-check"
    assert (result_dir / "latency.md").is_file()
    document = json.loads((result_dir / "latency.json").read_text())
    assert document["measured_frames"] == MEASURED and document["backend"] == "torch"
    results = document["results"]
    assert results["end_to_end"]["median_ms"] >= results["inference"]["median_ms"] > 0
    assert results["throughput_fps"] > 0
