import tempfile
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from app.upload import UploadLimits, UploadRejected, UploadTooLarge, unpack_upload
from jobs.layout import StorageError, StorageLayout, new_job_id, publish_frames, remove_job, staged_frames
from jobs.record import JobRecord, JobStatus, new_job
from jobs.store import JobStore
from metrics.definitions import Metrics
from metrics.http import MetricsMiddleware

Enqueue = Callable[[str], None]
PingQueue = Callable[[], None]


def _submit(
    file: UploadFile,
    store: JobStore,
    layout: StorageLayout,
    limits: UploadLimits,
    enqueue: Enqueue,
    clock: Callable[[], float],
) -> dict[str, str]:
    job_id = new_job_id()
    try:
        with staged_frames(layout) as staging:
            unpack_upload(file.file, staging, limits)
            publish_frames(layout, job_id, staging)
    except UploadTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except UploadRejected as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except StorageError as error:
        raise HTTPException(status_code=500, detail=f"cannot store the upload: {error}") from error
    store.create(new_job(job_id, clock()))
    try:
        enqueue(job_id)
    except Exception as error:
        remove_job(layout, job_id)
        store.transition(job_id, JobStatus.FAILED, clock(), reason="the job could not be put on the queue")
        raise HTTPException(status_code=503, detail="the job queue is unavailable, try again later") from error
    return {"job_id": job_id, "status": JobStatus.QUEUED.value}


def _known_job(store: JobStore, job_id: str) -> JobRecord:
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return record


def _status_body(record: JobRecord) -> dict[str, object]:
    return {
        "job_id": record.job_id,
        "status": record.status.value,
        "reason": record.reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _result_text(record: JobRecord, layout: StorageLayout) -> str:
    if record.status is JobStatus.FAILED:
        raise HTTPException(status_code=410, detail=f"job {record.job_id} failed: {record.reason}")
    if record.status is not JobStatus.SUCCEEDED:
        raise HTTPException(status_code=409, detail=f"job {record.job_id} is {record.status.value}, not finished yet")
    try:
        return layout.result_path(record.job_id).read_text()
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"result of job {record.job_id} is missing") from error


def _health_problems(layout: StorageLayout, ping_queue: PingQueue) -> list[str]:
    problems: list[str] = []
    try:
        ping_queue()
    except Exception as error:
        problems.append(f"queue backend is unavailable: {error}")
    try:
        layout.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=layout.root):
            pass
    except OSError as error:
        problems.append(f"storage is not writable: {error}")
    return problems


def create_app(
    store: JobStore,
    layout: StorageLayout,
    limits: UploadLimits,
    enqueue: Enqueue,
    ping_queue: PingQueue,
    clock: Callable[[], float] = time.time,
    metrics: Metrics | None = None,
) -> FastAPI:
    app = FastAPI(title="openmot-serve")
    if metrics is not None:
        app.add_middleware(MetricsMiddleware, metrics=metrics)

    @app.post("/jobs", status_code=202)
    def submit_job(file: UploadFile) -> dict[str, str]:
        return _submit(file, store, layout, limits, enqueue, clock)

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, object]:
        return _status_body(_known_job(store, job_id))

    @app.get("/jobs/{job_id}/result", response_class=PlainTextResponse)
    def job_result(job_id: str) -> str:
        return _result_text(_known_job(store, job_id), layout)

    @app.get("/health")
    def health() -> JSONResponse:
        problems = _health_problems(layout, ping_queue)
        if problems:
            return JSONResponse(status_code=503, content={"status": "unavailable", "problems": problems})
        return JSONResponse(content={"status": "ok"})

    return app
