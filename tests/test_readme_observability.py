import re
from pathlib import Path

from jobs.settings import DEFAULT_API_METRICS_PORT, DEFAULT_WORKER_METRICS_PORT

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _roadmap_v0_4() -> dict[str, bool]:
    section = README.split("### v0.4")[1].split("\n### ")[0]
    return {line[6:]: line[3] == "x" for line in section.splitlines() if re.match(r"- \[[ x]\] ", line)}


def _item(fragment: str) -> tuple[str, bool]:
    matches = [(text, done) for text, done in _roadmap_v0_4().items() if fragment in text]
    assert len(matches) == 1, f"expected exactly one roadmap item containing {fragment!r}"
    return matches[0]


def _status() -> str:
    return README.split("> **Status:")[1].split("\n")[0]


def test_the_api_table_no_longer_lists_a_public_metrics_endpoint() -> None:
    api_section = README.split("## API")[1].split("\n## ")[0]
    table_rows = [line for line in api_section.splitlines() if line.startswith("| `")]
    assert not any("/metrics" in row for row in table_rows)


def test_the_readme_explains_that_metrics_live_on_separate_unpublished_ports() -> None:
    api_section = README.split("## API")[1].split("\n## ")[0]
    assert "docs/observability.md" in api_section
    assert str(DEFAULT_API_METRICS_PORT) in api_section and str(DEFAULT_WORKER_METRICS_PORT) in api_section
    assert "not published" in api_section


def test_the_status_headline_reports_observability_done_and_the_status_links_its_guide() -> None:
    headline = _status().split("**")[0]
    assert "v0.4" in headline and "observability" in headline.lower() and "planned" not in headline
    assert "docs/observability.md" in _status()


def test_the_status_states_what_has_not_been_verified() -> None:
    status = _status()
    assert "worker" in status and "GPU" in status
    assert "not calibrated" in status
    assert "Kubernetes" in status


def test_the_metrics_and_dashboard_items_are_ticked_with_their_limits_stated() -> None:
    metrics_text, metrics_done = _item("Prometheus metrics")
    dashboard_text, dashboard_done = _item("Grafana dashboard and alert rules")
    assert metrics_done and dashboard_done
    assert "in-process" in metrics_text
    assert "not calibrated" in dashboard_text and "Alertmanager" in dashboard_text


def test_the_kubernetes_monitoring_manifests_stay_an_open_roadmap_item() -> None:
    _, done = _item("Kubernetes monitoring manifests")
    assert not done


def test_the_layout_no_longer_calls_the_monitoring_folder_planned() -> None:
    line = next(line for line in README.splitlines() if line.startswith("├── monitoring/"))
    assert "planned" not in line and "#" in line
    assert (ROOT / "monitoring").is_dir()


def test_the_architecture_heading_separates_the_compose_overlay_from_the_planned_manifests() -> None:
    heading = next(line for line in README.splitlines() if line.startswith("## Deployment architecture"))
    assert "monitoring planned, v0.4" not in heading
    assert "Kubernetes" in heading and "planned" in heading
