from k8s_manifests import BASE_DIR, K3S_OVERLAY_DIR, of_kind, render, requires_kustomize

pytestmark = requires_kustomize

NAMESPACE = "openmot"
WEIGHTS_ON_NODE = "/var/lib/openmot/models/yolox_x.pth"


def _named(kind: str, name: str) -> dict:
    matches = [document for document in of_kind(render(K3S_OVERLAY_DIR), kind) if document["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected exactly one {kind} named {name}"
    return matches[0]


def test_the_overlay_creates_the_openmot_namespace_and_puts_everything_in_it() -> None:
    documents = render(K3S_OVERLAY_DIR)
    assert [d["metadata"]["name"] for d in of_kind(documents, "Namespace")] == [NAMESPACE]
    namespaced = [document for document in documents if document["kind"] != "Namespace"]
    assert namespaced and all(document["metadata"]["namespace"] == NAMESPACE for document in namespaced)


def test_the_overlay_keeps_every_base_resource() -> None:
    base = {(d["kind"], d["metadata"]["name"]) for d in render(BASE_DIR)}
    overlay = {(d["kind"], d["metadata"]["name"]) for d in render(K3S_OVERLAY_DIR)}
    assert base <= overlay
    assert overlay - base == {("Namespace", NAMESPACE)}


def test_only_the_api_is_reachable_from_outside_the_node_network() -> None:
    services = of_kind(render(K3S_OVERLAY_DIR), "Service")
    exposed = {service["metadata"]["name"] for service in services if service["spec"].get("type") == "NodePort"}
    assert exposed == {"api"}
    api = _named("Service", "api")
    assert [(port["port"], port["nodePort"]) for port in api["spec"]["ports"]] == [(8000, 30080)]


def test_storage_uses_the_k3s_local_path_class() -> None:
    assert _named("PersistentVolumeClaim", "openmot-storage")["spec"]["storageClassName"] == "local-path"
    templates = _named("StatefulSet", "redis")["spec"]["volumeClaimTemplates"]
    assert [template["spec"]["storageClassName"] for template in templates] == ["local-path"]


def test_the_weights_come_from_the_node_path_chosen_by_the_overlay() -> None:
    volumes = _named("Deployment", "worker")["spec"]["template"]["spec"]["volumes"]
    weights = next(volume for volume in volumes if volume["name"] == "weights")
    assert weights["hostPath"] == {"path": WEIGHTS_ON_NODE, "type": "File"}


def test_images_are_pinned_by_the_overlay_and_never_pulled_from_a_registry() -> None:
    for name, image in (("api", "openmot-api:dev"), ("worker", "openmot-worker:dev")):
        container = _named("Deployment", name)["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == image
        assert container["imagePullPolicy"] == "IfNotPresent"


def test_the_overlay_does_not_weaken_the_worker() -> None:
    pod = _named("Deployment", "worker")["spec"]
    assert pod["strategy"] == {"type": "Recreate"}
    assert pod["template"]["spec"]["runtimeClassName"] == "nvidia"
    assert int(pod["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"]) == 1
