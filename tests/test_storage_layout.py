import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jobs.layout import (
    InvalidJobId,
    StorageError,
    StorageLayout,
    new_job_id,
    publish_frames,
    remove_job,
    staged_frames,
    write_result,
)

BAD_IDS = ["", ".", "..", "../outside", "a/b", "/etc/passwd", "a\\b", "x" * 65, "has space", "nul\x00l"]


def _layout(tmp_path: Path) -> StorageLayout:
    return StorageLayout(tmp_path / "storage")


def _fill(directory: Path, count: int = 3) -> None:
    for number in range(1, count + 1):
        (directory / f"{number:06d}.jpg").write_bytes(b"frame-%d" % number)


def test_paths_follow_the_job_folder_convention(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert layout.job_dir("job-1") == layout.root / "jobs" / "job-1"
    assert layout.frames_dir("job-1") == layout.root / "jobs" / "job-1" / "frames"
    assert layout.result_path("job-1") == layout.root / "jobs" / "job-1" / "result.txt"


@pytest.mark.parametrize("job_id", BAD_IDS)
def test_an_id_that_could_leave_the_storage_root_is_refused(tmp_path: Path, job_id: str) -> None:
    layout = _layout(tmp_path)
    for operation in (layout.job_dir, layout.frames_dir, layout.result_path):
        with pytest.raises(InvalidJobId):
            operation(job_id)
    with pytest.raises(InvalidJobId):
        remove_job(layout, job_id)
    with pytest.raises(InvalidJobId):
        write_result(layout, job_id, "x")


def test_generated_ids_are_valid_and_unique(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    ids = {new_job_id() for _ in range(200)}
    assert len(ids) == 200
    for job_id in ids:
        assert layout.job_dir(job_id).parent == layout.root / "jobs"


def test_the_layout_is_immutable(tmp_path: Path) -> None:
    with pytest.raises(FrozenInstanceError):
        _layout(tmp_path).root = tmp_path  # type: ignore[misc]


def test_staging_lives_on_the_same_filesystem_as_the_jobs(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with staged_frames(layout) as staging:
        assert staging.parent == layout.root / "staging"
        assert os.stat(staging).st_dev == os.stat(layout.root).st_dev


def test_published_frames_appear_whole_and_the_staging_folder_is_gone(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with staged_frames(layout) as staging:
        _fill(staging)
        published = publish_frames(layout, "job-1", staging)
        assert not staging.exists()
    assert published == layout.frames_dir("job-1")
    assert sorted(path.name for path in published.iterdir()) == ["000001.jpg", "000002.jpg", "000003.jpg"]
    assert list((layout.root / "staging").iterdir()) == []


def test_publishing_twice_for_one_job_keeps_the_first(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with staged_frames(layout) as staging:
        _fill(staging, 2)
        publish_frames(layout, "job-1", staging)
    with staged_frames(layout) as staging:
        _fill(staging, 5)
        with pytest.raises(StorageError, match="job-1"):
            publish_frames(layout, "job-1", staging)
    assert len(list(layout.frames_dir("job-1").iterdir())) == 2


def test_a_job_folder_that_points_outside_the_root_is_never_written_through(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (layout.root / "jobs").mkdir(parents=True)
    (layout.root / "jobs" / "evil").symlink_to(outside, target_is_directory=True)
    with staged_frames(layout) as staging:
        _fill(staging)
        with pytest.raises(StorageError):
            publish_frames(layout, "evil", staging)
    assert list(outside.iterdir()) == []
    with pytest.raises(StorageError):
        write_result(layout, "evil", "tracks")
    assert list(outside.iterdir()) == []


def test_a_failed_publish_leaves_no_half_made_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)

    def broken_rename(source: object, destination: object) -> None:
        raise OSError("disk on fire")

    with staged_frames(layout) as staging:
        _fill(staging)
        monkeypatch.setattr("jobs.layout.os.rename", broken_rename)
        with pytest.raises(StorageError, match="disk on fire"):
            publish_frames(layout, "job-1", staging)
    assert not layout.job_dir("job-1").exists()
    assert list((layout.root / "staging").iterdir()) == []


def test_the_staging_folder_is_removed_when_the_body_fails(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(RuntimeError, match="bad upload"):
        with staged_frames(layout) as staging:
            _fill(staging)
            raise RuntimeError("bad upload")
    assert list((layout.root / "staging").iterdir()) == []


def test_a_result_is_written_whole_with_no_temporary_files(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    write_result(layout, "job-1", "1,1,10,10,5,5,0.9,-1,-1,-1\n")
    assert layout.result_path("job-1").read_text() == "1,1,10,10,5,5,0.9,-1,-1,-1\n"
    assert [path.name for path in layout.job_dir("job-1").iterdir()] == ["result.txt"]


def test_a_failed_result_write_leaves_no_result_and_no_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)

    def broken_replace(source: object, destination: object) -> None:
        raise OSError("no space left")

    monkeypatch.setattr("tracking.files.os.replace", broken_replace)
    with pytest.raises(OSError, match="no space left"):
        write_result(layout, "job-1", "tracks")
    assert not layout.result_path("job-1").exists()
    assert list(layout.job_dir("job-1").iterdir()) == []


def test_a_failed_overwrite_keeps_the_previous_result_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    write_result(layout, "job-1", "old\n")
    monkeypatch.setattr("tracking.files.os.replace", lambda source, destination: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        write_result(layout, "job-1", "new\n")
    assert layout.result_path("job-1").read_text() == "old\n"


def test_removing_a_job_deletes_only_that_job(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    for job_id in ("job-1", "job-2"):
        with staged_frames(layout) as staging:
            _fill(staging)
            publish_frames(layout, job_id, staging)
        write_result(layout, job_id, "tracks")
    remove_job(layout, "job-1")
    assert not layout.job_dir("job-1").exists()
    assert layout.result_path("job-2").read_text() == "tracks"
    assert len(list(layout.frames_dir("job-2").iterdir())) == 3


def test_removing_an_unknown_job_is_a_no_op(tmp_path: Path) -> None:
    remove_job(_layout(tmp_path), "never-existed")
