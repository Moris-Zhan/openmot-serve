import numpy as np

from tracking.kalman import initiate, predict, update


def measurement(center_x: float = 100.0, center_y: float = 200.0, aspect: float = 0.4, height: float = 100.0) -> np.ndarray:
    return np.array([center_x, center_y, aspect, height])


def test_initiate_starts_with_zero_velocity() -> None:
    mean, covariance = initiate(measurement())
    assert mean.shape == (8,) and covariance.shape == (8, 8)
    assert np.allclose(mean[:4], measurement())
    assert np.all(mean[4:] == 0)
    assert np.allclose(covariance, covariance.T)
    assert np.all(np.diag(covariance) > 0)


def test_predict_moves_position_by_velocity_and_grows_uncertainty() -> None:
    mean, covariance = initiate(measurement())
    moving = mean.copy()
    moving[4] = 3.0
    moving[5] = -2.0
    next_mean, next_covariance = predict(moving, covariance)
    assert np.allclose(next_mean[:2], moving[:2] + np.array([3.0, -2.0]))
    assert np.allclose(next_mean[4:], moving[4:])
    assert np.trace(next_covariance) > np.trace(covariance)


def test_update_moves_estimate_toward_measurement() -> None:
    mean, covariance = initiate(measurement())
    predicted_mean, predicted_covariance = predict(mean, covariance)
    observed = measurement(center_x=110.0)
    new_mean, new_covariance = update(predicted_mean, predicted_covariance, observed)
    assert predicted_mean[0] < new_mean[0] <= observed[0]
    assert np.trace(new_covariance) < np.trace(predicted_covariance)
    assert np.allclose(new_covariance, new_covariance.T)


def test_filter_learns_constant_velocity() -> None:
    mean, covariance = initiate(measurement())
    for step in range(1, 31):
        mean, covariance = predict(mean, covariance)
        mean, covariance = update(mean, covariance, measurement(center_x=100.0 + 4.0 * step))
    assert abs(mean[4] - 4.0) < 0.3
    assert abs(mean[5]) < 0.3


def test_functions_do_not_mutate_their_inputs() -> None:
    mean, covariance = initiate(measurement())
    mean_before, covariance_before = mean.copy(), covariance.copy()
    predict(mean, covariance)
    update(mean, covariance, measurement(center_x=105.0))
    assert np.array_equal(mean, mean_before)
    assert np.array_equal(covariance, covariance_before)
