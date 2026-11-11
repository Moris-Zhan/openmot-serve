import shutil
import subprocess
from pathlib import Path

from k8s_manifests import K3S_OVERLAY_DIR, of_kind, render, requires_kustomize
from tracking.yolox import WEIGHTS_SHA256

DOC = Path(__file__).resolve().parents[1] / "docs" / "kubernetes.md"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _overlay_images() -> set[str]:
    deployments = of_kind(render(K3S_OVERLAY_DIR), "Deployment")
    return {container["image"] for deployment in deployments for container in deployment["spec"]["template"]["spec"]["containers"]}


def _overlay_weights_path() -> str:
    worker = next(d for d in of_kind(render(K3S_OVERLAY_DIR), "Deployment") if d["metadata"]["name"] == "worker")
    volumes = worker["spec"]["template"]["spec"]["volumes"]
    return next(volume for volume in volumes if volume["name"] == "weights")["hostPath"]["path"]


@requires_kustomize
def test_the_image_load_command_names_exactly_the_images_the_overlay_runs() -> None:
    images = _overlay_images()
    command = next(line for line in _text().splitlines() if line.startswith("docker save"))
    assert images == {"openmot-api:dev", "openmot-worker:dev"}
    assert all(image in command for image in images)
    assert "k3s ctr images import" in command


@requires_kustomize
def test_the_weights_are_placed_where_the_overlay_mounts_them_from() -> None:
    text = _text()
    assert _overlay_weights_path() in text
    assert WEIGHTS_SHA256 in text


def test_the_document_says_the_steps_were_not_run_on_a_real_cluster() -> None:
    assert "未經實機驗證" in _text()


def test_the_document_checks_that_the_images_are_visible_to_the_cluster() -> None:
    assert "k3s crictl images" in _text()


TOOL_VERSIONS = {"kubectl": "v1.31.4", "kustomize": "v5.5.0", "kubeconform": "v0.6.7"}
TOOL_VERSION_COMMANDS = {
    "kubectl": ["kubectl", "version", "--client"],
    "kustomize": ["kustomize", "version"],
    "kubeconform": ["kubeconform", "-v"],
}


def test_the_environment_section_comes_before_the_deployment_steps() -> None:
    text = _text()
    assert "## 驗證環境" in text
    assert text.index("## 驗證環境") < text.index("## 部署")


def test_the_environment_section_records_the_tools_and_their_versions() -> None:
    text = _text()
    for tool, version in TOOL_VERSIONS.items():
        assert tool in text and version in text


def test_the_documented_versions_are_what_the_installed_tools_report() -> None:
    for tool, version in TOOL_VERSIONS.items():
        if shutil.which(tool) is None:
            continue
        result = subprocess.run(TOOL_VERSION_COMMANDS[tool], capture_output=True, text=True, check=True)
        assert version in result.stdout, tool


def test_the_environment_section_explains_why_there_is_no_cluster_and_the_chosen_level() -> None:
    text = _text()
    assert "too many open files" in text
    assert "無叢集" in text
    assert "ai-model" in text
