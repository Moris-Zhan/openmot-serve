import json
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from tracking.config import metadata_path_for
from tracking.motchallenge import parse_result
from tracking.run import main

REPOSITORY = Path(__file__).resolve().parents[1]
WEIGHTS_PATH = REPOSITORY / "models" / "yolox_x.pth"
SEQUENCE_09 = REPOSITORY / "data" / "MOT17" / "train" / "MOT17-09-FRCNN"


def white_box_frame(index: int) -> np.ndarray:
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[100:200, 100 + 3 * index : 140 + 3 * index] = 255
    return image


def detect_white_box(image: np.ndarray) -> np.ndarray:
    rows, columns = np.where(image[:, :, 0] > 200)
    if len(rows) == 0:
        return np.zeros((0, 5))
    return np.array([[columns.min(), rows.min(), columns.max() + 1, rows.max() + 1, 0.9]])


def fake_loader(
    weights: Path, precision: str, device: str, backend: str = "torch", engine: Path | None = None
) -> Callable[[np.ndarray], np.ndarray]:
    return detect_white_box


class RecordingLoader:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[Path, str, str, str, Path | None]] = []
        self.error = error

    def __call__(
        self, weights: Path, precision: str, device: str, backend: str = "torch", engine: Path | None = None
    ) -> Callable[[np.ndarray], np.ndarray]:
        self.calls.append((weights, precision, device, backend, engine))
        if self.error is not None:
            raise self.error
        return detect_white_box


def make_sequence(directory: Path, frame_count: int = 8) -> Path:
    directory.mkdir(parents=True)
    for index in range(frame_count):
        cv2.imwrite(str(directory / f"{index + 1:06d}.png"), white_box_frame(index))
    return directory


def run(arguments: list[str], loader: Callable = fake_loader) -> int:
    return main(arguments, load_detect_frame=loader)


def assert_nothing_written(output_dir: Path) -> None:
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_successful_run_exits_zero_and_writes_result_and_metadata(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output)]) == 0
    assert len(parse_result(output.read_text())) == 8
    assert json.loads(metadata_path_for(output).read_text())["precision"] == "fp16"


def test_config_file_values_are_used_and_recorded(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    config = tmp_path / "tracking.toml"
    config.write_text("[tracker]\ndetection_threshold = 0.95\ntrack_buffer = 12\n")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output), "--config", str(config)]) == 0
    assert output.read_text() == ""
    recorded = json.loads(metadata_path_for(output).read_text())["tracker"]
    assert recorded["detection_threshold"] == 0.95 and recorded["track_buffer"] == 12


def test_precision_option_is_recorded(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output), "--precision", "fp32", "--device", "cpu"]) == 0
    assert json.loads(metadata_path_for(output).read_text())["precision"] == "fp32"


def test_missing_folder_exits_nonzero_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(tmp_path / "absent"), "--output", str(output)]) != 0
    assert "not found" in capsys.readouterr().err
    assert_nothing_written(output.parent)


def test_empty_folder_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(tmp_path / "empty"), "--output", str(output)]) != 0
    assert_nothing_written(output.parent)


def test_unreadable_frame_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    (frames / "000003.png").write_bytes(b"broken")
    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output)]) != 0
    assert_nothing_written(output.parent)


def test_invalid_config_exits_nonzero_before_loading_the_detector(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    config = tmp_path / "tracking.toml"
    config.write_text("[tracker]\ndetection_treshold = 0.5\n")
    loaded: list[bool] = []

    def spying_loader(weights: Path, precision: str, device: str) -> Callable:
        loaded.append(True)
        return detect_white_box

    output = tmp_path / "out" / "SEQ.txt"
    assert run([str(frames), "--output", str(output), "--config", str(config)], spying_loader) != 0
    assert not loaded
    assert_nothing_written(output.parent)


def test_fp16_on_cpu_is_rejected_by_the_real_loader(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tracking.run import load_default_detect_frame

    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    arguments = [str(frames), "--output", str(output), "--device", "cpu", "--precision", "fp16"]
    assert run(arguments, load_default_detect_frame) != 0
    assert "fp16" in capsys.readouterr().err
    assert_nothing_written(output.parent)


def test_missing_weights_exit_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tracking.run import load_default_detect_frame

    frames = make_sequence(tmp_path / "seq")
    output = tmp_path / "out" / "SEQ.txt"
    arguments = [str(frames), "--output", str(output), "--device", "cpu", "--precision", "fp32",
                 "--weights", str(tmp_path / "no-such.pth")]
    assert run(arguments, load_default_detect_frame) != 0
    assert "no-such.pth" in capsys.readouterr().err
    assert_nothing_written(output.parent)


@pytest.mark.gpu
@pytest.mark.data
def test_real_detector_on_a_real_sequence_end_to_end(tmp_path: Path) -> None:
    from tracking.run import load_default_detect_frame

    frames = tmp_path / "frames"
    frames.mkdir()
    for number in range(1, 41):
        (frames / f"{number:06d}.jpg").symlink_to(SEQUENCE_09 / "img1" / f"{number:06d}.jpg")
    output = tmp_path / "out" / "MOT17-09.txt"
    assert run([str(frames), "--output", str(output)], load_default_detect_frame) == 0
    rows = parse_result(output.read_text())
    assert {row.frame for row in rows} == set(range(1, 41))
    assert len({row.identity for row in rows}) < 40


def test_the_loader_signature_gains_a_backend_and_an_engine_with_defaults() -> None:
    import inspect

    from tracking.run import load_default_detect_frame

    parameters = inspect.signature(load_default_detect_frame).parameters
    assert list(parameters)[:3] == ["weights", "precision", "device"]
    assert parameters["backend"].default == "torch" and parameters["engine"].default is None


def test_the_default_loader_still_builds_a_torch_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    import tracking.run as run_module
    from tracking.detector import DetectorSettings

    seen: list[tuple[Path, DetectorSettings, str]] = []

    def fake_load(weights: Path, settings: DetectorSettings, device: str) -> object:
        seen.append((weights, settings, device))
        return object()

    monkeypatch.setattr(run_module, "load_detector", fake_load)
    run_module.load_default_detect_frame(Path("w.pth"), "fp32", "cpu")
    assert seen == [(Path("w.pth"), DetectorSettings(precision="fp32"), "cpu")]


def test_an_exported_backend_resolves_the_artifact_and_runs_the_staged_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inference.backends as backends
    from inference.artifacts import ArtifactInfo
    from tracking.detector import DetectorSettings
    from tracking.run import load_default_detect_frame

    artifact = ArtifactInfo(Path("m.onnx"), {})
    resolved: list[tuple[str, DetectorSettings, Path, Path | None]] = []
    loaded: list[tuple[str, DetectorSettings, str, ArtifactInfo | None]] = []

    def fake_resolve(backend: str, settings: DetectorSettings, weights: Path, engine: Path | None) -> ArtifactInfo:
        resolved.append((backend, settings, weights, engine))
        return artifact

    def fake_load(backend: str, weights: Path, settings: DetectorSettings, device: str, info: ArtifactInfo | None):
        loaded.append((backend, settings, device, info))
        return "staged"

    monkeypatch.setattr(backends, "resolve_backend_artifact", fake_resolve)
    monkeypatch.setattr(backends, "load_staged_detector", fake_load)
    monkeypatch.setattr("tracking.detector.run_staged", lambda staged, image: np.zeros((0, 5)))
    detect_frame = load_default_detect_frame(Path("w.pth"), "fp32", "cuda", "onnx", Path("custom.onnx"))
    settings = DetectorSettings(backend="onnx", precision="fp32")
    assert resolved == [("onnx", settings, Path("w.pth"), Path("custom.onnx"))]
    assert loaded == [("onnx", settings, "cuda", artifact)]
    assert detect_frame(np.zeros((4, 4, 3), dtype=np.uint8)).shape == (0, 5)


def test_the_backend_defaults_to_torch_and_no_engine_is_passed(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    loader = RecordingLoader()
    assert run([str(frames), "--output", str(tmp_path / "o.txt")], loader) == 0
    assert [(call[3], call[4]) for call in loader.calls] == [("torch", None)]


def test_the_backend_and_engine_flags_reach_the_loader(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    loader = RecordingLoader()
    arguments = [str(frames), "--output", str(tmp_path / "o.txt"), "--backend", "tensorrt", "--engine", "e.engine"]
    assert run(arguments, loader) == 0
    assert [(call[1], call[3], call[4]) for call in loader.calls] == [("fp16", "tensorrt", Path("e.engine"))]


def test_onnx_needs_an_explicit_fp32_and_fails_before_loading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frames = make_sequence(tmp_path / "seq")
    loader = RecordingLoader()
    assert run([str(frames), "--output", str(tmp_path / "out" / "o.txt"), "--backend", "onnx"], loader) != 0
    error = capsys.readouterr().err
    assert "onnx" in error and "fp32" in error and "fp16" in error
    assert loader.calls == [] and assert_nothing_written(tmp_path / "out") is None


def test_onnx_with_explicit_fp32_is_accepted(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    loader = RecordingLoader()
    arguments = [str(frames), "--output", str(tmp_path / "o.txt"), "--backend", "onnx", "--precision", "fp32"]
    assert run(arguments, loader) == 0 and loader.calls[0][1:4:2] == ("fp32", "onnx")


def test_a_missing_or_mismatched_artifact_exits_non_zero_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from inference.artifacts import ArtifactError

    frames = make_sequence(tmp_path / "seq")
    loader = RecordingLoader(ArtifactError("missing m.engine; build it with: python -m inference.build_engine"))
    arguments = [str(frames), "--output", str(tmp_path / "out" / "o.txt"), "--backend", "tensorrt"]
    assert run(arguments, loader) != 0
    assert "inference.build_engine" in capsys.readouterr().err
    assert_nothing_written(tmp_path / "out")


def test_a_missing_backend_package_exits_non_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from inference.backends import BackendError

    frames = make_sequence(tmp_path / "seq")
    loader = RecordingLoader(BackendError("the tensorrt-cu12 package is not installed"))
    arguments = [str(frames), "--output", str(tmp_path / "o.txt"), "--backend", "tensorrt"]
    assert run(arguments, loader) != 0 and "tensorrt-cu12" in capsys.readouterr().err


def test_an_unknown_backend_is_rejected_by_the_parser(tmp_path: Path) -> None:
    frames = make_sequence(tmp_path / "seq")
    with pytest.raises(SystemExit) as raised:
        run([str(frames), "--output", str(tmp_path / "o.txt"), "--backend", "openvino"])
    assert raised.value.code == 2
