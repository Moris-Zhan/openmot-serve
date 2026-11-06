import subprocess
from dataclasses import dataclass
from typing import Callable

MINIMUM_CUDA_VERSION: tuple[int, int] = (12, 8)


@dataclass(frozen=True)
class EnvReport:
    gpu_name: str
    driver_version: str
    torch_version: str
    cuda_version: str | None
    cuda_available: bool


@dataclass(frozen=True)
class EnvProbe:
    read_report: Callable[[], EnvReport]
    gpu_matches_cpu: Callable[[], bool]


@dataclass(frozen=True)
class CheckOutcome:
    report: EnvReport
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def parse_cuda_version(text: str) -> tuple[int, int]:
    parts = text.split(".")
    minor = int(parts[1]) if len(parts) > 1 else 0
    return int(parts[0]), minor


def format_version(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def cuda_version_failures(report: EnvReport) -> tuple[str, ...]:
    if report.cuda_version is None:
        return ("PyTorch reports no CUDA version",)
    if parse_cuda_version(report.cuda_version) < MINIMUM_CUDA_VERSION:
        return (
            f"CUDA {report.cuda_version} is below the required minimum "
            f"{format_version(MINIMUM_CUDA_VERSION)}",
        )
    return ()


def gpu_computation_failures(report: EnvReport, probe: EnvProbe) -> tuple[str, ...]:
    try:
        matches = probe.gpu_matches_cpu()
    except Exception as error:
        return (f"GPU computation failed on {report.gpu_name}: {error}",)
    if not matches:
        return (f"GPU result differs from the CPU result on {report.gpu_name}",)
    return ()


def run_env_check(probe: EnvProbe) -> CheckOutcome:
    report = probe.read_report()
    if not report.cuda_available:
        return CheckOutcome(
            report, (f"CUDA is not available to PyTorch ({report.torch_version})",)
        )
    failures = cuda_version_failures(report) + gpu_computation_failures(report, probe)
    return CheckOutcome(report, failures)


def format_outcome(outcome: CheckOutcome) -> str:
    report = outcome.report
    lines = [
        f"GPU:             {report.gpu_name}",
        f"Driver version:  {report.driver_version}",
        f"PyTorch version: {report.torch_version}",
        f"CUDA version:    {report.cuda_version}",
        f"CUDA available:  {report.cuda_available}",
    ]
    if outcome.passed:
        return "\n".join(lines + ["Result: OK"])
    return "\n".join(lines + ["Result: FAILED"] + [f"  - {failure}" for failure in outcome.failures])


def read_driver_version() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first_line = result.stdout.strip().splitlines()
    return first_line[0] if first_line else "unknown"


def read_torch_report() -> EnvReport:
    import torch

    available = torch.cuda.is_available()
    return EnvReport(
        gpu_name=torch.cuda.get_device_name(0) if available else "n/a",
        driver_version=read_driver_version() if available else "n/a",
        torch_version=torch.__version__,
        cuda_version=torch.version.cuda,
        cuda_available=available,
    )


def torch_gpu_matches_cpu() -> bool:
    import torch

    generator = torch.Generator().manual_seed(0)
    left = torch.randn(256, 256, generator=generator)
    right = torch.randn(256, 256, generator=generator)
    on_gpu = (left.cuda() @ right.cuda()).cpu()
    return torch.allclose(on_gpu, left @ right, rtol=1e-3, atol=1e-3)


def main() -> int:
    outcome = run_env_check(EnvProbe(read_torch_report, torch_gpu_matches_cpu))
    print(format_outcome(outcome))
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
