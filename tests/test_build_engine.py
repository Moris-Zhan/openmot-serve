import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference.artifacts import (
    EXPORT_COMMAND,
    ArtifactError,
    engine_name,
    onnx_name,
    read_sidecar,
    sidecar_path,
    write_sidecar,
)
from inference.backends import BackendError
from inference.build_engine import (
    BuildDependencies,
    BuildError,
    BuildSettings,
    build_engine,
    build_with_tensorrt,
    main,
)
from tracking.detector import DetectorSettings

WEIGHTS_SHA256 = "ab12cd34" + "0" * 56
ONNX_BYTES = b"fake-onnx-graph"
ONNX_SHA256 = hashlib.sha256(ONNX_BYTES).hexdigest()
GPU = "NVIDIA GeForce RTX 5090"
TENSORRT = "10.13.3.9"
ENGINE_BYTES = b"fake-engine"


def _place_onnx(directory: Path, content: bytes = ONNX_BYTES) -> Path:
    path = directory / onnx_name(WEIGHTS_SHA256, 800, 1440)
    path.write_bytes(content)
    write_sidecar(
        sidecar_path(path),
        {"weights_sha256": WEIGHTS_SHA256, "onnx_sha256": ONNX_SHA256, "input_height": 800, "input_width": 1440},
    )
    return path


def _engine_path(directory: Path) -> Path:
    return directory / engine_name(WEIGHTS_SHA256, 800, 1440, TENSORRT, GPU, "fp16")


class FakeBuild:
    def __init__(self, result: bytes | None = ENGINE_BYTES, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[Path, BuildSettings, Path]] = []

    def __call__(self, onnx: Path, settings: BuildSettings, timing_cache: Path) -> bytes | None:
        self.calls.append((onnx, settings, timing_cache))
        if self.error is not None:
            raise self.error
        return self.result

    def dependencies(self) -> BuildDependencies:
        return BuildDependencies(
            verify_weights=lambda path: WEIGHTS_SHA256,
            tensorrt_version=TENSORRT,
            gpu_name=GPU,
            build=self,
        )


def _run(build: FakeBuild, directory: Path) -> Path:
    return build_engine(
        Path("weights.pth"), directory, DetectorSettings(backend="tensorrt"), BuildSettings(), build.dependencies()
    )


def test_a_build_writes_the_engine_and_a_sidecar_with_every_build_parameter(tmp_path: Path) -> None:
    onnx = _place_onnx(tmp_path)
    build = FakeBuild()
    engine = _run(build, tmp_path)
    assert engine == _engine_path(tmp_path) and engine.read_bytes() == ENGINE_BYTES
    assert read_sidecar(sidecar_path(engine)) == {
        "weights_sha256": WEIGHTS_SHA256,
        "onnx_sha256": ONNX_SHA256,
        "input_height": 800,
        "input_width": 1440,
        "precision": "fp16",
        "workspace_bytes": 8 * 2**30,
        "optimization_level": "default",
        "builder_flags": ["FP16"],
        "tensorrt_version": TENSORRT,
        "gpu_name": GPU,
        "timing_cache": engine.name + ".timing",
    }
    assert build.calls == [(onnx, BuildSettings(), engine.with_name(engine.name + ".timing"))]


def test_the_defaults_are_fp16_8_gib_workspace_and_the_default_optimization_level() -> None:
    settings = BuildSettings()
    assert (settings.precision, settings.workspace_bytes, settings.optimization_level) == (
        "fp16",
        8 * 2**30,
        "default",
    )


def test_a_missing_onnx_points_at_the_export_command_and_builds_nothing(tmp_path: Path) -> None:
    build = FakeBuild()
    with pytest.raises(ArtifactError) as raised:
        _run(build, tmp_path)
    assert EXPORT_COMMAND in str(raised.value) and onnx_name(WEIGHTS_SHA256, 800, 1440) in str(raised.value)
    assert build.calls == []


def test_a_tampered_onnx_is_refused_before_building(tmp_path: Path) -> None:
    _place_onnx(tmp_path, content=b"tampered")
    build = FakeBuild()
    with pytest.raises(ArtifactError, match="onnx_sha256 mismatch"):
        _run(build, tmp_path)
    assert build.calls == []


def test_a_failed_build_leaves_no_engine_and_no_sidecar(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = sorted(entry.name for entry in tmp_path.iterdir())
    with pytest.raises(BuildError, match="failed"):
        _run(FakeBuild(result=None), tmp_path)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == before


def test_an_exception_during_the_build_leaves_no_partial_files(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    before = sorted(entry.name for entry in tmp_path.iterdir())
    with pytest.raises(RuntimeError, match="boom"):
        _run(FakeBuild(error=RuntimeError("boom")), tmp_path)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == before


def test_a_rebuild_replaces_the_previous_engine(tmp_path: Path) -> None:
    _place_onnx(tmp_path)
    _run(FakeBuild(result=b"first"), tmp_path)
    _run(FakeBuild(result=b"second"), tmp_path)
    assert _engine_path(tmp_path).read_bytes() == b"second"


def test_main_builds_and_reports_the_engine(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _place_onnx(tmp_path)
    code = main(["--weights", "w.pth", "--output-dir", str(tmp_path)], FakeBuild().dependencies())
    assert code == 0 and _engine_path(tmp_path).is_file()
    assert _engine_path(tmp_path).name in capsys.readouterr().out


def test_main_exits_non_zero_when_the_onnx_is_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--weights", "w.pth", "--output-dir", str(tmp_path)], FakeBuild().dependencies())
    assert code != 0 and EXPORT_COMMAND in capsys.readouterr().err


def test_main_exits_non_zero_when_the_build_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _place_onnx(tmp_path)
    code = main(["--weights", "w.pth", "--output-dir", str(tmp_path)], FakeBuild(result=None).dependencies())
    assert code != 0 and "failed" in capsys.readouterr().err
    assert not _engine_path(tmp_path).exists()


def test_main_without_tensorrt_names_the_missing_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> BuildDependencies:
        raise BackendError("the tensorrt-cu12 package is not installed")

    code = main(["--weights", "w.pth", "--output-dir", str(tmp_path)], None, missing)
    assert code != 0 and "tensorrt-cu12" in capsys.readouterr().err


class FakeCache:
    def serialize(self) -> bytes:
        return b"serialized-timing-cache"


class FakeConfig:
    def __init__(self) -> None:
        object.__setattr__(self, "flags", [])
        object.__setattr__(self, "pool_limits", [])
        object.__setattr__(self, "cache_inputs", [])
        object.__setattr__(self, "timing_cache_set", [])
        object.__setattr__(self, "assigned", [])

    def __setattr__(self, name: str, value: object) -> None:
        self.assigned.append(name)
        object.__setattr__(self, name, value)

    def set_flag(self, flag: object) -> None:
        self.flags.append(flag)

    def set_memory_pool_limit(self, pool: object, size: int) -> None:
        self.pool_limits.append((pool, size))

    def create_timing_cache(self, data: bytes) -> FakeCache:
        self.cache_inputs.append(data)
        return FakeCache()

    def set_timing_cache(self, cache: FakeCache, ignore_mismatch: bool) -> bool:
        self.timing_cache_set.append((cache, ignore_mismatch))
        return True


def _fake_tensorrt(parse_ok: bool = True, serialized: bytes | None = b"engine-blob") -> SimpleNamespace:
    config = FakeConfig()
    parsed: list[bytes] = []

    class Logger:
        WARNING = "warning"

        def __init__(self, severity: object) -> None:
            self.severity = severity

    class Parser:
        num_errors = 1

        def __init__(self, network: object, logger: object) -> None:
            pass

        def parse(self, data: bytes) -> bool:
            parsed.append(data)
            return parse_ok

        def get_error(self, index: int) -> str:
            return "unsupported node"

    class Builder:
        def __init__(self, logger: object) -> None:
            pass

        def create_network(self, flags: int) -> object:
            return object()

        def create_builder_config(self) -> FakeConfig:
            return config

        def build_serialized_network(self, network: object, builder_config: FakeConfig) -> bytes | None:
            return serialized

    return SimpleNamespace(
        config=config,
        parsed=parsed,
        Logger=Logger,
        Builder=Builder,
        OnnxParser=Parser,
        BuilderFlag=SimpleNamespace(FP16="FP16-flag"),
        MemoryPoolType=SimpleNamespace(WORKSPACE="workspace-pool"),
    )


def _build(tmp_path: Path, runtime: SimpleNamespace) -> bytes | None:
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(ONNX_BYTES)
    return build_with_tensorrt(runtime, onnx, BuildSettings(), tmp_path / "m.engine.timing")


def test_the_real_builder_parses_the_onnx_and_returns_the_serialized_engine(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    assert _build(tmp_path, runtime) == b"engine-blob"
    assert runtime.parsed == [ONNX_BYTES]


def test_the_real_builder_sets_only_the_fp16_flag_and_the_8_gib_workspace(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    _build(tmp_path, runtime)
    assert runtime.config.flags == ["FP16-flag"]
    assert runtime.config.pool_limits == [("workspace-pool", 8 * 2**30)]


def test_the_real_builder_leaves_the_optimization_level_at_its_default(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    _build(tmp_path, runtime)
    assert "builder_optimization_level" not in runtime.config.assigned


def test_the_real_builder_starts_from_an_empty_timing_cache_and_writes_it_back(tmp_path: Path) -> None:
    runtime = _fake_tensorrt()
    _build(tmp_path, runtime)
    assert runtime.config.cache_inputs == [b""]
    assert (tmp_path / "m.engine.timing").read_bytes() == b"serialized-timing-cache"


def test_the_real_builder_reads_an_existing_timing_cache(tmp_path: Path) -> None:
    (tmp_path / "m.engine.timing").write_bytes(b"previous-cache")
    runtime = _fake_tensorrt()
    _build(tmp_path, runtime)
    assert runtime.config.cache_inputs == [b"previous-cache"]
    assert runtime.config.timing_cache_set[0][1] is False


def test_the_real_builder_reports_parser_errors(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="unsupported node"):
        _build(tmp_path, _fake_tensorrt(parse_ok=False))


def test_the_real_builder_returns_none_and_keeps_no_timing_cache_when_the_build_fails(tmp_path: Path) -> None:
    assert _build(tmp_path, _fake_tensorrt(serialized=None)) is None
    assert not (tmp_path / "m.engine.timing").exists()
