from dataclasses import dataclass

FIELD_COUNT = 10


class ResultFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ResultRow:
    frame: int
    identity: int
    left: float
    top: float
    width: float
    height: float
    confidence: float


def _validate(row: ResultRow) -> None:
    if row.frame < 1:
        raise ResultFormatError(f"frame must be a positive integer starting at 1, got {row.frame}")
    if row.identity < 1:
        raise ResultFormatError(f"identity must be a positive integer, got {row.identity}")


def format_result_line(row: ResultRow) -> str:
    _validate(row)
    return (
        f"{row.frame},{row.identity},{row.left:.2f},{row.top:.2f},"
        f"{row.width:.2f},{row.height:.2f},{row.confidence:.4f},-1,-1,-1"
    )


def format_result(rows: tuple[ResultRow, ...]) -> str:
    return "".join(format_result_line(row) + "\n" for row in rows)


def _parse_line(line: str, line_number: int) -> ResultRow:
    fields = line.split(",")
    if len(fields) != FIELD_COUNT:
        raise ResultFormatError(
            f"line {line_number}: expected {FIELD_COUNT} fields, got {len(fields)}"
        )
    try:
        row = ResultRow(
            frame=int(fields[0]),
            identity=int(fields[1]),
            left=float(fields[2]),
            top=float(fields[3]),
            width=float(fields[4]),
            height=float(fields[5]),
            confidence=float(fields[6]),
        )
    except ValueError as error:
        raise ResultFormatError(f"line {line_number}: {error}") from error
    _validate(row)
    return row


def parse_result(text: str) -> tuple[ResultRow, ...]:
    return tuple(
        _parse_line(line, number)
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    )
