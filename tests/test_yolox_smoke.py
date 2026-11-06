from pathlib import Path

import pytest
import torch

from tracking.yolox import (
    WEIGHTS_SHA256,
    WeightsIntegrityError,
    build_yolox_x,
    grid_count,
    load_yolox_x,
    sha256_of_file,
)

WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "models" / "yolox_x.pth"
CLASS_COUNT = 80
CHANNELS = 4 + 1 + CLASS_COUNT


def test_grid_count_matches_three_strides() -> None:
    assert grid_count(800, 1440) == 100 * 180 + 50 * 90 + 25 * 45
    assert grid_count(64, 64) == 8 * 8 + 4 * 4 + 2 * 2


def test_untrained_model_output_shape_on_cpu() -> None:
    model = build_yolox_x().eval()
    with torch.no_grad():
        output = model(torch.zeros(1, 3, 64, 64))
    assert output.shape == (1, grid_count(64, 64), CHANNELS)


def test_batch_norm_uses_yolox_inference_settings() -> None:
    model = build_yolox_x()
    batch_norms = [m for m in model.modules() if isinstance(m, torch.nn.BatchNorm2d)]
    assert batch_norms
    assert all(m.eps == 1e-3 for m in batch_norms)


def test_sha256_of_file(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"abc")
    assert sha256_of_file(sample) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_load_rejects_file_with_wrong_hash(tmp_path: Path) -> None:
    tampered = tmp_path / "yolox_x.pth"
    tampered.write_bytes(b"not the real weights")
    with pytest.raises(WeightsIntegrityError) as caught:
        load_yolox_x(tampered, device="cpu")
    assert WEIGHTS_SHA256 in str(caught.value)


@pytest.mark.gpu
def test_fp16_forward_on_full_resolution_tensor() -> None:
    assert WEIGHTS_PATH.exists(), "run the weights download first (models/yolox_x.pth)"
    model = load_yolox_x(WEIGHTS_PATH, device="cuda")
    batch = torch.rand(1, 3, 800, 1440, device="cuda") * 255
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        output = model(batch)
    assert output.shape == (1, grid_count(800, 1440), CHANNELS)
    assert torch.isfinite(output.float()).all()
    objectness_and_class = output[..., 4:].float()
    assert objectness_and_class.min() >= 0.0 and objectness_and_class.max() <= 1.0
