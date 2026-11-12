import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

NAMESPACE = "openmot"
REQUEST_TIMEOUT = "--request-timeout=15s"
FRAMES = Path(__file__).resolve().parents[1] / "data" / "MOT17" / "train" / "MOT17-09-FRCNN" / "img1"


@dataclass(frozen=True)
class ClusterProbe:
    kubectl_installed: bool
    kubeconfig: str | None
    reachable: Callable[[], bool]
    gpu_count: Callable[[], int]
    namespace_exists: Callable[[], bool]
    frames_present: bool


def skip_reason(probe: ClusterProbe) -> str | None:
    if not probe.kubectl_installed:
        return "the kubectl CLI is not installed"
    if not probe.kubeconfig:
        return "KUBECONFIG is not set; the cluster tests never fall back to a default context"
    if not probe.reachable():
        return f"the cluster named by KUBECONFIG ({probe.kubeconfig}) is not reachable"
    if probe.gpu_count() < 1:
        return "no node advertises an allocatable nvidia.com/gpu"
    if probe.namespace_exists():
        return f"the namespace {NAMESPACE} already exists; refusing to touch an existing deployment"
    if not probe.frames_present:
        return f"MOT17 frames not found at {FRAMES}"
    return None


def kubectl(
    *arguments: str, check: bool = True, environ: Mapping[str, str] = os.environ
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["kubectl", *arguments], capture_output=True, text=True, check=check, env=dict(environ))


def _gpu_count() -> int:
    template = "{range .items[*]}{.status.allocatable.nvidia\\.com/gpu}{\"\\n\"}{end}"
    output = kubectl("get", "nodes", "-o", f"jsonpath={template}", REQUEST_TIMEOUT, check=False).stdout
    return sum(int(line) for line in output.split() if line.isdigit())


def real_probe(environ: Mapping[str, str] = os.environ) -> ClusterProbe:
    return ClusterProbe(
        kubectl_installed=shutil.which("kubectl") is not None,
        kubeconfig=environ.get("KUBECONFIG"),
        reachable=lambda: kubectl("get", "--raw", "/readyz", REQUEST_TIMEOUT, check=False).returncode == 0,
        gpu_count=_gpu_count,
        namespace_exists=lambda: kubectl("get", "namespace", NAMESPACE, REQUEST_TIMEOUT, check=False).returncode == 0,
        frames_present=FRAMES.is_dir(),
    )
