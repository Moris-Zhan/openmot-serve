import pytest

from tracking.env_check import (
    EnvProbe,
    EnvReport,
    format_outcome,
    parse_cuda_version,
    run_env_check,
)


def healthy_report() -> EnvReport:
    return EnvReport(
        gpu_name="NVIDIA GeForce RTX 5090",
        driver_version="580.126.20",
        torch_version="2.7.1+cu128",
        cuda_version="12.8",
        cuda_available=True,
    )


def probe_returning(report: EnvReport, gpu_matches_cpu: bool = True) -> EnvProbe:
    return EnvProbe(read_report=lambda: report, gpu_matches_cpu=lambda: gpu_matches_cpu)


def test_healthy_environment_passes() -> None:
    outcome = run_env_check(probe_returning(healthy_report()))

    assert outcome.passed
    assert outcome.failures == ()


def test_report_lists_all_five_items() -> None:
    text = format_outcome(run_env_check(probe_returning(healthy_report())))

    for expected in ("NVIDIA GeForce RTX 5090", "580.126.20", "2.7.1+cu128", "12.8", "True"):
        assert expected in text


def test_cuda_unavailable_fails_and_says_so() -> None:
    report = EnvReport("n/a", "n/a", "2.7.1+cpu", None, False)

    outcome = run_env_check(probe_returning(report))

    assert not outcome.passed
    assert any("CUDA" in failure and "not available" in failure for failure in outcome.failures)


def test_cuda_below_minimum_reports_both_versions() -> None:
    report = EnvReport("NVIDIA GeForce RTX 5090", "580.126.20", "2.6.0+cu124", "12.4", True)

    outcome = run_env_check(probe_returning(report))

    assert not outcome.passed
    assert any("12.4" in failure and "12.8" in failure for failure in outcome.failures)


def test_gpu_computation_error_names_the_gpu() -> None:
    def failing_gpu_check() -> bool:
        raise RuntimeError("no kernel image is available for execution on the device")

    probe = EnvProbe(read_report=healthy_report, gpu_matches_cpu=failing_gpu_check)

    outcome = run_env_check(probe)

    assert not outcome.passed
    assert any("NVIDIA GeForce RTX 5090" in failure for failure in outcome.failures)


def test_gpu_result_differing_from_cpu_fails() -> None:
    outcome = run_env_check(probe_returning(healthy_report(), gpu_matches_cpu=False))

    assert not outcome.passed
    assert any("CPU" in failure for failure in outcome.failures)


def test_gpu_is_not_exercised_when_cuda_is_unavailable() -> None:
    calls: list[str] = []

    def gpu_check() -> bool:
        calls.append("called")
        return True

    report = EnvReport("n/a", "n/a", "2.7.1+cpu", None, False)

    run_env_check(EnvProbe(read_report=lambda: report, gpu_matches_cpu=gpu_check))

    assert calls == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12.8", (12, 8)), ("12.10", (12, 10)), ("13.0", (13, 0)), ("12.8.1", (12, 8))],
)
def test_parse_cuda_version(text: str, expected: tuple[int, int]) -> None:
    assert parse_cuda_version(text) == expected


def test_cuda_12_10_is_not_mistaken_for_12_1() -> None:
    report = EnvReport("NVIDIA GeForce RTX 5090", "580.126.20", "9.9.9+cu1210", "12.10", True)

    assert run_env_check(probe_returning(report)).passed
