import re
import shutil
import subprocess
from pathlib import Path

from k8s_manifests import BASE_DIR, K3S_OVERLAY_DIR, of_kind, render, requires_kustomize

from tracking.yolox import WEIGHTS_SHA256

DOC = Path(__file__).resolve().parents[1] / "docs" / "kubernetes.md"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _overlay_images() -> set[str]:
    deployments = of_kind(render(K3S_OVERLAY_DIR), "Deployment")
    return {
        container["image"]
        for deployment in deployments
        for container in deployment["spec"]["template"]["spec"]["containers"]
    }


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


SECTIONS_IN_ORDER = [
    "## 驗證環境",
    "## 前置條件",
    "## 部署",
    "## 套用",
    "## 設定值",
    "## 儲存與資料保存",
    "## 疑難排解",
    "## 外部叢集驗證手冊",
]
KIND_BY_ALIAS = {
    "deployment": "Deployment",
    "statefulset": "StatefulSet",
    "service": "Service",
    "svc": "Service",
    "pvc": "PersistentVolumeClaim",
}


def test_the_guide_has_every_section_in_reading_order() -> None:
    text = _text()
    positions = [text.index(heading) for heading in SECTIONS_IN_ORDER]
    assert positions == sorted(positions)


def test_the_apply_command_points_at_the_overlay_and_the_health_check_at_its_node_port() -> None:
    text = _text()
    assert "kubectl apply -k deploy/k8s/overlays/k3s-single-node" in text
    assert K3S_OVERLAY_DIR.is_dir()
    assert ":30080/health" in text


@requires_kustomize
def test_every_workload_the_guide_names_exists_in_the_rendered_overlay() -> None:
    documents = render(K3S_OVERLAY_DIR)
    existing = {(document["kind"], document["metadata"]["name"]) for document in documents}
    references = re.findall(r"\b(deployment|statefulset|service|svc|pvc)/([a-z0-9-]+)", _text())
    assert references
    for alias, name in references:
        assert (KIND_BY_ALIAS[alias], name) in existing, f"{alias}/{name} is not rendered by the overlay"


@requires_kustomize
def test_every_configuration_variable_is_documented() -> None:
    text = _text()
    names = of_kind(render(BASE_DIR), "ConfigMap")[0]["data"]
    missing = [name for name in names if f"`{name}`" not in text]
    assert missing == []


def test_the_guide_explains_how_removing_workloads_differs_from_removing_the_data() -> None:
    text = _text()
    assert "kubectl delete -k deploy/k8s/overlays/k3s-single-node" in text
    assert "kubectl -n openmot delete deployment/api deployment/worker" in text
    assert "local-path" in text and "備份" in text


def test_the_troubleshooting_section_covers_the_known_failure_modes() -> None:
    text = _text()
    for symptom in ("ErrImagePull", "Insufficient nvidia.com/gpu", "NotReady", "Permission denied", "CrashLoopBackOff"):
        assert symptom in text, symptom


def test_the_external_runbook_covers_each_unverified_check_and_how_to_record_it() -> None:
    text = _text()
    runbook = text[text.index("## 外部叢集驗證手冊"):]
    for expected in (
        "kubectl -n openmot get pods",
        "python -m tracking.run",
        "cmp",
        "kubectl -n openmot delete pod -l app.kubernetes.io/name=worker",
        "kubectl -n openmot delete pod -l app.kubernetes.io/name=redis",
        "kubectl top pods -n openmot",
        "pytest -m integration tests/test_k8s_e2e.py",
    ):
        assert expected in runbook, expected
