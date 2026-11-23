from dataclasses import dataclass
from pathlib import Path

MIN_FIELDS = 6


class ResultFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Detection:
    frame: int
    track_id: int
    x: float
    y: float
    width: float
    height: float


def _parse(line: str, number: int) -> Detection:
    fields = line.split(",")
    if len(fields) < MIN_FIELDS:
        raise ResultFormatError(f"line {number}: expected at least {MIN_FIELDS} fields, got {len(fields)}")
    try:
        return Detection(
            int(fields[0]), int(fields[1]), float(fields[2]), float(fields[3]), float(fields[4]), float(fields[5])
        )
    except ValueError as error:
        raise ResultFormatError(f"line {number}: not a number ({error})") from error


def read_results(path: Path) -> list[Detection]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_parse(line, number) for number, line in enumerate(lines, start=1) if line.strip()]
