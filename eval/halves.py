import configparser
import os
import re
import shutil
import tempfile
from pathlib import Path

from eval.split import FrameRange

_SEQ_LENGTH_LINE = re.compile(r"^(seqLength\s*=).*$", re.MULTILINE)


class HalfError(RuntimeError):
    pass


def _read_sequence_length(seqinfo_path: Path) -> tuple[str, int]:
    if not seqinfo_path.is_file():
        raise HalfError(f"seqinfo.ini not found: {seqinfo_path}")
    text = seqinfo_path.read_text()
    parser = configparser.ConfigParser()
    parser.read_string(text)
    try:
        return text, int(parser["Sequence"]["seqLength"])
    except (KeyError, ValueError) as error:
        raise HalfError(f"{seqinfo_path} has no valid [Sequence] seqLength") from error


def _frame_path(frames_dir: Path, number: int) -> Path:
    return frames_dir / f"{number:06d}.jpg"


def _half_ground_truth(gt_path: Path, half: FrameRange) -> str:
    lines = []
    for line in gt_path.read_text().splitlines():
        if not line.strip():
            continue
        frame, _, rest = line.partition(",")
        if half.first <= int(frame) <= half.last:
            lines.append(f"{int(frame) - half.first + 1},{rest}")
    return "".join(f"{line}\n" for line in lines)


def materialize_half(sequence_dir: Path, half: FrameRange, destination: Path) -> None:
    if destination.exists():
        raise HalfError(f"destination already exists: {destination}")
    seqinfo_text, length = _read_sequence_length(sequence_dir / "seqinfo.ini")
    if half.first < 1 or half.last > length:
        raise HalfError(f"frames {half.first}-{half.last} are outside the sequence (1-{length})")
    frames_dir = sequence_dir / "img1"
    originals = [_frame_path(frames_dir, number).resolve() for number in half.frames()]
    for original in originals:
        if not original.is_file():
            raise HalfError(f"frame file not found: {original.name} in {frames_dir}")
    on_disk = sum(1 for path in frames_dir.iterdir() if path.suffix == ".jpg")
    if on_disk != length:
        raise HalfError(f"seqLength is {length} but {frames_dir} holds {on_disk} frames")
    gt_path = sequence_dir / "gt" / "gt.txt"
    if not gt_path.is_file():
        raise HalfError(f"gt.txt not found: {gt_path}")
    ground_truth = _half_ground_truth(gt_path, half)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=f".{destination.name}-"))
    try:
        (staging / "img1").mkdir()
        (staging / "gt").mkdir()
        for position, original in enumerate(originals, start=1):
            os.symlink(original, _frame_path(staging / "img1", position))
        (staging / "gt" / "gt.txt").write_text(ground_truth)
        (staging / "seqinfo.ini").write_text(_SEQ_LENGTH_LINE.sub(rf"\g<1>{half.count}", seqinfo_text))
        os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
