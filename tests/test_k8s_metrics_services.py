import pytest
from k8s_manifests import BASE_DIR, K3S_OVERLAY_DIR, of_kind, render, requires_kustomize

from jobs.settings import DEFAULT_API_METRICS_PORT, DEFAULT_WORKER_METRICS_PORT

pytestmark = requires_kustomize

METRICS_PORTS = {"api": DEFAULT_API_METRICS_PORT, "worker": DEFAULT_WORKER_METRICS_PORT}


def _only(documents: list[dict], kind: str, name: str) -> dict:
    matches = [document for document in of_kind(documents, kind) if document["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected exactly one {kind} named {name}"
    return matches[0]


def _container(component: str) -> dict:
    deployment = _only(render(BASE_DIR), "Deployment", component)
    return deployment["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize("component", ["api", "worker"])
def test_the_container_declares_the_named_metrics_port_with_the_service_default(component: str) -> None:
    ports = {port["name"]: port["containerPort"] for port in _container(component)["ports"]}
    assert ports["metrics"] == METRICS_PORTS[component]


@pytest.mark.parametrize("component", ["api", "worker"])
def test_the_metrics_service_is_cluster_internal_with_one_named_port(component: str) -> None:
    service = _only(render(BASE_DIR), "Service", f"{component}-metrics")
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert [(port["name"], port["port"], port["targetPort"]) for port in service["spec"]["ports"]] == [
        ("metrics", METRICS_PORTS[component], "metrics")
    ]


@pytest.mark.parametrize("component", ["api", "worker"])
def test_the_metrics_service_selects_the_pods_and_is_labelled_for_the_service_monitor(component: str) -> None:
    documents = render(BASE_DIR)
    service = _only(documents, "Service", f"{component}-metrics")
    pod_labels = _only(documents, "Deployment", component)["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"].items() <= pod_labels.items()
    assert service["metadata"]["labels"]["app.kubernetes.io/name"] == f"{component}-metrics"


@pytest.mark.parametrize("component", ["api", "worker"])
def test_the_metrics_service_keeps_not_ready_pods_scrapeable(component: str) -> None:
    service = _only(render(BASE_DIR), "Service", f"{component}-metrics")
    assert service["spec"]["publishNotReadyAddresses"] is True


def test_the_public_api_service_has_no_metrics_port_and_does_not_publish_not_ready_pods() -> None:
    service = _only(render(BASE_DIR), "Service", "api")
    assert [port["name"] for port in service["spec"]["ports"]] == ["http"]
    assert not service["spec"].get("publishNotReadyAddresses", False)


def test_no_externally_reachable_service_carries_a_metrics_port_in_the_k3s_overlay() -> None:
    services = of_kind(render(K3S_OVERLAY_DIR), "Service")
    external = [service for service in services if service["spec"].get("type", "ClusterIP") != "ClusterIP"]
    assert external, "the overlay is expected to expose the api"
    for service in external:
        carried = {port["name"] for port in service["spec"]["ports"]}
        assert "metrics" not in carried, f"{service['metadata']['name']} would publish metrics on the node"
