import json
import re
from pathlib import Path

import yaml
from prometheus_client import CollectorRegistry

from jobs.settings import DEFAULT_API_METRICS_PORT, DEFAULT_WORKER_METRICS_PORT
from metrics.definitions import create_metrics

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "observability.md").read_text(encoding="utf-8")

SECTIONS_IN_ORDER = [
    "## 指標",
    "## worker 指標",
    "## 單程序假設",
    "## 啟動監控",
    "## 警報規則",
    "## dashboard",
    "## 驗證與限制",
]
DASHBOARD_FILE = ROOT / "monitoring" / "grafana" / "dashboards" / "openmot.json"
RULES_FILE = ROOT / "monitoring" / "rules" / "openmot.rules.yml"
OVERLAY_TEXT = (ROOT / "deploy" / "docker" / "docker-compose.monitoring.yml").read_text()
QUEUE_DEPTH_METRIC = "openmot_queue_depth"


def _documented_metrics() -> set[str]:
    return set(re.findall(r"^\| `(openmot_\w+)` \|", GUIDE, re.M))


def _exposed_metrics() -> set[str]:
    registry = CollectorRegistry()
    create_metrics(registry)
    names = {family.name + ("_total" if family.type == "counter" else "") for family in registry.collect()}
    return names | {QUEUE_DEPTH_METRIC}


def test_the_guide_has_its_sections_in_order() -> None:
    positions = [GUIDE.index(heading) for heading in SECTIONS_IN_ORDER]
    assert positions == sorted(positions)


def test_the_documented_metrics_are_exactly_the_ones_the_services_expose() -> None:
    assert _documented_metrics() == _exposed_metrics()


def test_the_guide_names_the_metrics_ports_and_their_defaults() -> None:
    assert "OPENMOT_API_METRICS_PORT" in GUIDE and str(DEFAULT_API_METRICS_PORT) in GUIDE
    assert "OPENMOT_WORKER_METRICS_PORT" in GUIDE and str(DEFAULT_WORKER_METRICS_PORT) in GUIDE


def test_the_guide_says_the_metrics_are_not_on_the_public_api() -> None:
    assert "對外" in GUIDE and "404" in GUIDE


def test_the_guide_says_what_the_queue_depth_does_not_count() -> None:
    assert "正在處理" in GUIDE and "不輸出" in GUIDE


def test_the_guide_says_worker_metrics_have_no_end_to_end_evidence_without_a_gpu() -> None:
    assert "沒有 GPU" in GUIDE and "無法端到端驗證" in GUIDE


def test_the_guide_says_when_the_worker_opens_its_metrics_port() -> None:
    assert "啟動成功" in GUIDE and "`up`" in GUIDE


def test_the_guide_states_the_single_process_assumption() -> None:
    assert "單一程序" in GUIDE and "multiprocess" in GUIDE


def test_the_documented_command_layers_the_overlay_that_exists() -> None:
    assert "docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up --build" in GUIDE
    assert (ROOT / "deploy" / "docker" / "docker-compose.yml").is_file()
    assert (ROOT / "deploy" / "docker" / "docker-compose.monitoring.yml").is_file()


def test_every_environment_variable_the_guide_names_for_the_overlay_is_one_the_overlay_reads() -> None:
    variables = {"GRAFANA_ADMIN_PASSWORD", "OPENMOT_PROMETHEUS_PORT", "OPENMOT_GRAFANA_PORT"}
    for variable in variables:
        assert variable in GUIDE and variable in OVERLAY_TEXT


def test_the_guide_explains_the_local_only_binding_and_the_baked_in_configuration() -> None:
    assert "127.0.0.1" in GUIDE and "沒有預設密碼" in GUIDE
    assert "--build" in GUIDE and "打包進映像" in GUIDE


def test_the_guide_tells_a_machine_without_a_gpu_how_to_start_without_the_worker() -> None:
    assert "up --build redis api prometheus grafana" in GUIDE


def _rule_names() -> set[str]:
    groups = yaml.safe_load(RULES_FILE.read_text())["groups"]
    return {rule["alert"] for group in groups for rule in group["rules"]}


def test_the_documented_alerts_are_exactly_the_rules_in_the_rule_file() -> None:
    documented = set(re.findall(r"^\| `(\w+)` \|", GUIDE.split("## 警報規則")[1], re.M))
    assert documented == _rule_names()


def test_the_guide_says_the_thresholds_are_uncalibrated_starting_values() -> None:
    assert "未經真實流量校準" in GUIDE and "起始值" in GUIDE


def test_the_guide_says_there_is_no_notification_channel() -> None:
    assert "Alertmanager" in GUIDE and "沒有接" in GUIDE


def test_the_guide_says_worker_down_fires_on_a_stack_without_a_worker() -> None:
    assert "WorkerDown" in GUIDE and "預期" in GUIDE


def test_the_documented_panels_are_exactly_the_panels_in_the_dashboard() -> None:
    section = GUIDE.split("## dashboard")[1]
    documented = re.findall(r"^\| ([^|`]+?) \| [^|]+ \|$", section, re.M)
    panels = json.loads(DASHBOARD_FILE.read_text(encoding="utf-8"))["panels"]
    assert [title for title in documented if title not in ("面板", "---")] == [panel["title"] for panel in panels]


def test_the_guide_says_how_to_open_the_dashboard_and_that_it_is_empty_without_traffic() -> None:
    section = GUIDE.split("## dashboard")[1]
    assert "openmot-overview" in section and "127.0.0.1:3000" in section
    assert "沒有流量" in section and "空" in section


def _limits_section() -> str:
    return GUIDE.split("## 驗證與限制")[1]


def test_the_limits_section_names_what_was_verified_end_to_end() -> None:
    section = _limits_section()
    for verified in ("Prometheus 抓取 api", "請求指標", "佇列深度", "Grafana 自動載入", "不對主機"):
        assert verified in section, verified


def test_the_limits_section_names_every_unverified_part() -> None:
    section = _limits_section()
    assert "worker" in section and "沒有 GPU" in section and "無法端到端驗證" in section
    assert "未經真實流量校準" in section
    assert "Kubernetes" in section and "不在範圍" in section
    assert "Alertmanager" in section
    assert "尚未在 GitHub" in section


def test_the_limits_section_does_not_hard_code_test_counts_or_timings() -> None:
    assert not re.search(r"\d+\s*(個測試|項測試|秒)", _limits_section())


def test_the_guide_gives_the_commands_that_test_the_rules() -> None:
    assert "promtool check rules monitoring/rules/openmot.rules.yml" in GUIDE
    assert "promtool test rules monitoring/rules/openmot.rules.test.yml" in GUIDE
