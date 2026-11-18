import hashlib
from pathlib import Path
from typing import Any

import pytest

from inference.artifacts import engine_name, read_sidecar, sidecar_path
from inference.build_engine import (
    BuildDependencies,
    BuildError,
    BuildSettings,
    build_engine,
    build_with_tensorrt,
    main,
)
from inference.calibration import CalibrationError, CalibrationSelection
from tests.test_build_engine import (
    ENGINE_BYTES,
    GPU,
    TENSORRT,
    WEIGHTS_SHA256,
    _fake_tensorrt,
    _place_onnx,
)
from tracking.detector import DetectorSettings

CACHE_BYTES = b"calibration-scales"
SELECTION_SHA256 = "5" * 64
FRAME_COUNT = 4


class FakeCalibrator:
    def __init__(self, cache_path: Path, error: Exception | None) -> None:
        self.cache_path = cache_path
        self.error = error

    def write_calibration_cache(self, cache: bytes) -> None:
        self.cache_path.write_bytes(cache)


class Int8Build:
    def __init__(
        self,
        result: bytes | None = ENGINE_BYTES,
        build_error: Exception | None = None,
        calibration_error: Exception | None = None,
        write_cache: bool = True,
    ) -> None:
        self.result = result
        self.build_error = build_error
        self.calibration_error = calibration_error
        self.write_cache = write_cache
        self.calls: list[tuple[BuildSettings, Any]] = []
        self.created: list[FakeCalibrator] = []
        self.cache_present_at_creation: list[bool] = []
        self.selections: list[tuple[Path, int]] = []

    def select_frames(self, data_root: Path, count: int) -> CalibrationSelection:
        self.selections.append((data_root, count))
        return CalibrationSelection(tuple(Path(f"{index}.jpg") for index in range(count)), SELECTION_SHA256)

    def create_calibrator(self, selection: CalibrationSelection, cache_path: Path) -> FakeCalibrator:
        self.cache_present_at_creation.append(cache_path.exists())
        calibrator = FakeCalibrator(cache_path, None)
        self.created.append(calibrator)
        return calibrator

    def __call__(
        self, onnx: Path, settings: BuildSettings, timing_cache: Path, calibrator: Any = None
    ) -> bytes | None:
        self.calls.append((settings, calibrator))
        if calibrator is not None and self.write_cache:
            calibrator.write_calibration_cache(CACHE_BYTES)
        if calibrator is not None:
            calibrator.error = self.calibration_error
        if self.build_error is not None:
            raise self.build_error
        return self.result

    def dependencies(self, with_selection: bool = True) -> BuildDependencies:
        extra: dict[str, Any] = {"create_calibrator": self.create_calibrator}
        if with_selection:
            extra["select_frames"] = self.select_frames
        return BuildDependencies(
            verify_weights=lambda path: WEIGHTS_SHA256,
            tensorrt_version=TENSORRT,
            gpu_name=GPU,
            build=self,
            **extra,
        )


def int8_settings(data_root: Path) -> BuildSettings:
    return BuildSettings(precision="int8", calibration_frames=FRAME_COUNT, data_root=data_root)


def run_int8(build: Int8Build, directory: Path, with_selection: bool = True) -> Path:
    return build_engine(
        Path("weights.pth"),
        directory,
        DetectorSettings(backend="tensorrt", precision="int8"),
        int8_settings(directory / "data"),
        build.dependencies(with_selection),
    )


def int8_engine_path(directory: Path) -> Path:
    return directory / engine_name(WEIGHTS_SHA256, 800, 1440, TENSORRT, GPU, "int8")


def listing(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def test_the_calibration_settings_default_to_512_frames_of_the_mot17_data() -> None:
    settings = BuildSettings()
    assert settings.calibration_frames == 512
    assert settings.data_root.parts[-2:] == ("data", "MOT17")


def test_an_int8_build_writes_an_int8_named_engine_a_cache_and_a_calibration_record(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    build = Int8Build()
    engine = run_int8(build, tmp_path)
    assert engine == int8_engine_path(tmp_path) and engine.name.endswith("-int8.engine")
    assert engine.read_bytes() == ENGINE_BYTES
    cache = engine.with_name(engine.name + ".calib")
    assert cache.read_bytes() == CACHE_BYTES
    sidecar = read_sidecar(sidecar_path(engine))
    assert sidecar["precision"] == "int8"
    assert sidecar["builder_flags"] == ["INT8", "FP16"]
    assert sidecar["calibration"] == {
        "calibrator": "IInt8EntropyCalibrator2",
        "frames": FRAME_COUNT,
        "selection_sha256": SELECTION_SHA256,
        "cache": cache.name,
        "cache_sha256": hashlib.sha256(CACHE_BYTES).hexdigest(),
        "source": "MOT17 train FRCNN Tuning Half",
    }
    assert sidecar["weights_sha256"] == WEIGHTS_SHA256 and sidecar["tensorrt_version"] == TENSORRT


def test_the_frames_are_selected_from_the_configured_data_root_and_count(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    build = Int8Build()
    run_int8(build, tmp_path)
    assert build.selections == [(tmp_path / "data", FRAME_COUNT)]
    settings, calibrator = build.calls[0]
    assert settings.precision == "int8" and calibrator is build.created[0]


def test_a_stale_calibration_cache_is_removed_before_calibrating(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    int8_engine_path(tmp_path).with_name(int8_engine_path(tmp_path).name + ".calib").write_bytes(b"stale")
    build = Int8Build()
    engine = run_int8(build, tmp_path)
    assert build.cache_present_at_creation == [False]
    assert engine.with_name(engine.name + ".calib").read_bytes() == CACHE_BYTES


def test_an_fp16_build_is_unchanged_and_never_touches_calibration(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    build = Int8Build()
    engine = build_engine(
        Path("weights.pth"), tmp_path, DetectorSettings(backend="tensorrt"), BuildSettings(), build.dependencies()
    )
    assert engine.name.endswith("-fp16.engine")
    sidecar = read_sidecar(sidecar_path(engine))
    assert "calibration" not in sidecar and sidecar["builder_flags"] == ["FP16"]
    assert build.selections == [] and build.created == []
    assert build.calls == [(BuildSettings(), None)]
    assert not any(name.endswith(".calib") for name in listing(tmp_path))


def test_missing_calibration_data_fails_before_building_and_writes_nothing(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = listing(tmp_path)
    build = Int8Build()
    with pytest.raises(CalibrationError, match="MOT17-02-FRCNN"):
        run_int8(build, tmp_path, with_selection=False)
    assert build.calls == [] and listing(tmp_path) == before


def test_a_failed_int8_build_leaves_no_engine_sidecar_or_cache(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = listing(tmp_path)
    with pytest.raises(BuildError, match="failed"):
        run_int8(Int8Build(result=None), tmp_path)
    assert listing(tmp_path) == before


def test_an_exception_during_an_int8_build_leaves_no_partial_files(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = listing(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        run_int8(Int8Build(build_error=RuntimeError("boom")), tmp_path)
    assert listing(tmp_path) == before


def test_a_calibrator_error_fails_the_build_even_when_an_engine_was_produced(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = listing(tmp_path)
    with pytest.raises(CalibrationError, match="cannot read calibration frame"):
        run_int8(Int8Build(calibration_error=CalibrationError("cannot read calibration frame x.jpg")), tmp_path)
    assert listing(tmp_path) == before


def test_a_build_that_wrote_no_calibration_cache_fails_and_leaves_nothing(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = listing(tmp_path)
    with pytest.raises(CalibrationError, match="calibration cache"):
        run_int8(Int8Build(write_cache=False), tmp_path)
    assert listing(tmp_path) == before


def test_an_int8_build_without_a_calibrator_dependency_is_refused(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    dependencies = Int8Build().dependencies()
    bare = BuildDependencies(dependencies.verify_weights, TENSORRT, GPU, dependencies.build)
    with pytest.raises(BuildError, match="calibrator"):
        build_engine(
            Path("weights.pth"),
            tmp_path,
            DetectorSettings(backend="tensorrt", precision="int8"),
            int8_settings(tmp_path / "data"),
            bare,
        )


def test_the_real_builder_sets_the_int8_and_fp16_flags_and_attaches_the_calibrator(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    runtime.BuilderFlag.INT8 = "INT8-flag"
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"graph")
    calibrator = object()
    result = build_with_tensorrt(
        runtime, onnx, BuildSettings(precision="int8"), tmp_path / "m.engine.timing", calibrator
    )
    assert result == b"engine-blob"
    assert runtime.config.flags == ["INT8-flag", "FP16-flag"]
    assert runtime.config.int8_calibrator is calibrator


def test_the_real_builder_refuses_int8_without_a_calibrator(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    runtime.BuilderFlag.INT8 = "INT8-flag"
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"graph")
    with pytest.raises(BuildError, match="calibrator"):
        build_with_tensorrt(runtime, onnx, BuildSettings(precision="int8"), tmp_path / "m.engine.timing")
    assert runtime.config.flags == []


def test_the_real_fp16_builder_does_not_set_a_calibrator(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"graph")
    build_with_tensorrt(runtime, onnx, BuildSettings(), tmp_path / "m.engine.timing")
    assert runtime.config.flags == ["FP16-flag"] and "int8_calibrator" not in runtime.config.assigned


def test_the_command_line_builds_an_int8_engine_with_the_given_calibration_options(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    build = Int8Build()
    code = main(
        [
            "--precision", "int8",
            "--calibration-frames", str(FRAME_COUNT),
            "--data-root", str(tmp_path / "frames"),
            "--output-dir", str(tmp_path),
        ],
        dependencies=build.dependencies(),
    )
    assert code == 0 and int8_engine_path(tmp_path).is_file()
    assert build.selections == [(tmp_path / "frames", FRAME_COUNT)]


def test_the_command_line_defaults_to_fp16_and_rejects_other_precisions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _place_onnx(tmp_path)
    build = Int8Build()
    assert main(["--output-dir", str(tmp_path)], dependencies=build.dependencies()) == 0
    assert build.calls[0][0].precision == "fp16" and build.selections == []
    with pytest.raises(SystemExit):
        main(["--precision", "fp32", "--output-dir", str(tmp_path)], dependencies=build.dependencies())
    assert "fp32" in capsys.readouterr().err


def test_the_command_line_reports_missing_calibration_data_and_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _place_onnx(tmp_path)
    before = listing(tmp_path)
    build = Int8Build()
    code = main(
        ["--precision", "int8", "--data-root", str(tmp_path / "absent"), "--output-dir", str(tmp_path)],
        dependencies=build.dependencies(with_selection=False),
    )
    assert code != 0 and "MOT17-02-FRCNN" in capsys.readouterr().err
    assert listing(tmp_path) == before
