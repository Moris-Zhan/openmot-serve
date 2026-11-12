from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore
from jobs.record import JobStatus, new_job
from tracking.bytetrack import TrackerParams
from tracking.config import RunMetadata, load_tracker_params, metadata_path_for
from tracking.run import main
from tracking.yolox import WEIGHTS_ID, WEIGHTS_SHA256
from worker.processing import ProcessOutcome, WorkerContext, process_job


def _white_box_frame(index: int) -> np.ndarray:
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[100:200, 100 + 3 * index : 140 + 3 * index] = 255
    image[220:300, 400 - 2 * index : 440 - 2 * index] = 255
    return image


def _detect_white_boxes(image: np.ndarray) -> np.ndarray:
    mask = (image[:, :, 0] > 200).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = [
        [x, y, x + width, y + height, 0.9 - 0.05 * label]
        for label, (x, y, width, height, _) in enumerate(stats[1:])
    ]
    return np.array(boxes, dtype=float) if boxes else np.zeros((0, 5))


def _write_frames(directory: Path, count: int = 12) -> None:
    directory.mkdir(parents=True)
    for index in range(count):
        cv2.imwrite(str(directory / f"{index + 1:06d}.png"), _white_box_frame(index))


def _command_line_run(frames: Path, output: Path) -> None:
    def loader(weights: object, precision: object, device: object) -> Callable:
        return _detect_white_boxes

    assert main([str(frames), "--output", str(output)], load_detect_frame=loader) == 0


def _service_run(frames: Path, layout: StorageLayout, job_id: str) -> None:
    layout.job_dir(job_id).mkdir(parents=True)
    frames.rename(layout.frames_dir(job_id))
    store = InMemoryJobStore()
    store.create(new_job(job_id, now=1.0))
    metadata = RunMetadata(TrackerParams(), WEIGHTS_ID, WEIGHTS_SHA256, "fp16")
    context = WorkerContext(store, layout, _detect_white_boxes, metadata, "worker-a", 60)
    assert process_job(context, job_id) is ProcessOutcome.SUCCEEDED
    record = store.get(job_id)
    assert record is not None and record.status is JobStatus.SUCCEEDED


def test_the_service_and_the_command_line_write_identical_bytes(tmp_path: Path) -> None:
    command_frames = tmp_path / "cli-frames"
    service_frames = tmp_path / "service-frames"
    _write_frames(command_frames)
    _write_frames(service_frames)
    command_output = tmp_path / "cli-out" / "result.txt"
    layout = StorageLayout(tmp_path / "storage")

    _command_line_run(command_frames, command_output)
    _service_run(service_frames, layout, "job-1")

    command_result = command_output.read_bytes()
    assert command_result.count(b"\n") >= 12
    assert layout.result_path("job-1").read_bytes() == command_result
    assert metadata_path_for(layout.result_path("job-1")).read_bytes() == metadata_path_for(command_output).read_bytes()


def test_the_result_has_several_tracks_so_the_comparison_means_something(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    _write_frames(frames)
    output = tmp_path / "out" / "result.txt"
    _command_line_run(frames, output)
    identities = {line.split(",")[1] for line in output.read_text().splitlines()}
    assert len(identities) >= 2


def test_the_service_default_tracker_parameters_are_the_command_line_defaults() -> None:
    assert load_tracker_params(None) == TrackerParams()
