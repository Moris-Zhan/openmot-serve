import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml
from k8s_manifests import BASE_DIR, render, requires_kustomize

pytestmark = requires_kustomize

WORKLOAD_KINDS = ("Deployment", "StatefulSet")
KUBERNETES_VERSION = "1.31.0"
SCHEMA_CACHE = Path(tempfile.gettempdir()) / "openmot-kubeconform-cache"
CREDENTIAL_WORDS = re.compile(r"password|passwd|secret|token|api[_-]?key|credential|private[_-]?key", re.IGNORECASE)


def _workloads() -> list[dict]:
    return [document for document in render(BASE_DIR) if document["kind"] in WORKLOAD_KINDS]


def _pods_and_containers() -> list[tuple[str, dict, dict]]:
    return [
        (f"{workload['kind']}/{workload['metadata']['name']}", workload["spec"]["template"]["spec"], container)
        for workload in _workloads()
        for container in workload["spec"]["template"]["spec"]["containers"]
    ]


def test_every_workload_is_found() -> None:
    assert {workload["metadata"]["name"] for workload in _workloads()} == {"redis", "api", "worker"}


def test_no_container_runs_as_root_or_privileged() -> None:
    for name, pod, container in _pods_and_containers():
        pod_security = pod.get("securityContext", {})
        security = container.get("securityContext", {})
        assert pod_security.get("runAsNonRoot") is True or security.get("runAsNonRoot") is True, name
        assert security.get("runAsUser", pod_security.get("runAsUser", 0)) != 0, name
        assert not security.get("privileged", False), name
        assert security.get("allowPrivilegeEscalation") is False, name
        assert not pod.get("hostNetwork", False) and not pod.get("hostPID", False), name


def test_every_container_declares_a_readiness_probe_and_resource_bounds() -> None:
    for name, _, container in _pods_and_containers():
        assert "readinessProbe" in container, name
        assert container["resources"]["requests"]["cpu"], name
        assert container["resources"]["requests"]["memory"], name
        assert container["resources"]["limits"]["memory"], name


def test_every_host_path_is_mounted_read_only_everywhere() -> None:
    for name, pod, container in _pods_and_containers():
        host_volumes = {volume["name"] for volume in pod.get("volumes", []) if "hostPath" in volume}
        for mount in container.get("volumeMounts", []):
            if mount["name"] in host_volumes:
                assert mount["readOnly"] is True, f"{name} mounts {mount['name']} writable"


def test_the_manifests_contain_no_secrets_or_credentials() -> None:
    documents = render(BASE_DIR)
    assert all(document["kind"] != "Secret" for document in documents)
    text = yaml.safe_dump(documents)
    assert CREDENTIAL_WORDS.search(text) is None
    assert not re.search(r"://[^/\s]*@", text), "a URL embeds credentials"


def test_the_rendered_manifests_pass_the_kubernetes_schema() -> None:
    if shutil.which("kubeconform") is None:
        pytest.skip("the kubeconform CLI is not installed")
    rendered = subprocess.run(["kustomize", "build", str(BASE_DIR)], capture_output=True, text=True, check=True).stdout
    SCHEMA_CACHE.mkdir(exist_ok=True)
    result = subprocess.run(
        ["kubeconform", "-strict", "-summary", "-kubernetes-version", KUBERNETES_VERSION, "-cache", str(SCHEMA_CACHE)],
        input=rendered,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if "failed downloading schema" in output:
        pytest.skip("the Kubernetes schemas cannot be downloaded in this environment")
    assert result.returncode == 0, output
