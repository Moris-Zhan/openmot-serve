# Derived from ByteTrack (https://github.com/ifzhang/ByteTrack), commit
# d1bf0191adff59bc8fcfeaa0b33d3d1642552a99 (yolox/tracker/byte_tracker.py, matching.py).
# Copyright (c) 2021 Yifu Zhang. Licensed under the MIT License; the full text is in
# tracking/licenses/ByteTrack-LICENSE.
#
# Modifications: pure-function interface with frozen state; linear assignment uses
# scipy instead of lap/cython_bbox; per-run identity counter (identities start at 1 and
# are never reused); expired lost tracks are dropped immediately instead of lingering
# for one extra association round; the Kalman filter lives in tracking/kalman.py.

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import linear_sum_assignment

from tracking.kalman import initiate, predict, update
from tracking.motchallenge import ResultRow

LOW_SCORE_FLOOR = 0.1
NEW_TRACK_MARGIN = 0.1
SECOND_STAGE_LIMIT = 0.5
UNCONFIRMED_LIMIT = 0.7
DUPLICATE_DISTANCE = 0.15


@dataclass(frozen=True)
class TrackerParams:
    detection_threshold: float = 0.6
    match_threshold: float = 0.9
    track_buffer: int = 30

    def __post_init__(self) -> None:
        if not LOW_SCORE_FLOOR <= self.detection_threshold <= 1.0:
            raise ValueError(
                f"detection_threshold must be in [{LOW_SCORE_FLOOR}, 1.0], got {self.detection_threshold}"
            )
        if not 0.0 < self.match_threshold <= 1.0:
            raise ValueError(f"match_threshold must be in (0, 1], got {self.match_threshold}")
        if self.track_buffer < 1:
            raise ValueError(f"track_buffer must be at least 1 frame, got {self.track_buffer}")


@dataclass(frozen=True)
class Track:
    identity: int
    mean: np.ndarray
    covariance: np.ndarray
    score: float
    frame: int
    start_frame: int
    tracked: bool
    confirmed: bool

    @property
    def tlwh(self) -> np.ndarray:
        center_x, center_y, aspect, height = self.mean[:4]
        width = aspect * height
        return np.array([center_x - width / 2, center_y - height / 2, width, height])

    @property
    def tlbr(self) -> np.ndarray:
        left, top, width, height = self.tlwh
        return np.array([left, top, left + width, top + height])


@dataclass(frozen=True)
class TrackerState:
    frame: int
    tracked: tuple[Track, ...]
    lost: tuple[Track, ...]
    next_identity: int


def initial_state() -> TrackerState:
    return TrackerState(frame=0, tracked=(), lost=(), next_identity=1)


def _read_only(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array


def _to_xyah(box: np.ndarray) -> np.ndarray:
    width, height = box[2] - box[0], box[3] - box[1]
    return np.array([box[0] + width / 2, box[1] + height / 2, width / height, height])


def iou_matrix(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    if len(first) == 0 or len(second) == 0:
        return np.zeros((len(first), len(second)))
    top_left = np.maximum(first[:, None, :2], second[None, :, :2])
    bottom_right = np.minimum(first[:, None, 2:], second[None, :, 2:])
    overlap = np.clip(bottom_right - top_left, 0, None).prod(axis=2)
    area_first = (first[:, 2] - first[:, 0]) * (first[:, 3] - first[:, 1])
    area_second = (second[:, 2] - second[:, 0]) * (second[:, 3] - second[:, 1])
    union = area_first[:, None] + area_second[None, :] - overlap
    return overlap / np.where(union > 0, union, 1.0)


def _boxes_of(tracks: Sequence[Track]) -> np.ndarray:
    return np.array([track.tlbr for track in tracks]).reshape(-1, 4)


def _fuse_score(cost: np.ndarray, scores: np.ndarray) -> np.ndarray:
    return 1.0 - (1.0 - cost) * scores[None, :]


def assign(
    cost: np.ndarray, limit: float
) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...], tuple[int, ...]]:
    row_count, column_count = cost.shape
    if row_count == 0 or column_count == 0:
        return (), tuple(range(row_count)), tuple(range(column_count))
    # Pad so any row or column may stay unassigned at a price of limit / 2, which makes
    # a real pair worth taking exactly when its cost is below the limit.
    extended = np.full((row_count + column_count, row_count + column_count), limit / 2)
    extended[row_count:, column_count:] = 0.0
    extended[:row_count, :column_count] = cost
    rows, columns = linear_sum_assignment(extended)
    matches = tuple(
        (int(row), int(column))
        for row, column in zip(rows, columns)
        if row < row_count and column < column_count and cost[row, column] <= limit
    )
    matched_rows = {row for row, _ in matches}
    matched_columns = {column for _, column in matches}
    return (
        matches,
        tuple(row for row in range(row_count) if row not in matched_rows),
        tuple(column for column in range(column_count) if column not in matched_columns),
    )


def _predicted(track: Track) -> Track:
    mean = track.mean.copy()
    if not track.tracked:
        mean[7] = 0.0
    next_mean, next_covariance = predict(mean, track.covariance)
    return replace(track, mean=_read_only(next_mean), covariance=_read_only(next_covariance))


def _corrected(track: Track, box: np.ndarray, score: float, frame: int) -> Track:
    mean, covariance = update(track.mean, track.covariance, _to_xyah(box))
    return replace(
        track,
        mean=_read_only(mean),
        covariance=_read_only(covariance),
        score=float(score),
        frame=frame,
        tracked=True,
        confirmed=True,
    )


def _drop_duplicates(
    tracked: list[Track], lost: list[Track]
) -> tuple[list[Track], list[Track]]:
    distance = 1.0 - iou_matrix(_boxes_of(tracked), _boxes_of(lost))
    drop_tracked: set[int] = set()
    drop_lost: set[int] = set()
    for tracked_index, lost_index in zip(*np.where(distance < DUPLICATE_DISTANCE)):
        tracked_age = tracked[tracked_index].frame - tracked[tracked_index].start_frame
        lost_age = lost[lost_index].frame - lost[lost_index].start_frame
        if tracked_age > lost_age:
            drop_lost.add(int(lost_index))
        else:
            drop_tracked.add(int(tracked_index))
    return (
        [track for index, track in enumerate(tracked) if index not in drop_tracked],
        [track for index, track in enumerate(lost) if index not in drop_lost],
    )


def update_tracker(
    state: TrackerState, params: TrackerParams, detections: np.ndarray
) -> tuple[TrackerState, tuple[Track, ...]]:
    frame = state.frame + 1
    boxes = detections[:, :4]
    scores = detections[:, 4]
    well_formed = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    threshold = params.detection_threshold
    high_mask = well_formed & (scores >= threshold)
    low_mask = well_formed & (scores > LOW_SCORE_FLOOR) & (scores < threshold)
    high_boxes, high_scores = boxes[high_mask], scores[high_mask]
    low_boxes, low_scores = boxes[low_mask], scores[low_mask]

    unconfirmed = [track for track in state.tracked if not track.confirmed]
    pool = [_predicted(track) for track in state.tracked if track.confirmed]
    pool += [_predicted(track) for track in state.lost]

    carried: list[Track] = []
    refound: list[Track] = []
    newly_lost: list[Track] = []

    cost = _fuse_score(1.0 - iou_matrix(_boxes_of(pool), high_boxes), high_scores)
    matches, unmatched_pool, unmatched_high = assign(cost, params.match_threshold)
    for pool_index, detection_index in matches:
        track = _corrected(pool[pool_index], high_boxes[detection_index], high_scores[detection_index], frame)
        (carried if pool[pool_index].tracked else refound).append(track)

    still_tracked = [index for index in unmatched_pool if pool[index].tracked]
    cost = 1.0 - iou_matrix(_boxes_of([pool[index] for index in still_tracked]), low_boxes)
    matches, unmatched_second, _ = assign(cost, SECOND_STAGE_LIMIT)
    for local_index, detection_index in matches:
        carried.append(
            _corrected(pool[still_tracked[local_index]], low_boxes[detection_index], low_scores[detection_index], frame)
        )
    for local_index in unmatched_second:
        newly_lost.append(replace(pool[still_tracked[local_index]], tracked=False))

    remaining_boxes = high_boxes[list(unmatched_high)]
    remaining_scores = high_scores[list(unmatched_high)]
    cost = _fuse_score(1.0 - iou_matrix(_boxes_of(unconfirmed), remaining_boxes), remaining_scores)
    matches, _, unmatched_new = assign(cost, UNCONFIRMED_LIMIT)
    for track_index, detection_index in matches:
        carried.append(_corrected(unconfirmed[track_index], remaining_boxes[detection_index],
                                  remaining_scores[detection_index], frame))

    next_identity = state.next_identity
    activated: list[Track] = []
    for detection_index in unmatched_new:
        if remaining_scores[detection_index] < threshold + NEW_TRACK_MARGIN:
            continue
        mean, covariance = initiate(_to_xyah(remaining_boxes[detection_index]))
        activated.append(
            Track(
                identity=next_identity,
                mean=_read_only(mean),
                covariance=_read_only(covariance),
                score=float(remaining_scores[detection_index]),
                frame=frame,
                start_frame=frame,
                tracked=True,
                confirmed=frame == 1,
            )
        )
        next_identity += 1

    waiting = [pool[index] for index in unmatched_pool if not pool[index].tracked]
    survivors = [track for track in waiting if frame - track.frame <= params.track_buffer]

    tracked_next, lost_next = _drop_duplicates(carried + activated + refound, survivors + newly_lost)
    new_state = TrackerState(frame, tuple(tracked_next), tuple(lost_next), next_identity)
    return new_state, tuple(track for track in tracked_next if track.confirmed)


def run_tracker(frames: Sequence[np.ndarray], params: TrackerParams) -> tuple[ResultRow, ...]:
    state = initial_state()
    rows: list[ResultRow] = []
    for detections in frames:
        state, output = update_tracker(state, params, detections)
        for track in sorted(output, key=lambda item: item.identity):
            left, top, width, height = track.tlwh
            rows.append(
                ResultRow(state.frame, track.identity, float(left), float(top), float(width),
                          float(height), track.score)
            )
    return tuple(rows)
