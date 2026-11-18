import json
from pathlib import Path

import pytest
import torch

from eval.latency import main, read_gpu_memory_mib
from tracking.run import DEFAULT_WEIGHTS

REPOSITORY = Path(__file__).resolve().parents[1]
DATA_ROOT = REPOSITORY / "data" / "MOT17"
SEQUENCE = "MOT17-09-FRCNN"
WARMUP, MEASURED = 3, 12

pytestmark = [pytest.mark.gpu, pytest.mark.data]


def _require_environment() -> None:
    if not torch.cuda.is_available():
        pytest.skip("a CUDA GPU is required")
    if not Path(DEFAULT_WEIGHTS).is_file():
        pytest.skip(f"the weights {DEFAULT_WEIGHTS} are required")
    if not (DATA_ROOT / "train" / SEQUENCE / "img1").is_dir():
        pytest.skip(f"MOT17 frames not found under {DATA_ROOT}; run data/download_mot.sh")
    if read_gpu_memory_mib("cuda") is None:
        pytest.skip("nvidia-smi cannot report the GPU memory")


def test_the_real_probe_reads_a_positive_amount_of_used_memory() -> None:
    _require_environment()
    assert read_gpu_memory_mib("cuda") > 0


def test_a_real_measurement_records_the_memory_the_detector_takes(tmp_path: Path) -> None:
    _require_environment()
    code = main(
        [
            "--data-root", str(DATA_ROOT),
            "--results-root", str(tmp_path),
            "--run-name", "memory-check",
            "--sequences", SEQUENCE,
            "--warmup", str(WARMUP),
            "--measured", str(MEASURED),
        ]
    )  # fmt: skip
    assert code == 0
    document = json.loads((tmp_path / "latency" / "memory-check" / "latency.json").read_text())
    memory = document["gpu_memory"]
    print(
        f"\nGPU memory used: before load {memory['used_before_load_mib']} MiB, "
        f"load increase {memory['load_delta_mib']} MiB, run increase {memory['run_delta_mib']} MiB"
    )
    assert memory["available"] is True
    assert memory["load_delta_mib"] > 0
    assert memory["run_delta_mib"] >= 0
    assert memory["load_delta_mib"] == memory["used_after_load_mib"] - memory["used_before_load_mib"]
    assert memory["run_delta_mib"] == memory["used_after_run_mib"] - memory["used_before_load_mib"]
