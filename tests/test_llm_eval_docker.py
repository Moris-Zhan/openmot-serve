import subprocess

import pytest

from llm_eval import docker
from llm_eval.docker import ResourceError, Snapshot


def _snapshot(
    containers: dict[str, str] | None = None,
    volumes: set[str] | None = None,
    images: dict[str, str] | None = None,
) -> Snapshot:
    return Snapshot(
        {"cvat_db": "postgres:15"} if containers is None else containers,
        frozenset({"openmot_storage"} if volumes is None else volumes),
        {"a:1": "id1"} if images is None else images,
    )


def _completed(stdout: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, "")


def test_the_dedicated_names_and_defaults_are_fixed() -> None:
    assert docker.CONTAINER == "openmot-llm-eval"
    assert docker.VOLUME == "openmot-llm-eval-models"
    assert docker.DEFAULT_PORT == 11435 and docker.HOST == "127.0.0.1"


def test_identical_snapshots_have_no_violations() -> None:
    assert docker.compare(_snapshot(), _snapshot()) == []


def test_dedicated_containers_and_volumes_may_appear_and_disappear() -> None:
    before = _snapshot()
    during = _snapshot(
        containers={"cvat_db": "postgres:15", "openmot-llm-eval": "ollama/ollama:0.1"},
        volumes={"openmot_storage", "openmot-llm-eval-models"},
    )
    assert docker.compare(before, during) == []
    assert docker.compare(during, before) == []


@pytest.mark.parametrize("name", ["openmot-api", "other", "openmot_llm_eval"])
def test_a_new_container_with_another_name_is_a_violation(name: str) -> None:
    after = _snapshot(containers={"cvat_db": "postgres:15", name: "x"})
    assert any(name in violation for violation in docker.compare(_snapshot(), after))


def test_a_new_volume_with_another_name_is_a_violation() -> None:
    after = _snapshot(volumes={"openmot_storage", "extra"})
    assert any("extra" in violation for violation in docker.compare(_snapshot(), after))


def test_a_missing_container_or_volume_is_a_violation() -> None:
    assert docker.compare(_snapshot(), _snapshot(containers={"x": "y"}))
    assert docker.compare(_snapshot(), _snapshot(volumes={"other"}))


def test_a_container_whose_image_changed_is_a_violation() -> None:
    after = _snapshot(containers={"cvat_db": "postgres:16"})
    assert any("cvat_db" in violation for violation in docker.compare(_snapshot(), after))


def test_only_an_ollama_image_may_be_added() -> None:
    allowed = _snapshot(images={"a:1": "id1", "ollama/ollama:0.12.0": "id2"})
    assert docker.compare(_snapshot(), allowed) == []
    other = _snapshot(images={"a:1": "id1", "redis:7": "id3"})
    assert any("redis:7" in violation for violation in docker.compare(_snapshot(), other))


def test_a_removed_or_retagged_image_is_a_violation() -> None:
    assert docker.compare(_snapshot(), _snapshot(images={}))
    assert docker.compare(_snapshot(), _snapshot(images={"a:1": "different"}))


def test_a_snapshot_is_read_from_the_docker_cli_output() -> None:
    outputs = {
        "ps": "cvat_db postgres:15\nopenmot-llm-eval ollama/ollama:0.1\n",
        "volume": "openmot_storage\nopenmot-llm-eval-models\n",
        "images": "postgres:15 aaa\nollama/ollama:0.1 bbb\n<none>:<none> ccc\n",
    }

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(outputs[command[1]])

    snapshot = docker.take_snapshot(runner)
    assert snapshot.containers == {"cvat_db": "postgres:15", "openmot-llm-eval": "ollama/ollama:0.1"}
    assert snapshot.volumes == {"openmot_storage", "openmot-llm-eval-models"}
    assert snapshot.images["ollama/ollama:0.1"] == "bbb" and snapshot.images["ccc"] == "ccc"
    assert docker.snapshot_from_json(docker.snapshot_to_json(snapshot)) == snapshot


def test_the_run_command_is_pinned_loopback_only_and_uses_the_dedicated_names() -> None:
    command = docker.run_command("ollama/ollama:0.12.5")
    assert command[:3] == ["docker", "run", "-d"]
    assert command[command.index("--name") + 1] == "openmot-llm-eval"
    assert f"{docker.VOLUME}:/root/.ollama" in command
    assert "127.0.0.1:11435:11434" in command
    assert command[command.index("--gpus") + 1] == "all"
    assert command[-1] == "ollama/ollama:0.12.5"


def test_the_port_can_be_changed_but_stays_on_loopback() -> None:
    assert "127.0.0.1:12000:11434" in docker.run_command("ollama/ollama:0.12.5", port=12000)


@pytest.mark.parametrize("image", ["ollama/ollama:latest", "ollama/ollama", "redis:7", "ollama/ollama-evil:1"])
def test_an_unpinned_or_non_ollama_image_is_refused(image: str) -> None:
    with pytest.raises(ResourceError):
        docker.run_command(image)


def test_cleanup_removes_only_the_dedicated_container_and_volume() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return _completed()

    docker.cleanup(runner)
    assert commands == [
        ["docker", "rm", "-f", "openmot-llm-eval"],
        ["docker", "volume", "rm", "openmot-llm-eval-models"],
    ]


def test_cleanup_can_keep_the_models_when_explicitly_asked() -> None:
    commands: list[list[str]] = []
    docker.cleanup(lambda command: commands.append(command) or _completed(), keep_models=True)
    assert commands == [["docker", "rm", "-f", "openmot-llm-eval"]]


@pytest.mark.parametrize(
    ("container", "volume"), [("openmot-api", docker.VOLUME), (docker.CONTAINER, "openmot_storage")]
)
def test_cleanup_refuses_a_name_that_is_not_dedicated_and_runs_nothing(container: str, volume: str) -> None:
    commands: list[list[str]] = []
    with pytest.raises(ResourceError):
        docker.cleanup(lambda command: commands.append(command) or _completed(), container=container, volume=volume)
    assert commands == []
