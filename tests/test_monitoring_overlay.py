import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from jobs.settings import DEFAULT_API_METRICS_PORT, DEFAULT_WORKER_METRICS_PORT

ROOT = Path(__file__).resolve().parents[1]
DOCKER_DIR = ROOT / "deploy" / "docker"
BASE_FILE = DOCKER_DIR / "docker-compose.yml"
OVERLAY_FILE = DOCKER_DIR / "docker-compose.monitoring.yml"
MONITORING = ROOT / "monitoring"
PASSWORD_VARIABLE = "GRAFANA_ADMIN_PASSWORD"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


OVERLAY = _load(OVERLAY_FILE)
BASE = _load(BASE_FILE)


def _published_ports(compose: dict) -> list[str]:
    return [str(port) for service in compose["services"].values() for port in service.get("ports", [])]


def test_the_overlay_adds_exactly_prometheus_and_grafana_and_leaves_the_base_services_alone() -> None:
    assert set(OVERLAY["services"]) == {"prometheus", "grafana"}
    assert set(BASE["services"]).isdisjoint(OVERLAY["services"])


def test_prometheus_and_grafana_are_reachable_from_the_local_host_only() -> None:
    for name in ("prometheus", "grafana"):
        ports = OVERLAY["services"][name]["ports"]
        assert ports and all(str(port).startswith("127.0.0.1:") for port in ports), name


def test_the_service_metrics_ports_are_never_published_to_the_host() -> None:
    published = _published_ports(BASE) + _published_ports(OVERLAY)
    for port in (DEFAULT_API_METRICS_PORT, DEFAULT_WORKER_METRICS_PORT):
        assert not any(str(port) in mapping for mapping in published), port


def test_the_grafana_password_is_required_and_has_no_default() -> None:
    value = OVERLAY["services"]["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"]
    assert value.startswith(f"${{{PASSWORD_VARIABLE}:?")
    assert ":-" not in value and "admin" not in value.replace(PASSWORD_VARIABLE, "").lower()


def test_the_images_are_built_from_pinned_bases_with_the_configuration_inside() -> None:
    for name, dockerfile in (("prometheus", "prometheus.Dockerfile"), ("grafana", "grafana.Dockerfile")):
        build = OVERLAY["services"][name]["build"]
        assert (DOCKER_DIR / build["context"]).resolve() == ROOT
        assert build["dockerfile"] == f"monitoring/{dockerfile}"
        lines = (MONITORING / dockerfile).read_text().splitlines()
        bases = [line.split()[1] for line in lines if line.startswith("FROM ")]
        assert len(bases) == 1 and re.fullmatch(r"[\w./-]+:v?\d+\.\d+\.\d+", bases[0]), bases
        assert any(line.startswith("COPY ") for line in lines)
        assert not any(line.startswith("ADD ") for line in lines)


def test_no_service_mounts_a_host_path() -> None:
    for service in OVERLAY["services"].values():
        for volume in service.get("volumes", []):
            source = volume.split(":")[0] if isinstance(volume, str) else volume["source"]
            assert not source.startswith((".", "/", "~")), source


def test_prometheus_scrapes_the_api_and_worker_metrics_ports() -> None:
    config = _load(MONITORING / "prometheus.yml")
    targets = {job["job_name"]: job["static_configs"][0]["targets"] for job in config["scrape_configs"]}
    assert targets == {
        "openmot-api": [f"api:{DEFAULT_API_METRICS_PORT}"],
        "openmot-worker": [f"worker:{DEFAULT_WORKER_METRICS_PORT}"],
    }


def test_prometheus_loads_the_rules_that_the_image_contains() -> None:
    config = _load(MONITORING / "prometheus.yml")
    dockerfile = (MONITORING / "prometheus.Dockerfile").read_text()
    assert config["rule_files"] == ["/etc/prometheus/rules/*.rules.yml"]
    assert "/etc/prometheus/rules" in dockerfile
    assert list((MONITORING / "rules").glob("*.rules.yml"))


def test_grafana_provisioning_points_at_prometheus_and_the_dashboard_folder() -> None:
    source = _load(MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus.yml")
    (datasource,) = source["datasources"]
    assert datasource["type"] == "prometheus" and datasource["url"] == "http://prometheus:9090"
    provider = _load(MONITORING / "grafana" / "provisioning" / "dashboards" / "openmot.yml")
    assert provider["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"
    assert "/etc/grafana/provisioning" in (MONITORING / "grafana.Dockerfile").read_text()


def _compose_config(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(BASE_FILE), "-f", str(OVERLAY_FILE), "config"],
        capture_output=True,
        text=True,
        env=environment,
    )


@pytest.fixture
def compose_available() -> None:
    if shutil.which("docker") is None:
        pytest.skip("the docker CLI is not installed")
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("the docker compose plugin is not available")


def test_expanding_without_the_grafana_password_fails_and_names_the_variable(compose_available: None) -> None:
    environment = {key: value for key, value in os.environ.items() if key != PASSWORD_VARIABLE}
    result = _compose_config(environment)
    assert result.returncode != 0
    assert PASSWORD_VARIABLE in result.stderr


def test_expanding_with_the_grafana_password_succeeds(compose_available: None) -> None:
    result = _compose_config({**os.environ, PASSWORD_VARIABLE: "a-test-password"})
    assert result.returncode == 0, result.stderr
    expanded = yaml.safe_load(result.stdout)
    assert {"redis", "api", "worker", "prometheus", "grafana"} <= set(expanded["services"])
