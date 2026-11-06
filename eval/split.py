from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class FrameRange:
    first: int
    last: int

    @property
    def count(self) -> int:
        return self.last - self.first + 1

    def frames(self) -> Iterator[int]:
        return iter(range(self.first, self.last + 1))


@dataclass(frozen=True)
class TuningHalf(FrameRange):
    pass


@dataclass(frozen=True)
class ValidationHalf(FrameRange):
    pass


def split_sequence(length: int) -> tuple[TuningHalf, ValidationHalf]:
    if isinstance(length, bool) or not isinstance(length, int):
        raise TypeError(f"sequence length must be an int, got {type(length).__name__}")
    if length < 2:
        raise ValueError(f"a sequence needs at least 2 frames to be split, got {length}")
    middle = length // 2
    return TuningHalf(1, middle), ValidationHalf(middle + 1, length)
