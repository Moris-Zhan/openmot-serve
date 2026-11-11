from k8s_cluster import ClusterProbe, skip_reason


def _probe(**overrides) -> ClusterProbe:  # type: ignore[no-untyped-def]
    values = {
        "kubectl_installed": True,
        "kubeconfig": "/home/user/kubeconfig",
        "reachable": lambda: True,
        "gpu_count": lambda: 1,
        "namespace_exists": lambda: False,
        "frames_present": True,
    }
    return ClusterProbe(**(values | overrides))


def test_a_usable_cluster_is_not_skipped() -> None:
    assert skip_reason(_probe()) is None


def test_a_missing_kubectl_is_named() -> None:
    assert "kubectl" in skip_reason(_probe(kubectl_installed=False))  # type: ignore[operator]


def test_a_missing_kubeconfig_is_named_so_a_default_context_is_never_used() -> None:
    assert "KUBECONFIG" in skip_reason(_probe(kubeconfig=None))  # type: ignore[operator]
    assert "KUBECONFIG" in skip_reason(_probe(kubeconfig=""))  # type: ignore[operator]


def test_an_unreachable_cluster_is_named() -> None:
    assert "not reachable" in skip_reason(_probe(reachable=lambda: False))  # type: ignore[operator]


def test_a_cluster_without_a_gpu_is_named() -> None:
    assert "nvidia.com/gpu" in skip_reason(_probe(gpu_count=lambda: 0))  # type: ignore[operator]


def test_an_existing_openmot_namespace_is_never_touched() -> None:
    reason = skip_reason(_probe(namespace_exists=lambda: True))
    assert reason is not None and "openmot" in reason and "existing" in reason


def test_missing_frames_are_named() -> None:
    assert "frames" in skip_reason(_probe(frames_present=False))  # type: ignore[operator]


def test_the_cluster_is_not_queried_when_an_earlier_check_already_decides() -> None:
    def explode() -> bool:
        raise AssertionError("the cluster must not be queried")

    assert skip_reason(_probe(kubeconfig=None, reachable=explode)) is not None
    assert skip_reason(_probe(kubectl_installed=False, reachable=explode)) is not None
