import pytest
from k8s_manifests import BASE_DIR, of_kind, render, requires_kustomize

pytestmark = requires_kustomize


def _only(kind: str, name: str) -> dict:
    matches = [document for document in of_kind(render(BASE_DIR), kind) if document["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected exactly one {kind} named {name}"
    return matches[0]


def _redis_container() -> dict:
    containers = _only("StatefulSet", "redis")["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]


def test_shared_storage_is_one_single_node_claim() -> None:
    claim = _only("PersistentVolumeClaim", "openmot-storage")
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"]["requests"]["storage"]


def test_redis_is_a_single_replica_stateful_set() -> None:
    redis = _only("StatefulSet", "redis")
    assert redis["spec"]["replicas"] == 1
    assert redis["spec"]["serviceName"] == "redis"


def test_redis_persists_through_an_append_only_file_on_its_own_claim() -> None:
    redis = _only("StatefulSet", "redis")
    assert _redis_container()["command"] == ["redis-server", "--appendonly", "yes"]
    templates = redis["spec"]["volumeClaimTemplates"]
    assert len(templates) == 1
    assert templates[0]["spec"]["accessModes"] == ["ReadWriteOnce"]
    mounts = {mount["name"]: mount["mountPath"] for mount in _redis_container()["volumeMounts"]}
    assert mounts[templates[0]["metadata"]["name"]] == "/data"


@pytest.mark.parametrize("probe", ["readinessProbe", "livenessProbe"])
def test_redis_probes_ask_redis_to_ping(probe: str) -> None:
    command = _redis_container()[probe]["exec"]["command"]
    assert command[:2] == ["redis-cli", "ping"]


def test_redis_declares_resource_bounds() -> None:
    resources = _redis_container()["resources"]
    assert resources["requests"]["cpu"] and resources["requests"]["memory"]
    assert resources["limits"]["memory"]


def test_redis_runs_unprivileged_and_can_write_its_volume() -> None:
    pod = _only("StatefulSet", "redis")["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["fsGroup"] == pod["securityContext"]["runAsUser"]
    assert _redis_container()["securityContext"]["allowPrivilegeEscalation"] is False


def test_the_redis_service_selects_the_redis_pods_on_the_redis_port() -> None:
    service = _only("Service", "redis")
    labels = _only("StatefulSet", "redis")["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"].items() <= labels.items()
    assert [port["port"] for port in service["spec"]["ports"]] == [6379]
