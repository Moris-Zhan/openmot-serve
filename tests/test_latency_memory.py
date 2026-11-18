import subprocess

import pytest

from eval.latency import read_gpu_memory_mib


class FakeRun:
    def __init__(self, stdout: str = "", returncode: int = 0, error: Exception | None = None) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, "")


def test_a_single_integer_is_the_used_memory_in_mib() -> None:
    assert read_gpu_memory_mib("cuda", FakeRun("1234\n")) == 1234


def test_the_query_asks_for_the_used_memory_of_one_device_without_decoration() -> None:
    run = FakeRun("1\n")
    read_gpu_memory_mib("cuda", run)
    command = run.commands[0]
    assert command[0] == "nvidia-smi"
    assert "--query-gpu=memory.used" in command
    assert "--format=csv,noheader,nounits" in command
    assert command[command.index("-i") + 1] == "0"


def test_the_device_index_comes_from_the_device_name() -> None:
    run = FakeRun("1\n")
    read_gpu_memory_mib("cuda:1", run)
    assert run.commands[0][run.commands[0].index("-i") + 1] == "1"


@pytest.mark.parametrize("device", ["cpu", "mps", "cuda:", "cuda:x", "cuda:-1", ""])
def test_a_device_that_is_not_a_cuda_index_is_unavailable_and_nothing_is_run(device: str) -> None:
    run = FakeRun("1\n")
    assert read_gpu_memory_mib(device, run) is None
    assert run.commands == []


def test_a_missing_nvidia_smi_is_unavailable() -> None:
    assert read_gpu_memory_mib("cuda", FakeRun(error=FileNotFoundError("nvidia-smi"))) is None


def test_a_failing_nvidia_smi_is_unavailable() -> None:
    assert read_gpu_memory_mib("cuda", FakeRun("1234\n", returncode=9)) is None


def test_a_hanging_nvidia_smi_is_unavailable() -> None:
    error = subprocess.TimeoutExpired("nvidia-smi", 10)
    assert read_gpu_memory_mib("cuda", FakeRun(error=error)) is None


@pytest.mark.parametrize("output", ["", "\n", "N/A\n", "12 34\n", "12.5\n", "-3\n", "1234\n5678\n"])
def test_output_that_is_not_a_single_non_negative_integer_is_unavailable(output: str) -> None:
    assert read_gpu_memory_mib("cuda", FakeRun(output)) is None
