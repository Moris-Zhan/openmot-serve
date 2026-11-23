import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

PREFIX = "openmot-llm-eval"
CONTAINER = "openmot-llm-eval"
VOLUME = "openmot-llm-eval-models"
HOST = "127.0.0.1"
DEFAULT_PORT = 11435
OLLAMA_IMAGE_REPO = "ollama/ollama"
OLLAMA_MODELS_DIR = "/root/.ollama"
OLLAMA_PORT = 11434

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class ResourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    containers: dict[str, str]
    volumes: frozenset[str]
    images: dict[str, str]


def _lines(completed: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def take_snapshot(runner: Runner) -> Snapshot:
    containers: dict[str, str] = {}
    for line in _lines(runner(["docker", "ps", "-a", "--format", "{{.Names}} {{.Image}}"])):
        name, _, image = line.partition(" ")
        containers[name] = image
    volumes = frozenset(_lines(runner(["docker", "volume", "ls", "-q"])))
    images: dict[str, str] = {}
    for line in _lines(runner(["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"])):
        name, _, identifier = line.rpartition(" ")
        images[identifier if name == "<none>:<none>" else name] = identifier
    return Snapshot(containers, volumes, images)


def snapshot_to_json(snapshot: Snapshot) -> str:
    document = {
        "containers": snapshot.containers,
        "volumes": sorted(snapshot.volumes),
        "images": snapshot.images,
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def snapshot_from_json(text: str) -> Snapshot:
    document = json.loads(text)
    return Snapshot(document["containers"], frozenset(document["volumes"]), document["images"])


def _dedicated(name: str) -> bool:
    return name.startswith(PREFIX)


def compare(before: Snapshot, after: Snapshot) -> list[str]:
    violations: list[str] = []
    for name in sorted(after.containers.keys() - before.containers.keys()):
        if not _dedicated(name):
            violations.append(f"container {name} was added")
    for name in sorted(before.containers.keys() - after.containers.keys()):
        if not _dedicated(name):
            violations.append(f"container {name} disappeared")
    for name in sorted(before.containers.keys() & after.containers.keys()):
        if before.containers[name] != after.containers[name]:
            violations.append(f"container {name} changed image")
    for name in sorted(after.volumes - before.volumes):
        if not _dedicated(name):
            violations.append(f"volume {name} was added")
    for name in sorted(before.volumes - after.volumes):
        if not _dedicated(name):
            violations.append(f"volume {name} disappeared")
    for name in sorted(after.images.keys() - before.images.keys()):
        if not name.startswith(f"{OLLAMA_IMAGE_REPO}:"):
            violations.append(f"image {name} was added")
    for name in sorted(before.images.keys() - after.images.keys()):
        violations.append(f"image {name} disappeared")
    for name in sorted(before.images.keys() & after.images.keys()):
        if before.images[name] != after.images[name]:
            violations.append(f"image {name} changed")
    return violations


def run_command(image: str, port: int = DEFAULT_PORT) -> list[str]:
    repository, _, tag = image.partition(":")
    if repository != OLLAMA_IMAGE_REPO or not tag or tag == "latest":
        raise ResourceError(f"the image must be {OLLAMA_IMAGE_REPO} with a pinned tag, got {image!r}")
    return [
        "docker", "run", "-d", "--name", CONTAINER, "--gpus", "all",
        "-v", f"{VOLUME}:{OLLAMA_MODELS_DIR}", "-p", f"{HOST}:{port}:{OLLAMA_PORT}", image,
    ]  # fmt: skip


def cleanup(
    runner: Runner, keep_models: bool = False, container: str = CONTAINER, volume: str = VOLUME
) -> list[list[str]]:
    for name in (container, volume):
        if not _dedicated(name):
            raise ResourceError(f"refusing to remove {name!r}: it is not a dedicated {PREFIX}* resource")
    commands = [["docker", "rm", "-f", container]]
    if not keep_models:
        commands.append(["docker", "volume", "rm", volume])
    for command in commands:
        runner(command)
    return commands
