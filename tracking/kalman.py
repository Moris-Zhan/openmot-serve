# Constant-velocity Kalman filter over bounding boxes in (cx, cy, aspect, height)
# space, as used by SORT-family trackers. Written from the textbook equations rather
# than copied from DeepSORT/ByteTrack (DeepSORT is GPL-3.0); only the published noise
# weights (1/20 position, 1/160 velocity) follow ByteTrack's settings.

import numpy as np

POSITION_WEIGHT = 1.0 / 20
VELOCITY_WEIGHT = 1.0 / 160

_MOTION_MATRIX = np.eye(8)
_MOTION_MATRIX[:4, 4:] = np.eye(4)
_OBSERVATION_MATRIX = np.eye(4, 8)


def initiate(measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height = measurement[3]
    mean = np.concatenate([measurement, np.zeros(4)])
    std = np.array(
        [
            2 * POSITION_WEIGHT * height,
            2 * POSITION_WEIGHT * height,
            1e-2,
            2 * POSITION_WEIGHT * height,
            10 * VELOCITY_WEIGHT * height,
            10 * VELOCITY_WEIGHT * height,
            1e-5,
            10 * VELOCITY_WEIGHT * height,
        ]
    )
    return mean, np.diag(np.square(std))


def predict(mean: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height = mean[3]
    std = np.array(
        [
            POSITION_WEIGHT * height,
            POSITION_WEIGHT * height,
            1e-2,
            POSITION_WEIGHT * height,
            VELOCITY_WEIGHT * height,
            VELOCITY_WEIGHT * height,
            1e-5,
            VELOCITY_WEIGHT * height,
        ]
    )
    process_noise = np.diag(np.square(std))
    next_mean = _MOTION_MATRIX @ mean
    next_covariance = _MOTION_MATRIX @ covariance @ _MOTION_MATRIX.T + process_noise
    return next_mean, next_covariance


def _project(mean: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height = mean[3]
    std = np.array(
        [POSITION_WEIGHT * height, POSITION_WEIGHT * height, 1e-1, POSITION_WEIGHT * height]
    )
    measurement_noise = np.diag(np.square(std))
    projected_mean = _OBSERVATION_MATRIX @ mean
    projected_covariance = _OBSERVATION_MATRIX @ covariance @ _OBSERVATION_MATRIX.T + measurement_noise
    return projected_mean, projected_covariance


def update(
    mean: np.ndarray, covariance: np.ndarray, measurement: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    projected_mean, projected_covariance = _project(mean, covariance)
    gain = np.linalg.solve(projected_covariance, _OBSERVATION_MATRIX @ covariance).T
    new_mean = mean + gain @ (measurement - projected_mean)
    new_covariance = covariance - gain @ projected_covariance @ gain.T
    return new_mean, (new_covariance + new_covariance.T) / 2
