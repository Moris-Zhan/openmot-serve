import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml
from k8s_manifests import K3S_OVERLAY_DIR, K8S_DIR, of_kind, render, requires_kustomize

pytestmark = requires_kustomize

MONITORING_DIR = K8S_DIR / "monitoring"
RULES_FILE = Path(__file__).resolve().parents[1] / "monitoring" / "rules" / "openmot.rules.yml"
WORKLOAD_NAMESPACE = "openmot"
MONITORING_NAMESPACE = "monitoring"
RELEASE_LABEL = {"release": "monitoring"}
KUBERNETES_VERSION = "1.31.0"
SCHEMA_CACHE = Path(tempfile.gettempdir()) / "openmot-kubeconform-cache"
CUSTOM_KINDS = ("ServiceMonitor", "PrometheusRule")
CORE_KINDS = {"Namespace", "Service", "Deployment", "StatefulSet", "PersistentVolumeClaim", "ConfigMap"}
EXPECTED_JOBS = {"api-metrics": "openmot-api", "worker-metrics": "openmot-worker"}


def _monitoring() -> list[dict]:
    return render(MONITORING_DIR)


def _service_monitors() -> list[dict]:
    return of_kind(_monitoring(), "ServiceMonitor")


def _rule_groups() -> list[dict]:
    return yaml.safe_load(RULES_FILE.read_text())["groups"]


def _job_of_alert(alert: str) -> str:
    rule = next(rule for group in _rule_groups() for rule in group["rules"] if rule.get("alert") == alert)
    jobs = set(re.findall(r'job="([^"]+)"', rule["expr"]))
    assert len(jobs) == 1, f"{alert} should refer to exactly one job"
    return jobs.pop()


def _selected_service(monitor: dict, services: list[dict]) -> list[dict]:
    spec = monitor["spec"]
    assert spec["namespaceSelector"]["matchNames"] == [WORKLOAD_NAMESPACE]
    wanted = spec["selector"]["matchLabels"]
    return [service for service in services if wanted.items() <= service["metadata"].get("labels", {}).items()]


def test_the_monitoring_render_holds_the_namespace_two_service_monitors_and_one_rule() -> None:
    documents = _monitoring()
    assert [(d["kind"], d["metadata"]["name"]) for d in of_kind(documents, "Namespace")] == [
        ("Namespace", MONITORING_NAMESPACE)
    ]
    assert len(of_kind(documents, "ServiceMonitor")) == 2
    assert len(of_kind(documents, "PrometheusRule")) == 1
    assert len(documents) == 4


def test_the_monitoring_kustomization_does_not_rewrite_namespaces() -> None:
    kustomization = yaml.safe_load((MONITORING_DIR / "kustomization.yaml").read_text())
    assert "namespace" not in kustomization


@pytest.mark.parametrize("kind", CUSTOM_KINDS)
def test_custom_resources_live_in_the_monitoring_namespace_with_the_release_label(kind: str) -> None:
    for document in of_kind(_monitoring(), kind):
        assert document["metadata"]["namespace"] == MONITORING_NAMESPACE
        assert RELEASE_LABEL.items() <= document["metadata"]["labels"].items()


def test_each_service_monitor_scrapes_the_named_metrics_port_and_pins_the_job_label() -> None:
    monitors = {m["metadata"]["name"]: m for m in _service_monitors()}
    assert set(monitors) == {"api-metrics", "worker-metrics"}
    for name, monitor in monitors.items():
        assert monitor["spec"]["selector"]["matchLabels"] == {"app.kubernetes.io/name": name}
        (endpoint,) = monitor["spec"]["endpoints"]
        assert (endpoint["port"], endpoint["path"], endpoint["interval"]) == ("metrics", "/metrics", "15s")
        (relabeling,) = [r for r in endpoint["relabelings"] if r["targetLabel"] == "job"]
        assert relabeling["action"] == "replace" and relabeling["replacement"] == EXPECTED_JOBS[name]


def test_each_service_monitor_selects_exactly_one_metrics_service_of_the_workload_overlay() -> None:
    services = of_kind(render(K3S_OVERLAY_DIR), "Service")
    for monitor in _service_monitors():
        selected = _selected_service(monitor, services)
        assert len(selected) == 1, monitor["metadata"]["name"]
        ports = {port["name"] for port in selected[0]["spec"]["ports"]}
        assert {endpoint["port"] for endpoint in monitor["spec"]["endpoints"]} <= ports


def test_the_job_labels_match_the_ones_the_down_alerts_use() -> None:
    jobs = {
        monitor["metadata"]["name"]: monitor["spec"]["endpoints"][0]["relabelings"][0]["replacement"]
        for monitor in _service_monitors()
    }
    assert jobs == {"api-metrics": _job_of_alert("ApiDown"), "worker-metrics": _job_of_alert("WorkerDown")}


def test_the_prometheus_rule_carries_exactly_the_groups_of_the_rule_file() -> None:
    (rule,) = of_kind(_monitoring(), "PrometheusRule")
    assert rule["spec"]["groups"] == _rule_groups()


def test_the_workload_overlay_needs_no_operator_custom_resources() -> None:
    kinds = {document["kind"] for document in render(K3S_OVERLAY_DIR)}
    assert kinds <= CORE_KINDS, f"unexpected kinds: {sorted(kinds - CORE_KINDS)}"
    assert not kinds & set(CUSTOM_KINDS)


def test_the_core_resources_of_the_monitoring_render_pass_the_kubernetes_schema() -> None:
    if shutil.which("kubeconform") is None:
        pytest.skip("the kubeconform CLI is not installed")
    rendered = subprocess.run(["kustomize", "build", str(MONITORING_DIR)], capture_output=True, text=True, check=True)
    SCHEMA_CACHE.mkdir(exist_ok=True)
    result = subprocess.run(
        [
            "kubeconform",
            "-strict",
            "-summary",
            "-skip",
            ",".join(CUSTOM_KINDS),
            "-kubernetes-version",
            KUBERNETES_VERSION,
            "-cache",
            str(SCHEMA_CACHE),
        ],
        input=rendered.stdout,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if "failed downloading schema" in output:
        pytest.skip("the Kubernetes schemas cannot be downloaded in this environment")
    assert result.returncode == 0, output
    assert "Valid: 1," in output and "Skipped: 3" in output, f"the Namespace was not checked: {output}"
