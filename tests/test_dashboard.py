import json
import re
from pathlib import Path

import yaml
from prometheus_client import CollectorRegistry

from metrics.definitions import create_metrics
from metrics.queue import QUEUE_DEPTH_METRIC

ROOT = Path(__file__).resolve().parents[1]
GRAFANA = ROOT / "monitoring" / "grafana"
DASHBOARD_FILE = GRAFANA / "dashboards" / "openmot.json"
EXPECTED_PANELS = [
    "請求率",
    "API 延遲 (p95)",
    "5xx 比例",
    "工作結果",
    "工作耗時 (p50 / p95)",
    "影格吞吐 (FPS)",
    "佇列深度",
]


def _dashboard() -> dict:
    return json.loads(DASHBOARD_FILE.read_text(encoding="utf-8"))


def _panels() -> list[dict]:
    return [panel for panel in _dashboard()["panels"] if panel["type"] != "row"]


def _exposed_sample_names() -> set[str]:
    registry = CollectorRegistry()
    create_metrics(registry)
    names = {QUEUE_DEPTH_METRIC}
    for family in registry.collect():
        if family.type == "counter":
            names.add(f"{family.name}_total")
        elif family.type == "histogram":
            names |= {f"{family.name}_{suffix}" for suffix in ("bucket", "sum", "count")}
    return names


def _unknown_metrics(panel: dict) -> set[str]:
    used = {name for target in panel["targets"] for name in re.findall(r"\bopenmot_\w+", target["expr"])}
    return used - _exposed_sample_names()


def test_the_dashboard_has_the_seven_documented_panels_in_order() -> None:
    assert [panel["title"] for panel in _panels()] == EXPECTED_PANELS


def test_every_panel_has_a_query_and_reads_from_the_provisioned_datasource() -> None:
    datasources = yaml.safe_load((GRAFANA / "provisioning" / "datasources" / "prometheus.yml").read_text())
    uid = datasources["datasources"][0]["uid"]
    for panel in _panels():
        assert panel["targets"], panel["title"]
        assert all(target["expr"].strip() for target in panel["targets"]), panel["title"]
        assert panel["datasource"]["uid"] == uid, panel["title"]


def test_no_panel_queries_a_metric_the_services_do_not_expose() -> None:
    for panel in _panels():
        assert not _unknown_metrics(panel), f"panel {panel['title']!r} uses unknown metrics {_unknown_metrics(panel)}"


def test_the_panels_do_not_overlap() -> None:
    cells = [
        (panel["gridPos"]["x"] + dx, panel["gridPos"]["y"] + dy)
        for panel in _panels()
        for dx in range(panel["gridPos"]["w"])
        for dy in range(panel["gridPos"]["h"])
    ]
    assert len(cells) == len(set(cells))


def test_the_dashboard_has_a_stable_identity_so_a_reload_updates_it_in_place() -> None:
    dashboard = _dashboard()
    assert dashboard["uid"] == "openmot-overview" and dashboard["title"] == "openmot"
    assert dashboard["id"] is None


def test_the_grafana_image_carries_the_dashboard_to_the_folder_the_provider_reads() -> None:
    provider = yaml.safe_load((GRAFANA / "provisioning" / "dashboards" / "openmot.yml").read_text())
    folder = provider["providers"][0]["options"]["path"]
    dockerfile = (ROOT / "monitoring" / "grafana.Dockerfile").read_text()
    assert f"COPY monitoring/grafana/dashboards {folder}" in dockerfile
