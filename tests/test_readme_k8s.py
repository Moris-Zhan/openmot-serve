import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
GUIDE = (ROOT / "docs" / "kubernetes.md").read_text(encoding="utf-8")


def _roadmap_items(version: str) -> dict[str, bool]:
    section = README.split(f"### {version}")[1].split("\n### ")[0]
    return {line[6:]: line[3] == "x" for line in section.splitlines() if re.match(r"- \[[ x]\] ", line)}


def _item(version: str, fragment: str) -> bool:
    matches = [done for text, done in _roadmap_items(version).items() if fragment in text]
    assert len(matches) == 1, f"expected exactly one roadmap item containing {fragment!r}"
    return matches[0]


def test_the_status_line_reports_v0_3_and_that_it_is_not_verified_on_a_cluster() -> None:
    status_line = README.split("> **Status:")[1].split("\n")[0]
    headline = status_line.split("**")[0]
    assert "v0.3" in headline
    assert "not yet verified" in headline
    assert "docs/kubernetes.md" in status_line


def test_the_manifests_are_no_longer_described_as_planned() -> None:
    assert "k3s manifests (planned" not in README
    assert "Deployment architecture _(planned" not in README
    assert (ROOT / "deploy" / "k8s" / "overlays" / "k3s-single-node" / "kustomization.yaml").is_file()


def test_the_gpu_operator_is_not_claimed_as_managed() -> None:
    assert "k3s + NVIDIA GPU Operator" not in README
    assert "GPU Operator" in README and "not managed" in README
    assert "GPU Operator" not in README.split("flowchart LR")[1].split("```")[0]


def test_the_authored_manifests_are_ticked_and_the_unverified_gpu_scheduling_is_not() -> None:
    assert _item("v0.3", "k3s manifests")
    assert _item("v0.3", "Health checks and resource limits")
    assert not _item("v0.3", "GPU scheduling for the worker")


def test_the_live_cluster_verification_stays_open_while_the_guide_says_it_is_unverified() -> None:
    assert "未經實機驗證" in GUIDE
    assert not _item("v0.3", "live k3s cluster")


def test_the_layout_names_what_the_k8s_directory_holds() -> None:
    line = next(line for line in README.splitlines() if "k8s/" in line and "#" in line)
    assert "kustomize" in line and "planned" not in line
