import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, UploadFile

from app.upload import UploadLimits, UploadRejected, UploadTooLarge, unpack_upload
from jobs.layout import StorageError, StorageLayout, new_job_id, publish_frames, remove_job, staged_frames
from jobs.record import JobStatus, new_job
from jobs.store import JobStore

Enqueue = Callable[[str], None]


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


def create_app(
    store: JobStore,
    layout: StorageLayout,
    limits: UploadLimits,
    enqueue: Enqueue,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    app = FastAPI(title="openmot-serve")

    @app.post("/jobs", status_code=202)
    def submit_job(file: UploadFile) -> dict[str, str]:
        return _submit(file, store, layout, limits, enqueue, clock)

    return app
