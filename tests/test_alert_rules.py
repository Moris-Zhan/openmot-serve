import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from prometheus_client import CollectorRegistry

from metrics.definitions import create_metrics
from metrics.queue import QUEUE_DEPTH_METRIC

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "monitoring" / "rules"
RULES_FILE = RULES_DIR / "openmot.rules.yml"
RULE_TESTS_FILE = RULES_DIR / "openmot.rules.test.yml"
PROMETHEUS_CONFIG = ROOT / "monitoring" / "prometheus.yml"
EXPECTED_ALERTS = {"ApiDown", "WorkerDown", "HighJobFailureRatio", "JobQueueBacklog", "HighApiErrorRatio"}


def _alert_rules() -> list[dict]:
    groups = yaml.safe_load(RULES_FILE.read_text())["groups"]
    return [rule for group in groups for rule in group["rules"]]


def _exposed_metric_names() -> set[str]:
    registry = CollectorRegistry()
    create_metrics(registry)
    names = {family.name + ("_total" if family.type == "counter" else "") for family in registry.collect()}
    return names | {QUEUE_DEPTH_METRIC}


def _promtool(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["promtool", *arguments], capture_output=True, text=True, cwd=RULES_DIR)


@pytest.fixture
def promtool_available() -> None:
    if shutil.which("promtool") is None:
        pytest.skip("promtool is not installed (it ships in the Prometheus release tarball)")


def test_promtool_accepts_the_rule_file(promtool_available: None) -> None:
    result = _promtool("check", "rules", str(RULES_FILE))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_rule_unit_tests_pass(promtool_available: None) -> None:
    result = _promtool("test", "rules", str(RULE_TESTS_FILE))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_file_defines_exactly_the_documented_alerts() -> None:
    assert {rule["alert"] for rule in _alert_rules()} == EXPECTED_ALERTS


def test_every_alert_waits_before_firing_and_says_what_it_means() -> None:
    for rule in _alert_rules():
        assert rule["for"], rule["alert"]
        assert rule["annotations"]["summary"].strip(), rule["alert"]
        assert rule["labels"]["severity"] in {"warning", "critical"}, rule["alert"]


def test_every_alert_has_a_firing_and_a_quiet_unit_test() -> None:
    tests = yaml.safe_load(RULE_TESTS_FILE.read_text())["tests"]
    cases = [case for test in tests for case in test["alert_rule_test"]]
    for alert in EXPECTED_ALERTS:
        outcomes = {bool(case["exp_alerts"]) for case in cases if case["alertname"] == alert}
        assert outcomes == {True, False}, f"{alert} needs both a firing and a quiet test case"


def test_every_metric_a_rule_uses_is_one_the_services_expose() -> None:
    exposed = _exposed_metric_names()
    for rule in _alert_rules():
        used = set(re.findall(r"\bopenmot_\w+", rule["expr"]))
        assert used <= exposed, f"{rule['alert']} uses unknown metrics {sorted(used - exposed)}"


def test_the_up_rules_name_the_jobs_prometheus_scrapes() -> None:
    scrape_configs = yaml.safe_load(PROMETHEUS_CONFIG.read_text())["scrape_configs"]
    scrape_jobs = {job["job_name"] for job in scrape_configs}
    used = {name for rule in _alert_rules() for name in re.findall(r'job="([\w-]+)"', rule["expr"])}
    assert used == scrape_jobs
