from pathlib import Path

SYNTHETIC_SOURCES = ("empty", "single", "gaps", "crowd")

_Row = tuple[int, int, float, float, float, float]


def _empty() -> list[_Row]:
    return []


def _single() -> list[_Row]:
    return [(frame, 1, 10.0 * frame, 100.0, 40.0, 100.0) for frame in range(1, 21)]


def _gaps() -> list[_Row]:
    rows: list[_Row] = [(frame, 1, 50.0, 100.0, 40.0, 100.0) for frame in range(1, 11)]
    reappearing = [3, 4, 5, 10, 11, 12]
    rows += [(frame, 2, 300.0, 100.0, 40.0, 100.0) for frame in reappearing]
    rows += [(frame, 3, 600.0, 100.0, 40.0, 100.0) for frame in range(8, 16)]
    return sorted(rows)


def _crowd() -> list[_Row]:
    rows: list[_Row] = [(frame, i, 100.0 * i, 200.0, 40.0, 100.0) for frame in range(1, 11) for i in range(1, 6)]
    rows += [(7, i, 20.0 * i, 400.0, 20.0, 50.0) for i in range(6, 41)]
    return sorted(rows)


_BUILDERS = {"empty": _empty, "single": _single, "gaps": _gaps, "crowd": _crowd}


def write_synthetic(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name in SYNTHETIC_SOURCES:
        path = directory / f"{name}.txt"
        path.write_text("".join(f"{f},{i},{x},{y},{w},{h},0.9,-1,-1,-1\n" for f, i, x, y, w, h in _BUILDERS[name]()))
        paths[name] = path
    return paths
