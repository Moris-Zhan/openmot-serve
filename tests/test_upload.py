import io
import struct
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.upload import UploadLimits, UploadRejected, UploadTooLarge, unpack_upload
from tracking.sequence import list_frames

LIMITS = UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=2_000_000)


def _jpeg(seed: int, size: int = 16) -> bytes:
    rng = np.random.default_rng(seed)
    ok, encoded = cv2.imencode(".jpg", rng.integers(0, 255, (size, size, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    ok, encoded = cv2.imencode(".png", rng.integers(0, 255, (16, 16, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def _archive(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _frames(count: int = 5, start: int = 1) -> dict[str, bytes]:
    return {f"{number:06d}.jpg": _jpeg(number) for number in range(start, start + count)}


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    directory = tmp_path / "staging"
    directory.mkdir()
    return directory


def _unpack(data: bytes, staging: Path, limits: UploadLimits = LIMITS) -> int:
    return unpack_upload(io.BytesIO(data), staging, limits)


def _assert_nothing_left(staging: Path) -> None:
    assert list(staging.iterdir()) == []
    assert sorted(path.name for path in staging.parent.iterdir()) == ["staging"]


def test_a_valid_archive_is_unpacked_frame_for_frame(staging: Path) -> None:
    frames = _frames(5)
    assert _unpack(_archive(frames), staging) == 5
    unpacked = list_frames(staging)
    assert [path.name for path in unpacked] == sorted(frames)
    assert all(path.read_bytes() == frames[path.name] for path in unpacked)
    assert sorted(path.name for path in staging.parent.iterdir()) == ["staging"]


def test_png_frames_and_upper_case_suffixes_are_accepted(staging: Path) -> None:
    entries = {"000001.PNG": _png(1), "000002.png": _png(2), "000003.JPG": _jpeg(3)}
    assert _unpack(_archive(entries), staging) == 3


def test_stored_and_deflated_archives_both_work(staging: Path) -> None:
    assert _unpack(_archive(_frames(3), zipfile.ZIP_STORED), staging) == 3


def test_something_that_is_not_a_zip_is_rejected(staging: Path) -> None:
    with pytest.raises(UploadRejected, match="not a zip archive"):
        _unpack(b"definitely not a zip file", staging)
    _assert_nothing_left(staging)


def test_an_empty_archive_has_no_frames(staging: Path) -> None:
    with pytest.raises(UploadRejected, match="no frames"):
        _unpack(_archive({}), staging)
    _assert_nothing_left(staging)


def test_an_archive_without_images_is_rejected_and_names_the_offender(staging: Path) -> None:
    with pytest.raises(UploadRejected, match="notes.txt"):
        _unpack(_archive({**_frames(2), "notes.txt": b"hello"}), staging)
    _assert_nothing_left(staging)


@pytest.mark.parametrize(
    "name",
    ["../evil.jpg", "../../evil.jpg", "/abs/evil.jpg", "sub/../../evil.jpg", "..\\evil.jpg", "C:\\evil.jpg"],
)
def test_paths_that_escape_the_target_are_rejected_and_nothing_is_written(
    staging: Path, tmp_path: Path, name: str
) -> None:
    with pytest.raises(UploadRejected, match="path|folder"):
        _unpack(_archive({"000001.jpg": _jpeg(1), name: _jpeg(2)}), staging)
    _assert_nothing_left(staging)
    assert not (tmp_path / "evil.jpg").exists()
    assert not (tmp_path.parent / "evil.jpg").exists()


@pytest.mark.parametrize("name", ["frames/000001.jpg", "frames/", "./000001.jpg", "__MACOSX/._000001.jpg"])
def test_subfolders_and_folder_entries_are_rejected(staging: Path, name: str) -> None:
    with pytest.raises(UploadRejected, match="folder"):
        _unpack(_archive({name: _jpeg(1)}), staging)
    _assert_nothing_left(staging)


def test_non_consecutive_frame_numbers_are_rejected(staging: Path) -> None:
    entries = {"000001.jpg": _jpeg(1), "000002.jpg": _jpeg(2), "000004.jpg": _jpeg(4)}
    with pytest.raises(UploadRejected, match="consecutive"):
        _unpack(_archive(entries), staging)
    _assert_nothing_left(staging)


def test_non_numeric_frame_names_are_rejected(staging: Path) -> None:
    with pytest.raises(UploadRejected, match="numbers"):
        _unpack(_archive({"frame_a.jpg": _jpeg(1)}), staging)
    _assert_nothing_left(staging)


def test_two_entries_with_the_same_name_are_rejected(staging: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("000001.jpg", _jpeg(1))
        with pytest.warns(UserWarning):
            archive.writestr("000001.jpg", _jpeg(2))
    with pytest.raises(UploadRejected, match="twice|duplicate"):
        _unpack(buffer.getvalue(), staging)
    _assert_nothing_left(staging)


def test_an_unreadable_frame_is_rejected_and_named(staging: Path) -> None:
    entries = _frames(4)
    entries["000003.jpg"] = b"this is not a jpeg"
    with pytest.raises(UploadRejected, match="000003.jpg"):
        _unpack(_archive(entries), staging)
    _assert_nothing_left(staging)


def test_a_corrupted_archive_body_is_rejected(staging: Path) -> None:
    data = bytearray(_archive(_frames(2), zipfile.ZIP_STORED))
    payload = bytes(_jpeg(1))
    position = bytes(data).index(payload) + len(payload) // 2
    data[position] ^= 0xFF
    with pytest.raises(UploadRejected, match="corrupt|unreadable"):
        _unpack(bytes(data), staging)
    _assert_nothing_left(staging)


def test_an_upload_over_the_size_limit_is_rejected_while_streaming(staging: Path) -> None:
    class Endless(io.RawIOBase):
        def __init__(self) -> None:
            self.served = 0

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: bytearray) -> int:
            buffer[:] = b"\0" * len(buffer)
            self.served += len(buffer)
            return len(buffer)

    source = Endless()
    with pytest.raises(UploadTooLarge, match="upload"):
        unpack_upload(source, staging, UploadLimits(max_upload_bytes=50_000, max_unpacked_bytes=100_000))
    assert source.served < 50_000 + 10 * 1024 * 1024
    _assert_nothing_left(staging)


def test_a_compression_bomb_is_rejected_before_anything_is_unpacked(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_to_extract(*arguments: object) -> None:
        raise AssertionError("the bomb must be rejected from its headers alone")

    monkeypatch.setattr("app.upload._extract", refuse_to_extract)
    bomb = _archive({"000001.jpg": b"\0" * 50_000_000})
    assert len(bomb) < 100_000
    with pytest.raises(UploadTooLarge, match="unpacked"):
        _unpack(bomb, staging, UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=1_000_000))
    _assert_nothing_left(staging)


def test_headers_that_understate_the_real_size_cannot_smuggle_data_through(staging: Path) -> None:
    data = bytearray(_archive({"000001.jpg": b"\0" * 5_000_000}))
    struct.pack_into("<I", data, data.index(b"PK\x03\x04") + 22, 1000)
    struct.pack_into("<I", data, data.index(b"PK\x01\x02") + 24, 1000)
    with pytest.raises(UploadRejected, match="corrupt|unreadable"):
        _unpack(bytes(data), staging, UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=10_000))
    _assert_nothing_left(staging)


def test_many_small_entries_count_towards_the_unpacked_limit(staging: Path) -> None:
    frames = _frames(40)
    total = sum(len(content) for content in frames.values())
    with pytest.raises(UploadTooLarge, match="unpacked"):
        _unpack(_archive(frames), staging, UploadLimits(max_upload_bytes=1_000_000, max_unpacked_bytes=total - 1))
    _assert_nothing_left(staging)


def test_an_archive_exactly_at_the_limits_is_accepted(staging: Path) -> None:
    frames = _frames(6)
    data = _archive(frames)
    total = sum(len(content) for content in frames.values())
    assert _unpack(data, staging, UploadLimits(max_upload_bytes=len(data), max_unpacked_bytes=total)) == 6


def test_a_failed_upload_does_not_disturb_files_already_in_the_staging_folder_parent(
    staging: Path, tmp_path: Path
) -> None:
    sibling = tmp_path / "keep.txt"
    sibling.write_text("keep")
    with pytest.raises(UploadRejected):
        _unpack(b"nope", staging)
    assert sibling.read_text() == "keep"
