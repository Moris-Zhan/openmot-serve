import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "ci.md").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())

SECTIONS_IN_ORDER = ["## 工作流程", "## 在本機重現", "## 發佈的映像", "## 權限", "## 常見失敗", "## GPU 測試"]


def _documented_jobs() -> set[str]:
    return set(re.findall(r"^\| `([\w-]+)` \|", GUIDE, re.M))


def _run_commands(job_name: str) -> list[str]:
    return [step["run"] for step in WORKFLOW["jobs"][job_name]["steps"] if "run" in step]


def test_the_guide_has_its_sections_in_order() -> None:
    positions = [GUIDE.index(heading) for heading in SECTIONS_IN_ORDER]
    assert positions == sorted(positions)


def test_the_documented_jobs_are_exactly_the_jobs_in_the_workflow() -> None:
    assert _documented_jobs() == set(WORKFLOW["jobs"])


def test_the_local_commands_are_the_ones_the_workflow_runs() -> None:
    assert "ruff check ." in _run_commands("lint")[-1]
    assert "ruff check ." in GUIDE
    assert "python -m pytest" in _run_commands("test")[-1]
    assert "python -m pytest" in GUIDE
    smoke = _run_commands("build-api")[-1]
    assert smoke in GUIDE


def test_the_documented_image_build_matches_the_workflow_build() -> None:
    build = next(step for step in WORKFLOW["jobs"]["build-api"]["steps"] if "build-push-action" in step.get("uses", ""))
    assert f"docker build -f {build['with']['file']} -t {build['with']['tags']} ." in GUIDE


def test_the_guide_names_the_published_images_and_their_tags() -> None:
    for image in ("openmot-api", "openmot-worker"):
        assert f"ghcr.io/<owner>/{image}" in GUIDE
    assert "`main`" in GUIDE and "commit SHA" in GUIDE


def test_the_guide_points_to_the_tool_versions_and_says_gpu_tests_are_not_run() -> None:
    assert "docs/kubernetes.md" in GUIDE
    assert "-m gpu" in GUIDE
    assert "不在 CI" in GUIDE


def test_the_guide_is_honest_that_publishing_from_main_has_not_run_yet() -> None:
    assert "尚未在 `main` 上實際執行" in GUIDE


def test_the_guide_records_the_kubectl_mismatch_the_first_real_run_found() -> None:
    assert "v1.37.2" in GUIDE and "v1.31.4" in GUIDE
