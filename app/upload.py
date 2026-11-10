import shutil
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import cv2

from tracking.sequence import IMAGE_SUFFIXES, SequenceError, list_frames

_CHUNK_BYTES = 1024 * 1024
_ARCHIVE_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError)


class UploadRejected(ValueError):
    pass


class UploadTooLarge(UploadRejected):
    pass


@dataclass(frozen=True)
class UploadLimits:
    max_upload_bytes: int
    max_unpacked_bytes: int


def _copy_capped(source: BinaryIO, sink: BinaryIO, limit: int, too_large: str) -> int:
    copied = 0
    while True:
        chunk = source.read(min(_CHUNK_BYTES, limit - copied + 1))
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > limit:
            raise UploadTooLarge(too_large)
        sink.write(chunk)


def _checked_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    parts = name.split("/")
    if name.startswith("/") or "\\" in name or ".." in parts:
        raise UploadRejected(f"unsafe path in archive: {name!r}")
    if len(parts) > 1 or info.is_dir():
        raise UploadRejected(f"archive entries must sit at the top level, not in a folder: {name!r}")
    if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise UploadRejected(f"{name!r} is not an image, only {sorted(IMAGE_SUFFIXES)} files are accepted")
    return name


def _checked_entries(archive: zipfile.ZipFile, limits: UploadLimits) -> list[tuple[zipfile.ZipInfo, str]]:
    entries = [(info, _checked_name(info)) for info in archive.infolist()]
    if not entries:
        raise UploadRejected("the archive contains no frames")
    names = [name for _, name in entries]
    for name in names:
        if names.count(name) > 1:
            raise UploadRejected(f"{name!r} appears twice in the archive")
    declared = sum(info.file_size for info, _ in entries)
    if declared > limits.max_unpacked_bytes:
        raise UploadTooLarge(
            f"unpacked frames would be {declared} bytes, over the limit of {limits.max_unpacked_bytes}"
        )
    return entries


def _extract(archive: zipfile.ZipFile, entries: list[tuple[zipfile.ZipInfo, str]], staging: Path) -> None:
    for info, name in entries:
        with archive.open(info) as source, open(staging / name, "wb") as sink:
            shutil.copyfileobj(source, sink, _CHUNK_BYTES)


def _check_frames(staging: Path) -> int:
    try:
        frames = list_frames(staging)
    except SequenceError as error:
        raise UploadRejected(f"invalid frame sequence: {error}") from error
    for frame in frames:
        if cv2.imread(str(frame)) is None:
            raise UploadRejected(f"frame {frame.name} cannot be decoded as an image")
    return len(frames)


def _clear(directory: Path) -> None:
    for child in directory.iterdir():
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()


def unpack_upload(stream: BinaryIO, staging: Path, limits: UploadLimits) -> int:
    try:
        with tempfile.TemporaryFile(dir=staging.parent, prefix="upload-zip-") as spool:
            _copy_capped(stream, spool, limits.max_upload_bytes, f"upload is larger than {limits.max_upload_bytes} bytes")
            spool.seek(0)
            if not zipfile.is_zipfile(spool):
                raise UploadRejected("the upload is not a zip archive")
            try:
                with zipfile.ZipFile(spool) as archive:
                    entries = _checked_entries(archive, limits)
                    _extract(archive, entries, staging)
            except _ARCHIVE_ERRORS as error:
                raise UploadRejected(f"the archive is corrupt or unreadable: {error}") from error
        return _check_frames(staging)
    except BaseException:
        _clear(staging)
        raise
