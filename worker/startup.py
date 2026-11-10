from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jobs.layout import StorageLayout
from jobs.settings import Settings
from jobs.store import JobStore
from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata
from tracking.env_check import CheckOutcome, format_outcome
from tracking.sequence import DetectFrame
from worker.processing import WorkerContext

WORKER_DEVICE = "cuda"


class StartupError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartupDependencies:
    check_environment: Callable[[], CheckOutcome]
    load_detect_frame: Callable[[Path, str, str], DetectFrame]
    make_store: Callable[[Settings], JobStore]


def start_worker(settings: Settings, dependencies: StartupDependencies, owner: str) -> WorkerContext:
    from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256

    outcome = dependencies.check_environment()
    if not outcome.passed:
        raise StartupError(f"environment check failed:\n{format_outcome(outcome)}")
    try:
        detect_frame = dependencies.load_detect_frame(settings.weights, settings.precision, WORKER_DEVICE)
    except Exception as error:
        raise StartupError(f"cannot load the detector from {settings.weights}: {error}") from error
    return WorkerContext(
        store=dependencies.make_store(settings),
        layout=StorageLayout(settings.storage_dir),
        detect_frame=detect_frame,
        metadata=RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, settings.precision),
        owner=owner,
        lock_timeout_seconds=settings.lock_timeout_seconds,
        time_limit_seconds=settings.task_time_limit_seconds,
    )
