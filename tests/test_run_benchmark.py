import json
from dataclasses import asdict
from pathlib import Path

import pytest

import eval.run
import eval.tuning
from eval.detection_cache import CacheKey, detect_with_cache
from eval.run import PROJECT_ROOT, BenchmarkConfig, benchmark_defaults, main
from eval.tuning import read_params, write_params
from tests.test_run import LENGTH, SEQUENCES, Harness
from tracking.bytetrack import TrackerParams, run_tracker
from tracking.detector import DetectorSettings
from tracking.motchallenge import format_result
from tracking.yolox import WEIGHTS_SHA256

MOT17_SEQUENCES = tuple(f"MOT17-{number}-FRCNN" for number in ("02", "04", "05", "09", "10", "11", "13"))
MOT20_SEQUENCES = ("MOT20-01", "MOT20-02", "MOT20-03", "MOT20-05")


def test_mot17_defaults_are_the_existing_root_and_the_seven_frcnn_sequences() -> None:
    assert benchmark_defaults("MOT17") == (PROJECT_ROOT / "data" / "MOT17", MOT17_SEQUENCES)


def test_mot20_defaults_are_the_mot20_root_and_its_labelled_training_sequences() -> None:
    assert benchmark_defaults("MOT20") == (PROJECT_ROOT / "data" / "MOT20", MOT20_SEQUENCES)


def test_an_unknown_benchmark_is_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="MOT16"):
        benchmark_defaults("MOT16")


def test_the_config_defaults_to_mot17(tmp_path: Path) -> None:
    config = BenchmarkConfig(tmp_path, tmp_path, "run", "fp16", tmp_path / "w.pth", "cpu")
    assert config.benchmark == "MOT17"


def _captured_config(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> BenchmarkConfig:
    captured: list[BenchmarkConfig] = []
    monkeypatch.setattr(eval.run, "execute", lambda config, dependencies: captured.append(config) or "")
    assert main(["--device", "cpu", *argv]) == 0
    return captured[0]


def test_the_command_defaults_to_mot17(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _captured_config(monkeypatch, [])
    assert (config.benchmark, config.data_root, config.sequences) == (
        "MOT17",
        PROJECT_ROOT / "data" / "MOT17",
        MOT17_SEQUENCES,
    )


def test_selecting_mot20_changes_the_default_root_and_sequences(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _captured_config(monkeypatch, ["--benchmark", "MOT20"])
    assert (config.benchmark, config.data_root, config.sequences) == (
        "MOT20",
        PROJECT_ROOT / "data" / "MOT20",
        MOT20_SEQUENCES,
    )


def test_an_explicit_root_and_sequences_win_over_the_benchmark_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _captured_config(
        monkeypatch, ["--benchmark", "MOT20", "--data-root", str(tmp_path), "--sequences", "MOT20-02"]
    )
    assert (config.benchmark, config.data_root, config.sequences) == ("MOT20", tmp_path, ("MOT20-02",))


def test_missing_mot20_data_names_the_benchmark_the_sequences_and_the_download_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path)
    harness.data_root = tmp_path / "nowhere"
    assert harness.run("first", ("--benchmark", "MOT20")) != 0
    error = capsys.readouterr().err
    assert "MOT20 sequences not found" in error
    assert all(name in error for name in SEQUENCES)
    assert "data/download_mot.sh --with-mot20" in error
    assert not harness.results_root.exists()


def test_missing_mot17_data_keeps_the_existing_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    harness = Harness(tmp_path)
    harness.data_root = tmp_path / "nowhere"
    assert harness.run("first") != 0
    error = capsys.readouterr().err
    assert "MOT17 sequences not found" in error
    assert "Run data/download_mot.sh to download them." in error
    assert "--with-mot20" not in error


def _spy_on_scoring(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def spy(real):
        def wrapper(sequence_dirs, result_files, workspace, benchmark="MOT17"):
            seen.append(benchmark)
            return real(sequence_dirs, result_files, workspace, benchmark)

        return wrapper

    monkeypatch.setattr(eval.run, "score_sequences", spy(eval.run.score_sequences))
    monkeypatch.setattr(eval.tuning, "score_sequences", spy(eval.tuning.score_sequences))
    return seen


@pytest.mark.parametrize("benchmark", ["MOT17", "MOT20"])
def test_the_selected_benchmark_reaches_both_tuning_and_validation_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark: str
) -> None:
    seen = _spy_on_scoring(monkeypatch)
    harness = Harness(tmp_path)
    assert harness.run("first", ("--benchmark", benchmark)) == 0
    assert seen and set(seen) == {benchmark}


def test_the_summary_records_the_benchmark(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    assert harness.run("first", ("--benchmark", "MOT20")) == 0
    summary = json.loads((harness.results_root / "first" / "summary.json").read_text())
    assert summary["benchmark"] == "MOT20"


REUSED = TrackerParams(detection_threshold=0.55, match_threshold=0.85, track_buffer=20)
VALIDATION_FRAMES = LENGTH - LENGTH // 2


def _params_file(tmp_path: Path, params: TrackerParams = REUSED) -> Path:
    path = tmp_path / "reused.json"
    write_params(path, params)
    return path


def _refuse_to_tune(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tuning must be skipped when a parameter file is given")

    monkeypatch.setattr(eval.run, "tune_parameters", refuse)


def test_a_parameter_file_is_used_instead_of_tuning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_to_tune(monkeypatch)
    harness = Harness(tmp_path)
    assert harness.run("first", ("--params", str(_params_file(tmp_path)))) == 0
    run_dir = harness.results_root / "first"
    assert read_params(run_dir / "params.json") == REUSED
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["tracker_params"] == asdict(REUSED)
    key = CacheKey.from_settings(DetectorSettings(), WEIGHTS_SHA256)
    for name in SEQUENCES:
        cached = detect_with_cache(
            harness.results_root / "cache",
            key,
            f"{name}-validation",
            run_dir / "halves" / name / "validation" / "img1",
            lambda image: 1 / 0,
        )
        assert (run_dir / "results" / f"{name}.txt").read_text() == format_result(run_tracker(cached, REUSED))


def test_a_parameter_file_never_touches_the_tuning_half(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_to_tune(monkeypatch)
    harness = Harness(tmp_path)
    assert harness.run("first", ("--params", str(_params_file(tmp_path)))) == 0
    run_dir = harness.results_root / "first"
    assert not (run_dir / "tuning").exists() and not any(run_dir.glob("halves/*/tuning"))
    assert harness.detector.calls == len(SEQUENCES) * VALIDATION_FRAMES
    cached = sorted(path.name.rsplit("-", 1)[0] for path in (harness.results_root / "cache").glob("*.npz"))
    assert cached == sorted(f"{name}-validation" for name in SEQUENCES)


def test_the_summary_says_the_parameters_were_not_tuned(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    path = _params_file(tmp_path)
    assert harness.run("first", ("--benchmark", "MOT20", "--params", str(path))) == 0
    summary = json.loads((harness.results_root / "first" / "summary.json").read_text())
    # The path, not only the file name: every run writes a file called params.json.
    assert summary["tuning"] == {"skipped": True, "params_file": str(path)}
    assert summary["benchmark"] == "MOT20"


def test_without_a_parameter_file_the_tuning_summary_is_unchanged(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    assert harness.run("first") == 0
    summary = json.loads((harness.results_root / "first" / "summary.json").read_text())
    assert set(summary["tuning"]) == {"combined_hota", "combinations"}


@pytest.mark.parametrize("content", [None, "not json", '{"detection_threshold": 0.6}'])
def test_an_unusable_parameter_file_stops_before_any_detection_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    path = tmp_path / "bad.json"
    if content is not None:
        path.write_text(content)
    harness = Harness(tmp_path)
    assert harness.run("first", ("--params", str(path))) != 0
    assert "cannot read params" in capsys.readouterr().err
    assert harness.loads == 0 and harness.detector.calls == 0
    assert not harness.results_root.exists()
