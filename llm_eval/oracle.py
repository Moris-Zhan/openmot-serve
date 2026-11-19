from pathlib import Path

# Independent gold-answer program: it reads the result file line by line on its own and imports
# nothing from this package, so a bug in the tools cannot also appear here.


def _rows(path: Path) -> list[tuple[int, int, float, float, float, float]]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        fields = line.split(",")
        rows.append(
            (int(fields[0]), int(fields[1]), float(fields[2]), float(fields[3]), float(fields[4]), float(fields[5]))
        )
    return rows


def _frames_per_id(path: Path) -> dict[int, set[int]]:
    seen: dict[int, set[int]] = {}
    for frame, identity, *_ in _rows(path):
        seen.setdefault(identity, set()).add(frame)
    return seen


def unique_ids(path: Path, min_frames: int = 1) -> int:
    return sum(1 for frames in _frames_per_id(path).values() if len(frames) >= min_frames)


def track_span(path: Path, track_id: int) -> tuple[int, int] | None:
    frames = _frames_per_id(path).get(track_id)
    return (min(frames), max(frames)) if frames else None


def dwell_frames(path: Path, track_id: int) -> int:
    return len(_frames_per_id(path).get(track_id, set()))


def people_in_frame(path: Path, frame: int) -> int:
    return len({identity for row_frame, identity, *_ in _rows(path) if row_frame == frame})


def ids_in_region(
    path: Path,
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    frame_start: int | None = None,
    frame_end: int | None = None,
) -> list[int]:
    found: set[int] = set()
    for frame, identity, x, y, width, height in _rows(path):
        if frame_start is not None and frame < frame_start:
            continue
        if frame_end is not None and frame > frame_end:
            continue
        foot_x, foot_y = x + width / 2, y + height
        if x_min <= foot_x <= x_max and y_min <= foot_y <= y_max:
            found.add(identity)
    return sorted(found)
