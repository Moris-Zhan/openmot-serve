import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
PYTHON_VERSION = "3.11"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow["on"] if "on" in workflow else workflow[True]


def _steps(job_name: str) -> list[dict]:
    return _workflow()["jobs"][job_name]["steps"]


def _commands(job_name: str) -> list[str]:
    return [step["run"] for step in _steps(job_name) if "run" in step]


def _index_of_command(job_name: str, fragment: str) -> int:
    matches = [index for index, command in enumerate(_commands(job_name)) if fragment in command]
    assert matches, f"no step in {job_name} runs a command containing {fragment!r}"
    return matches[0]


def _pyproject() -> dict:
    return tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())


def _torch_pins() -> list[str]:
    dependencies = _pyproject()["project"]["dependencies"]
    return [entry for entry in dependencies if entry.startswith(("torch==", "torchvision=="))]


def test_the_workflow_file_exists_and_is_valid_yaml() -> None:
    assert isinstance(_workflow(), dict)


def test_every_push_and_pull_request_runs_the_workflow() -> None:
    triggers = _triggers(_workflow())
    assert "push" in triggers
    assert "pull_request" in triggers


def test_the_workflow_never_runs_on_pull_request_target() -> None:
    assert "pull_request_target" not in _triggers(_workflow())


def test_the_workflow_is_read_only_by_default() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_a_newer_push_cancels_the_superseded_run_but_never_on_main() -> None:
    concurrency = _workflow()["concurrency"]
    assert "github.ref" in concurrency["group"]
    assert "refs/heads/main" in str(concurrency["cancel-in-progress"])


@pytest.mark.parametrize("job_name", ["lint", "test"])
def test_the_jobs_run_on_the_python_version_the_images_use(job_name: str) -> None:
    setup = [step for step in _steps(job_name) if str(step.get("uses", "")).startswith("actions/setup-python@")]
    assert [step["with"]["python-version"] for step in setup] == [PYTHON_VERSION]


def test_lint_runs_ruff_check_with_the_pinned_version_and_no_format_check() -> None:
    commands = "\n".join(_commands("lint"))
    development_dependencies = _pyproject()["project"]["optional-dependencies"]["dev"]
    assert next(entry for entry in development_dependencies if entry.startswith("ruff")) in commands
    assert "ruff check ." in commands
    assert "ruff format" not in commands


def test_the_cpu_build_of_torch_is_installed_before_the_package() -> None:
    assert _index_of_command("test", CPU_TORCH_INDEX) < _index_of_command("test", "pip install -e")


def test_the_cpu_torch_install_pins_the_versions_the_project_requires() -> None:
    install = _commands("test")[_index_of_command("test", CPU_TORCH_INDEX)]
    assert len(_torch_pins()) == 2
    for pin in _torch_pins():
        assert pin in install


def test_the_package_is_installed_with_the_dev_and_service_extras() -> None:
    command = _commands("test")[_index_of_command("test", "pip install -e")]
    assert '".[dev,service]"' in command


def test_the_test_job_runs_the_default_selection_without_gpu_markers() -> None:
    pytest_commands = [command for command in _commands("test") if "pytest" in command]
    assert pytest_commands
    for command in pytest_commands:
        arguments = command.split("pytest", 1)[1].split()
        assert "-m" not in arguments
        assert "gpu" not in command


def test_no_job_requests_a_gpu_runner() -> None:
    for job in _workflow()["jobs"].values():
        assert "gpu" not in str(job["runs-on"]).lower()


MANIFEST_TOOLS = ("kubectl", "kustomize", "kubeconform")
SHA256_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _documented_tool_versions() -> dict[str, str]:
    table = (REPOSITORY_ROOT / "docs" / "kubernetes.md").read_text()
    rows = re.findall(r"^\| (kubectl|kustomize|kubeconform) \| (v[\d.]+) \|", table, re.M)
    return dict(rows)


def _tool_install_command() -> str:
    return _commands("test")[_index_of_command("test", "sha256sum")]


def test_the_manifest_tools_are_installed_before_the_tests_run() -> None:
    assert _index_of_command("test", "sha256sum") < _index_of_command("test", "pytest")


def test_each_manifest_tool_download_is_checked_against_a_sha256_digest() -> None:
    command = _tool_install_command()
    assert "sha256sum --check" in command
    assert len(SHA256_DIGEST.findall(command)) == len(MANIFEST_TOOLS)
    for tool in MANIFEST_TOOLS:
        assert tool in command


def test_the_manifest_tool_versions_match_the_documented_versions() -> None:
    versions = _documented_tool_versions()
    assert set(versions) == set(MANIFEST_TOOLS)
    for tool, version in versions.items():
        assert version in _tool_install_command(), f"{tool} {version} is not the version the workflow installs"


def _build_step(job_name: str, image: str) -> dict:
    builds = [
        step
        for step in _steps(job_name)
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
        and Path(step["with"]["file"]).name == f"{image}.Dockerfile"
    ]
    assert len(builds) == 1, f"{job_name} must build {image}.Dockerfile exactly once"
    return builds[0]


def test_the_api_image_is_built_from_its_dockerfile_on_every_run() -> None:
    build = _build_step("build-api", "api")
    assert build["with"]["file"] == "deploy/docker/api.Dockerfile"
    assert build["with"]["context"] == "."
    assert "if" not in _workflow()["jobs"]["build-api"]


def test_the_api_build_job_neither_pushes_nor_logs_in_nor_gets_write_permission() -> None:
    job = _workflow()["jobs"]["build-api"]
    assert _build_step("build-api", "api")["with"]["push"] is False
    assert not [step for step in job["steps"] if str(step.get("uses", "")).startswith("docker/login-action@")]
    assert "permissions" not in job


def test_the_built_api_image_is_smoke_tested_by_importing_the_entrypoint() -> None:
    tag = _build_step("build-api", "api")["with"]["tags"]
    smoke = _commands("build-api")[_index_of_command("build-api", "import app.run")]
    assert _build_step("build-api", "api")["with"]["load"] is True
    assert f"docker run --rm {tag} python -c" in smoke


def _publish() -> dict:
    return _workflow()["jobs"]["publish"]


def _tags(job_name: str, image: str) -> list[str]:
    return str(_build_step(job_name, image)["with"]["tags"]).strip().splitlines()


def test_images_are_published_only_by_pushes_to_main() -> None:
    condition = _publish()["if"]
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition


def test_publishing_waits_for_lint_tests_and_the_api_build() -> None:
    assert set(_publish()["needs"]) == {"lint", "test", "build-api"}


def test_only_the_publish_job_can_write_packages() -> None:
    workflow = _workflow()
    assert "packages" not in workflow["permissions"]
    assert _publish()["permissions"] == {"contents": "read", "packages": "write"}
    for name, job in workflow["jobs"].items():
        if name != "publish":
            assert "packages" not in job.get("permissions", {})


def test_the_registry_login_uses_only_the_built_in_token() -> None:
    logins = [step for step in _publish()["steps"] if str(step.get("uses", "")).startswith("docker/login-action@")]
    assert len(logins) == 1
    assert logins[0]["with"]["registry"] == "ghcr.io"
    assert logins[0]["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"


@pytest.mark.parametrize("image", ["api", "worker"])
def test_each_published_image_is_pushed_with_a_commit_tag_and_a_main_tag(image: str) -> None:
    assert _build_step("publish", image)["with"]["push"] is True
    tags = _tags("publish", image)
    assert len(tags) == 2
    assert all(tag.startswith("ghcr.io/") and f"/openmot-{image}:" in tag for tag in tags)
    assert sorted(tag.rsplit(":", 1)[1] for tag in tags) == ["${{ github.sha }}", "main"]


def test_the_registry_path_uses_the_lowercased_repository_owner() -> None:
    owner_steps = [step for step in _publish()["steps"] if "GITHUB_REPOSITORY_OWNER,," in step.get("run", "")]
    assert len(owner_steps) == 1
    for image in ("api", "worker"):
        assert all(f"steps.{owner_steps[0]['id']}.outputs" in tag for tag in _tags("publish", image))


def test_disk_space_is_freed_before_the_images_are_built() -> None:
    steps = _publish()["steps"]
    cleanup = [index for index, step in enumerate(steps) if "/usr/share/dotnet" in step.get("run", "")]
    builds = [
        index for index, step in enumerate(steps) if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert cleanup and builds and cleanup[0] < min(builds)


def test_the_large_worker_image_does_not_use_the_build_cache() -> None:
    worker = _build_step("publish", "worker")["with"]
    assert "cache-from" not in worker and "cache-to" not in worker


EXACT_ACTION_VERSION = re.compile(r"^[\w.-]+/[\w.-]+@(v\d+\.\d+\.\d+|[0-9a-f]{40})$")
SECRET_REFERENCE = re.compile(r"secrets\.(\w+)")


def _all_steps() -> list[dict]:
    return [step for job in _workflow()["jobs"].values() for step in job["steps"]]


def test_every_action_is_pinned_to_an_exact_version_or_commit() -> None:
    used = [step["uses"] for step in _all_steps() if "uses" in step]
    assert used
    unpinned = [action for action in used if not EXACT_ACTION_VERSION.match(action)]
    assert not unpinned, f"actions must be pinned to an exact version or commit: {unpinned}"


def test_the_workflow_uses_no_secret_other_than_the_built_in_token() -> None:
    assert set(SECRET_REFERENCE.findall(WORKFLOW_PATH.read_text())) <= {"GITHUB_TOKEN"}


def test_no_credential_is_written_into_the_workflow_as_plain_text() -> None:
    for step in _all_steps():
        for key, value in step.get("with", {}).items():
            if any(word in key.lower() for word in ("password", "token", "secret")):
                assert str(value).startswith("${{") and str(value).endswith("}}"), f"{key} must be an expression"


def test_a_missing_manifest_tool_fails_before_the_tests_run() -> None:
    checks = [index for index, command in enumerate(_commands("test")) if "kustomize version" in command]
    assert checks and "kubeconform -v" in _commands("test")[checks[0]]
    assert "kubectl version --client" in _commands("test")[checks[0]]
    assert checks[0] < _index_of_command("test", "pytest")
