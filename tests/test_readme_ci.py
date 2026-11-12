import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())


def _roadmap_items(version: str) -> dict[str, bool]:
    section = README.split(f"### {version}")[1].split("\n### ")[0]
    return {line[6:]: line[3] == "x" for line in section.splitlines() if re.match(r"- \[[ x]\] ", line)}


def _item(fragment: str) -> bool:
    matches = [done for text, done in _roadmap_items("v0.4").items() if fragment in text]
    assert len(matches) == 1, f"expected exactly one roadmap item containing {fragment!r}"
    return matches[0]


def test_the_status_headline_reports_v0_4_ci_done_and_keeps_the_v0_3_caveat() -> None:
    headline = README.split("> **Status:")[1].split("\n")[0].split("**")[0]
    assert "v0.4" in headline and "CI" in headline
    assert "v0.3" in headline and "not yet verified" in headline


def test_the_status_links_the_ci_guide_and_the_observability_guide() -> None:
    status_line = README.split("> **Status:")[1].split("\n")[0]
    assert "docs/ci.md" in status_line
    assert "docs/observability.md" in status_line


def test_the_ci_and_publish_items_are_ticked() -> None:
    assert _item("GitHub Actions")
    assert _item("Publish the api and worker images")


def test_the_repository_layout_lists_the_workflow_directory() -> None:
    assert any(".github/workflows/" in line and "#" in line for line in README.splitlines())
    assert (ROOT / ".github" / "workflows" / "ci.yml").is_file()


def test_the_readme_describes_the_jobs_the_workflow_really_has() -> None:
    assert {"lint", "test", "build-api", "publish"} == set(WORKFLOW["jobs"])
    ci_row = next(line for line in README.splitlines() if line.startswith("| CI |"))
    assert "Lint" in ci_row and "test" in ci_row.lower() and "image" in ci_row.lower()
