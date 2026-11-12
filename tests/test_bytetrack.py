import numpy as np
import pytest

from tracking.bytetrack import TrackerParams, assign, run_tracker
from tracking.motchallenge import ResultRow, format_result

PERSON_WIDTH = 40.0
PERSON_HEIGHT = 100.0


def detection(left: float, top: float, score: float) -> list[float]:
    return [left, top, left + PERSON_WIDTH, top + PERSON_HEIGHT, score]


def frame_of(*boxes: list[float]) -> np.ndarray:
    return np.array(boxes, dtype=np.float64).reshape(-1, 5)


def walker(frame_count: int, start_left: float = 100.0, top: float = 200.0, speed: float = 3.0,
           score: float = 0.9, absent: range = range(0), scores: dict[int, float] | None = None) -> list[np.ndarray]:
    frames = []
    for index in range(frame_count):
        if index in absent:
            frames.append(frame_of())
            continue
        frame_score = (scores or {}).get(index, score)
        frames.append(frame_of(detection(start_left + speed * index, top, frame_score)))
    return frames


def identities_of(rows: tuple[ResultRow, ...]) -> list[int]:
    return [row.identity for row in rows]


def frames_of(rows: tuple[ResultRow, ...]) -> list[int]:
    return [row.frame for row in rows]


def test_identity_persists_for_one_moving_person() -> None:
    rows = run_tracker(walker(30), TrackerParams())
    assert len(rows) == 30
    assert set(identities_of(rows)) == {1}
    assert frames_of(rows) == list(range(1, 31))


def test_two_separate_people_get_distinct_identities() -> None:
    frames = [
        frame_of(detection(100 + 3 * i, 200, 0.9), detection(900 + 3 * i, 300, 0.9)) for i in range(20)
    ]
    rows = run_tracker(frames, TrackerParams())
    assert len(rows) == 40
    for frame_number in (1, 10, 20):
        in_frame = [row.identity for row in rows if row.frame == frame_number]
        assert sorted(in_frame) == [1, 2]
    left_ids = {row.identity for row in rows if row.left < 500}
    right_ids = {row.identity for row in rows if row.left >= 500}
    assert len(left_ids) == 1 and len(right_ids) == 1 and left_ids != right_ids


def test_short_occlusion_keeps_the_same_identity() -> None:
    rows = run_tracker(walker(30, absent=range(10, 15)), TrackerParams())
    assert set(identities_of(rows)) == {1}
    assert not any(10 < row.frame <= 15 for row in rows)
    assert any(row.frame > 15 for row in rows)


def test_identity_is_never_reused_after_a_track_ends() -> None:
    params = TrackerParams(track_buffer=30)
    frames = walker(80, absent=range(10, 50))
    rows = run_tracker(frames, params)
    before = {row.identity for row in rows if row.frame <= 10}
    after = {row.identity for row in rows if row.frame > 50}
    assert before == {1}
    assert after and min(after) > max(before)
    assert not (before & after)


def test_longer_buffer_bridges_a_longer_gap() -> None:
    frames = walker(60, absent=range(10, 40))
    short = run_tracker(frames, TrackerParams(track_buffer=10))
    long = run_tracker(frames, TrackerParams(track_buffer=60))
    assert len(set(identities_of(short))) == 2
    assert set(identities_of(long)) == {1}


def test_low_score_detections_continue_an_existing_track() -> None:
    scores = {index: 0.3 for index in range(5, 10)}
    rows = run_tracker(walker(15, scores=scores), TrackerParams())
    assert len(rows) == 15
    assert set(identities_of(rows)) == {1}


def test_low_score_detections_never_start_a_track() -> None:
    assert run_tracker(walker(15, score=0.3), TrackerParams()) == ()


def test_detections_below_the_floor_are_ignored() -> None:
    assert run_tracker(walker(15, score=0.05), TrackerParams()) == ()


def test_new_track_after_first_frame_needs_confirmation() -> None:
    frames = [frame_of()] * 4 + walker(10)
    rows = run_tracker(frames, TrackerParams())
    assert frames_of(rows)[0] == 6
    assert set(identities_of(rows)) == {1}


def test_new_track_requires_score_above_threshold_plus_margin() -> None:
    frames = [frame_of()] * 4 + walker(10, score=0.65)
    assert run_tracker(frames, TrackerParams(detection_threshold=0.6)) == ()


def test_empty_frames_are_handled() -> None:
    assert run_tracker([frame_of()] * 5, TrackerParams()) == ()
    assert run_tracker([], TrackerParams()) == ()


def noisy_scene(seed: int) -> list[np.ndarray]:
    generator = np.random.default_rng(seed)
    frames = []
    for index in range(60):
        boxes = []
        for person in range(4):
            jitter = generator.normal(0, 1.5, 2)
            boxes.append(detection(100 + 200 * person + 2 * index + jitter[0], 200 + jitter[1],
                                   float(generator.uniform(0.35, 0.99))))
        for _ in range(int(generator.integers(0, 3))):
            boxes.append(detection(float(generator.uniform(0, 1500)), float(generator.uniform(0, 900)),
                                   float(generator.uniform(0.1, 0.95))))
        frames.append(frame_of(*boxes))
    return frames


def test_identical_input_gives_byte_identical_output() -> None:
    scene = noisy_scene(seed=7)
    first = format_result(run_tracker(scene, TrackerParams())).encode()
    second = format_result(run_tracker([frame.copy() for frame in scene], TrackerParams())).encode()
    assert first and first == second


def test_input_detections_are_not_modified() -> None:
    scene = noisy_scene(seed=3)
    snapshot = [frame.copy() for frame in scene]
    run_tracker(scene, TrackerParams())
    assert all(np.array_equal(a, b) for a, b in zip(scene, snapshot, strict=False))


def test_custom_parameters_change_behaviour() -> None:
    frames = walker(15, score=0.5)
    assert run_tracker(frames, TrackerParams(detection_threshold=0.6)) == ()
    assert len(run_tracker(frames, TrackerParams(detection_threshold=0.4))) > 0


@pytest.mark.parametrize(
    "kwargs",
    [{"detection_threshold": 0.05}, {"detection_threshold": 1.5}, {"match_threshold": 0.0},
     {"match_threshold": 1.2}, {"track_buffer": 0}],
)
def test_invalid_parameters_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        TrackerParams(**kwargs)


def test_assign_picks_the_globally_cheapest_pairing() -> None:
    cost = np.array([[0.1, 0.2], [0.1, 0.9]])
    matches, unmatched_rows, unmatched_columns = assign(cost, limit=1.0)
    assert sorted(matches) == [(0, 1), (1, 0)]
    assert unmatched_rows == () and unmatched_columns == ()


def test_assign_leaves_pairs_above_the_limit_unmatched() -> None:
    cost = np.array([[0.2, 0.95], [0.9, 0.97]])
    matches, unmatched_rows, unmatched_columns = assign(cost, limit=0.5)
    assert matches == ((0, 0),)
    assert unmatched_rows == (1,)
    assert unmatched_columns == (1,)


def test_assign_handles_empty_sides() -> None:
    assert assign(np.zeros((0, 3)), limit=0.5) == ((), (), (0, 1, 2))
    assert assign(np.zeros((2, 0)), limit=0.5) == ((), (0, 1), ())
