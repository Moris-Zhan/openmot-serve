"""Run the HTTP API against an in-memory store and a fake worker.

Every submitted job is "tracked" instantly: one box per frame, no GPU, Redis or Celery needed.
"""

import os
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout, write_result
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobStatus
from jobs.settings import load_settings
from tracking.motchallenge import ResultRow, format_result


def build_fake_app(storage_dir: Path, limits: UploadLimits) -> FastAPI:
    store = InMemoryJobStore()
    layout = StorageLayout(storage_dir)

    def run_job_instantly(job_id: str) -> None:
        store.transition(job_id, JobStatus.RUNNING, time.time())
        frame_count = len(list(layout.frames_dir(job_id).iterdir()))
        rows = tuple(ResultRow(frame, 1, 100.0, 100.0, 50.0, 100.0, 0.9) for frame in range(1, frame_count + 1))
        write_result(layout, job_id, format_result(rows))
        store.transition(job_id, JobStatus.SUCCEEDED, time.time())

    return create_app(store, layout, limits, run_job_instantly, lambda: None)


def main() -> None:
    settings = load_settings(os.environ)
    limits = UploadLimits(settings.max_upload_bytes, settings.max_unpacked_bytes)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(build_fake_app(settings.storage_dir, limits), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
