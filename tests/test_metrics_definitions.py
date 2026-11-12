import subprocess
import sys
import tomllib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from metrics.definitions import Metrics, create_metrics

ROOT = Path(__file__).resolve().parents[1]


def _families(registry: CollectorRegistry) -> dict[str, str]:
    return {family.name: family.type for family in registry.collect()}


def _samples(registry: CollectorRegistry, family_name: str) -> list:
    return [sample for family in registry.collect() if family.name == family_name for sample in family.samples]


def test_the_registry_holds_exactly_the_documented_metric_families() -> None:
    registry = CollectorRegistry()
    create_metrics(registry)
    assert _families(registry) == {
        "openmot_http_requests": "counter",
        "openmot_http_request_duration_seconds": "histogram",
        "openmot_jobs": "counter",
        "openmot_job_duration_seconds": "histogram",
        "openmot_frames_processed": "counter",
    }


def test_the_http_metrics_are_labelled_by_method_route_and_status() -> None:
    registry = CollectorRegistry()
    metrics = create_metrics(registry)
    metrics.http_requests.labels(method="GET", route="/health", status="200").inc()
    metrics.http_request_duration.labels(method="GET", route="/health").observe(0.01)
    counted = _samples(registry, "openmot_http_requests")
    assert {tuple(sorted(sample.labels)) for sample in counted} == {("method", "route", "status")}
    timed = _samples(registry, "openmot_http_request_duration_seconds")
    assert {tuple(sorted(label for label in sample.labels if label != "le")) for sample in timed} == {
        ("method", "route")
    }


def test_job_outcomes_are_labelled_only_by_outcome() -> None:
    registry = CollectorRegistry()
    metrics = create_metrics(registry)
    metrics.jobs.labels(outcome="succeeded").inc()
    assert {tuple(sample.labels) for sample in _samples(registry, "openmot_jobs")} == {("outcome",)}


def _bucket_bounds(registry: CollectorRegistry, family_name: str) -> list[float]:
    bounds = {float(sample.labels["le"]) for sample in _samples(registry, family_name) if "le" in sample.labels}
    return sorted(bound for bound in bounds if bound != float("inf"))


def test_the_request_latency_buckets_span_five_milliseconds_to_ten_seconds() -> None:
    registry = CollectorRegistry()
    metrics = create_metrics(registry)
    metrics.http_request_duration.labels(method="GET", route="/health").observe(0.01)
    bounds = _bucket_bounds(registry, "openmot_http_request_duration_seconds")
    assert bounds[0] == 0.005 and bounds[-1] == 10


def test_the_job_duration_buckets_reach_the_default_task_time_limit() -> None:
    registry = CollectorRegistry()
    create_metrics(registry)
    bounds = _bucket_bounds(registry, "openmot_job_duration_seconds")
    assert bounds[0] == 1 and bounds[-1] == 1500


def test_two_registries_do_not_share_state() -> None:
    first, second = CollectorRegistry(), CollectorRegistry()
    first_metrics = create_metrics(first)
    create_metrics(second)
    first_metrics.frames_processed.inc(5)
    assert _samples(first, "openmot_frames_processed")[0].value == 5
    assert _samples(second, "openmot_frames_processed")[0].value == 0


def test_the_metrics_bundle_is_immutable() -> None:
    metrics = create_metrics(CollectorRegistry())
    assert isinstance(metrics, Metrics)
    with pytest.raises(FrozenInstanceError):
        metrics.jobs = metrics.frames_processed  # type: ignore[misc]


def test_the_metrics_package_never_loads_torch() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import sys, metrics.definitions; assert 'torch' not in sys.modules"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_metrics_package_is_shipped_in_the_wheel_and_both_images() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "metrics*" in project["tool"]["setuptools"]["packages"]["find"]["include"]
    for name in ("api.Dockerfile", "worker.Dockerfile"):
        assert "COPY metrics ./metrics" in (ROOT / "deploy" / "docker" / name).read_text()


def test_prometheus_client_is_pinned_identically_for_the_service_and_the_api_image() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = [line for line in project["optional-dependencies"]["service"] if line.startswith("prometheus_client")]
    assert len(pins) == 1 and "==" in pins[0]
    api_lines = (ROOT / "deploy" / "docker" / "api-requirements.txt").read_text().split()
    assert pins[0] in api_lines
