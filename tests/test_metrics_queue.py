from collections.abc import Callable

import redis
from prometheus_client import CollectorRegistry, generate_latest

from metrics.definitions import create_metrics
from metrics.queue import QUEUE_DEPTH_METRIC, QueueDepthCollector, dedicated_redis_client, redis_queue_depth

QUEUE = "celery"


class FakeRedis:
    def __init__(self, lengths: dict[str, int], failure: Exception | None = None) -> None:
        self._lengths = lengths
        self._failure = failure
        self.asked: list[str] = []

    def llen(self, name: str) -> int:
        self.asked.append(name)
        if self._failure is not None:
            raise self._failure
        return self._lengths.get(name, 0)


def _registry_with(depth: Callable[[], int]) -> CollectorRegistry:
    registry = CollectorRegistry()
    create_metrics(registry)
    registry.register(QueueDepthCollector(depth))
    return registry


def test_the_depth_is_the_length_of_the_waiting_queue() -> None:
    client = FakeRedis({QUEUE: 3, "other": 99})
    registry = _registry_with(redis_queue_depth(client, QUEUE))
    assert registry.get_sample_value(QUEUE_DEPTH_METRIC) == 3
    assert client.asked == [QUEUE]


def test_an_empty_queue_is_reported_as_zero() -> None:
    registry = _registry_with(redis_queue_depth(FakeRedis({}), QUEUE))
    assert registry.get_sample_value(QUEUE_DEPTH_METRIC) == 0


def test_an_unreachable_redis_leaves_the_depth_out_and_the_rest_intact() -> None:
    for failure in (redis.ConnectionError("down"), redis.TimeoutError("slow"), OSError("network")):
        registry = _registry_with(redis_queue_depth(FakeRedis({QUEUE: 5}, failure), QUEUE))
        rendered = generate_latest(registry).decode()
        assert registry.get_sample_value(QUEUE_DEPTH_METRIC) is None
        assert QUEUE_DEPTH_METRIC not in rendered
        assert "openmot_frames_processed_total" in rendered


def test_registering_the_collector_does_not_query_redis() -> None:
    client = FakeRedis({QUEUE: 1})
    registry = CollectorRegistry()
    registry.register(QueueDepthCollector(redis_queue_depth(client, QUEUE)))
    assert client.asked == []


def test_the_dedicated_client_gives_up_after_two_seconds() -> None:
    client = dedicated_redis_client("redis://redis:6379/0")
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == 2 and kwargs["socket_connect_timeout"] == 2
