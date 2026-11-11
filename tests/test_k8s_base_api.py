from k8s_manifests import BASE_DIR, of_kind, render, requires_kustomize

pytestmark = requires_kustomize


def _only(kind: str, name: str) -> dict:
    matches = [document for document in of_kind(render(BASE_DIR), kind) if document["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected exactly one {kind} named {name}"
    return matches[0]


def _pod() -> dict:
    return _only("Deployment", "api")["spec"]["template"]["spec"]


def _container() -> dict:
    containers = _pod()["containers"]
    assert len(containers) == 1
    return containers[0]


def test_the_api_serves_on_port_8000() -> None:
    ports = {port["name"]: port["containerPort"] for port in _container()["ports"]}
    assert ports == {"http": 8000}


def test_the_api_reads_the_generated_config() -> None:
    config_map = of_kind(render(BASE_DIR), "ConfigMap")[0]["metadata"]["name"]
    assert [source["configMapRef"]["name"] for source in _container()["envFrom"]] == [config_map]


def test_readiness_asks_the_health_endpoint_so_a_broken_backend_leaves_the_service() -> None:
    probe = _container()["readinessProbe"]
    assert probe["httpGet"]["path"] == "/health"
    assert probe["httpGet"]["port"] == "http"


def test_liveness_only_checks_the_port_because_restarting_cannot_fix_a_lost_redis() -> None:
    probe = _container()["livenessProbe"]
    assert "httpGet" not in probe and "exec" not in probe
    assert probe["tcpSocket"]["port"] == "http"


def test_startup_is_given_a_grace_period_without_depending_on_redis() -> None:
    probe = _container()["startupProbe"]
    assert probe["tcpSocket"]["port"] == "http"
    assert probe["failureThreshold"] * probe["periodSeconds"] >= 30


def test_the_api_declares_resource_bounds() -> None:
    resources = _container()["resources"]
    assert resources["requests"]["cpu"] and resources["requests"]["memory"]
    assert resources["limits"]["memory"]


def test_the_api_runs_as_the_image_user_without_privileges() -> None:
    pod = _pod()["securityContext"]
    assert pod["runAsNonRoot"] is True
    assert pod["runAsUser"] == 1000
    assert pod["fsGroup"] == 1000
    security = _container()["securityContext"]
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"]["drop"] == ["ALL"]


def test_the_api_mounts_the_shared_storage_claim() -> None:
    mount = next(mount for mount in _container()["volumeMounts"] if mount["mountPath"] == "/data/storage")
    volume = next(volume for volume in _pod()["volumes"] if volume["name"] == mount["name"])
    assert volume["persistentVolumeClaim"]["claimName"] == "openmot-storage"
    assert not mount.get("readOnly", False)


def test_the_api_service_is_cluster_internal_and_selects_the_api_pods() -> None:
    service = _only("Service", "api")
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    labels = _only("Deployment", "api")["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"].items() <= labels.items()
    assert [(port["port"], port["targetPort"]) for port in service["spec"]["ports"]] == [(8000, "http")]
