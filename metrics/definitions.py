from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram

REQUEST_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
JOB_DURATION_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600, 1200, 1500)


@dataclass(frozen=True)
class Metrics:
    http_requests: Counter
    http_request_duration: Histogram
    jobs: Counter
    job_duration: Histogram
    frames_processed: Counter


def create_metrics(registry: CollectorRegistry) -> Metrics:
    return Metrics(
        http_requests=Counter(
            "openmot_http_requests_total",
            "HTTP requests handled by the api, by route template and status code.",
            ["method", "route", "status"],
            registry=registry,
        ),
        http_request_duration=Histogram(
            "openmot_http_request_duration_seconds",
            "Time the api spent handling an HTTP request, by route template.",
            ["method", "route"],
            buckets=REQUEST_LATENCY_BUCKETS,
            registry=registry,
        ),
        jobs=Counter(
            "openmot_jobs_total",
            "Tracking jobs the worker finished handling, by outcome.",
            ["outcome"],
            registry=registry,
        ),
        job_duration=Histogram(
            "openmot_job_duration_seconds",
            "Processing time of jobs that were run (skipped jobs are not counted).",
            buckets=JOB_DURATION_BUCKETS,
            registry=registry,
        ),
        frames_processed=Counter(
            "openmot_frames_processed_total",
            "Frames of the jobs that succeeded.",
            registry=registry,
        ),
    )
