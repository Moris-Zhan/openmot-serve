from collections.abc import Callable, Iterable
from typing import Protocol

import redis
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

QUEUE_DEPTH_METRIC = "openmot_queue_depth"
QUEUE_DEPTH_DOCUMENTATION = "Jobs waiting for a worker; jobs a worker already took are not counted."
REDIS_TIMEOUT_SECONDS = 2


class ListLength(Protocol):
    def llen(self, name: str) -> int: ...


def dedicated_redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url, socket_timeout=REDIS_TIMEOUT_SECONDS, socket_connect_timeout=REDIS_TIMEOUT_SECONDS
    )


def redis_queue_depth(client: ListLength, queue_name: str) -> Callable[[], int]:
    def depth() -> int:
        return client.llen(queue_name)

    return depth


class QueueDepthCollector(Collector):
    def __init__(self, depth: Callable[[], int]) -> None:
        self._depth = depth

    def describe(self) -> Iterable[GaugeMetricFamily]:
        return [GaugeMetricFamily(QUEUE_DEPTH_METRIC, QUEUE_DEPTH_DOCUMENTATION)]

    def collect(self) -> Iterable[GaugeMetricFamily]:
        try:
            depth = self._depth()
        except (redis.RedisError, OSError):
            return
        family = GaugeMetricFamily(QUEUE_DEPTH_METRIC, QUEUE_DEPTH_DOCUMENTATION)
        family.add_metric([], depth)
        yield family
