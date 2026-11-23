from pathlib import Path

from llm_eval import oracle

# Hand-built sequence. Bottom-centre of a box is (x + w/2, y + h).
#   id 1: frames 1,2,3        box 0,0,10,20     -> bottom-centre (5, 20)
#   id 2: frames 1,2,5        box 100,100,20,40 -> bottom-centre (110, 140)   (absent on frames 3 and 4)
#   id 3: frame 2 only        box 50,50,10,10   -> bottom-centre (55, 60)
HAND_ROWS = [
    (1, 1, 0, 0, 10, 20),
    (2, 1, 0, 0, 10, 20),
    (3, 1, 0, 0, 10, 20),
    (1, 2, 100, 100, 20, 40),
    (2, 2, 100, 100, 20, 40),
    (5, 2, 100, 100, 20, 40),
    (2, 3, 50, 50, 10, 10),
]


def write_rows(path: Path, rows: list[tuple[int, int, float, float, float, float]]) -> Path:
    path.write_text("".join(f"{f},{i},{x},{y},{w},{h},0.9,-1,-1,-1\n" for f, i, x, y, w, h in rows))
    return path


def test_unique_ids_by_hand(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", HAND_ROWS)
    assert oracle.unique_ids(path) == 3
    assert oracle.unique_ids(path, min_frames=2) == 2
    assert oracle.unique_ids(path, min_frames=3) == 2
    assert oracle.unique_ids(path, min_frames=4) == 0


def test_track_span_and_dwell_by_hand(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", HAND_ROWS)
    assert oracle.track_span(path, 1) == (1, 3)
    assert oracle.track_span(path, 2) == (1, 5)
    assert oracle.track_span(path, 3) == (2, 2)
    assert oracle.track_span(path, 99) is None
    assert oracle.dwell_frames(path, 2) == 3
    assert oracle.dwell_frames(path, 1) == 3
    assert oracle.dwell_frames(path, 99) == 0


def test_people_in_frame_by_hand(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", HAND_ROWS)
    assert [oracle.people_in_frame(path, frame) for frame in range(1, 7)] == [2, 3, 1, 0, 1, 0]


def test_ids_in_region_uses_the_bottom_centre_and_includes_the_border(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", HAND_ROWS)
    assert oracle.ids_in_region(path, 0, 0, 10, 20) == [1]
    assert oracle.ids_in_region(path, 0, 0, 5, 20) == [1]
    assert oracle.ids_in_region(path, 0, 0, 4.99, 20) == []
    assert oracle.ids_in_region(path, 50, 50, 60, 60) == [3]
    assert oracle.ids_in_region(path, 0, 0, 200, 200) == [1, 2, 3]
    assert oracle.ids_in_region(path, 100, 100, 120, 140, frame_start=3) == [2]
    assert oracle.ids_in_region(path, 100, 100, 120, 140, frame_start=3, frame_end=4) == []


def test_an_empty_result_gives_zero_and_nothing(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", [])
    assert oracle.unique_ids(path) == 0
    assert oracle.people_in_frame(path, 1) == 0
    assert oracle.track_span(path, 1) is None
    assert oracle.ids_in_region(path, 0, 0, 100, 100) == []


def test_a_single_identity(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", [(f, 1, 0, 0, 10, 10) for f in (4, 5, 6)])
    assert oracle.unique_ids(path) == 1
    assert oracle.track_span(path, 1) == (4, 6)


def test_an_identity_that_reappears_counts_only_the_frames_it_was_present(tmp_path: Path) -> None:
    rows = [(f, 9, 0, 0, 10, 10) for f in (3, 4, 5, 10, 11, 12)]
    path = write_rows(tmp_path / "r.txt", rows)
    assert oracle.track_span(path, 9) == (3, 12)
    assert oracle.dwell_frames(path, 9) == 6


def test_forty_people_in_one_frame(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "r.txt", [(7, identity, identity, 0, 5, 5) for identity in range(1, 41)])
    assert oracle.people_in_frame(path, 7) == 40
    assert oracle.unique_ids(path) == 40
