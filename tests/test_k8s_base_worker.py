from k8s_manifests import BASE_DIR, of_kind, render, requires_kustomize

pytestmark = requires_kustomize


def _deployment() -> dict:
    matches = [
        document for document in of_kind(render(BASE_DIR), "Deployment") if document["metadata"]["name"] == "worker"
    ]
    assert len(matches) == 1
    return matches[0]


def _pod() -> dict:
    return _deployment()["spec"]["template"]["spec"]


def _container() -> dict:
    containers = _pod()["containers"]
    assert len(containers) == 1
    return containers[0]


def test_there_is_exactly_one_worker_and_it_replaces_rather_than_rolls() -> None:
    spec = _deployment()["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}


def test_the_worker_asks_for_one_gpu_through_the_nvidia_runtime() -> None:
    assert int(_container()["resources"]["limits"]["nvidia.com/gpu"]) == 1
    assert _pod()["runtimeClassName"] == "nvidia"


def test_the_worker_has_no_liveness_probe_because_a_busy_solo_pool_cannot_answer() -> None:
    assert "livenessProbe" not in _container()


def test_startup_and_readiness_run_the_worker_healthcheck() -> None:
    for probe in ("startupProbe", "readinessProbe"):
        assert _container()[probe]["exec"]["command"] == ["python", "-m", "worker.healthcheck"]


def test_startup_allows_several_times_the_detector_load_time() -> None:
    probe = _container()["startupProbe"]
    assert probe["failureThreshold"] * probe["periodSeconds"] >= 120
    assert probe["timeoutSeconds"] >= 10


def test_termination_outlasts_the_task_time_limit_so_the_current_job_can_finish() -> None:
    config = of_kind(render(BASE_DIR), "ConfigMap")[0]["data"]
    assert _pod()["terminationGracePeriodSeconds"] > int(config["OPENMOT_TASK_TIME_LIMIT_SECONDS"])


def test_the_weights_are_mounted_read_only_from_a_host_file() -> None:
    mount = next(mount for mount in _container()["volumeMounts"] if mount["mountPath"] == "/models/yolox_x.pth")
    assert mount["readOnly"] is True
    volume = next(volume for volume in _pod()["volumes"] if volume["name"] == mount["name"])
    assert volume["hostPath"]["type"] == "File"


def test_the_weights_mount_matches_the_configured_weights_path() -> None:
    config = of_kind(render(BASE_DIR), "ConfigMap")[0]["data"]
    assert config["OPENMOT_WEIGHTS"] == "/models/yolox_x.pth"


def test_the_worker_reads_the_generated_config_and_shares_the_storage_claim() -> None:
    config_map = of_kind(render(BASE_DIR), "ConfigMap")[0]["metadata"]["name"]
    assert [source["configMapRef"]["name"] for source in _container()["envFrom"]] == [config_map]
    mount = next(mount for mount in _container()["volumeMounts"] if mount["mountPath"] == "/data/storage")
    volume = next(volume for volume in _pod()["volumes"] if volume["name"] == mount["name"])
    assert volume["persistentVolumeClaim"]["claimName"] == "openmot-storage"


def test_the_worker_declares_resource_bounds() -> None:
    resources = _container()["resources"]
    assert resources["requests"]["cpu"] and resources["requests"]["memory"]
    assert resources["limits"]["memory"]


def test_the_worker_runs_as_the_image_user_without_privileges() -> None:
    pod = _pod()["securityContext"]
    assert pod["runAsNonRoot"] is True and pod["runAsUser"] == 1000 and pod["fsGroup"] == 1000
    security = _container()["securityContext"]
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"]["drop"] == ["ALL"]
