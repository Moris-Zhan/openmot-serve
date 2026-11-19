import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
JOBS = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"]


def _section() -> str:
    return README.split("## CI/CD pipeline")[1].split("\n## ")[0]


def _diagram() -> str:
    return _section().split("```mermaid")[1].split("```")[0]


def _job_ids(diagram: str) -> dict[str, str]:
    # A job node reads `id["job-name<br/>detail"]`; the first label line is the job name.
    nodes = re.findall(r'^\s*(\w+)\["([^"<]+)(?:<br/>[^"]*)?"\]', diagram, re.M)
    return {name: node_id for node_id, name in nodes if name in JOBS}


def _edges(diagram: str) -> set[tuple[str, str]]:
    return set(re.findall(r"(\w+)\s*-->\s*(\w+)", diagram))


def test_the_diagram_shows_every_job_of_the_workflow() -> None:
    assert set(_job_ids(_diagram())) == set(JOBS)


def test_the_diagram_draws_every_needs_edge_of_the_workflow() -> None:
    diagram = _diagram()
    ids = _job_ids(diagram)
    expected = {(ids[needed], ids[name]) for name, job in JOBS.items() for needed in job.get("needs", [])}
    assert expected, "the workflow has no needs edge to check"
    assert expected <= _edges(diagram)


def test_the_section_states_the_publish_condition_and_the_permission() -> None:
    section = _section()
    assert "packages: write" in section
    assert "main" in section and "push" in section
