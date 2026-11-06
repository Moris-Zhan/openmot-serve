import pytest

from eval.split import FrameRange, TuningHalf, ValidationHalf, split_sequence

MOT17_TRAIN_LENGTHS = {
    "MOT17-02": 600,
    "MOT17-04": 1050,
    "MOT17-05": 837,
    "MOT17-09": 525,
    "MOT17-10": 654,
    "MOT17-11": 900,
    "MOT17-13": 750,
}


def test_even_length_sequence_splits_in_the_middle() -> None:
    tuning, validation = split_sequence(600)
    assert (tuning.first, tuning.last) == (1, 300)
    assert (validation.first, validation.last) == (301, 600)


def test_odd_length_sequence_gives_the_extra_frame_to_validation() -> None:
    tuning, validation = split_sequence(525)
    assert (tuning.first, tuning.last) == (1, 262)
    assert (validation.first, validation.last) == (263, 525)


@pytest.mark.parametrize("length", MOT17_TRAIN_LENGTHS.values(), ids=MOT17_TRAIN_LENGTHS.keys())
def test_halves_do_not_overlap_and_cover_every_frame(length: int) -> None:
    tuning, validation = split_sequence(length)
    tuning_frames = set(tuning.frames())
    validation_frames = set(validation.frames())
    assert tuning_frames.isdisjoint(validation_frames)
    assert tuning_frames | validation_frames == set(range(1, length + 1))
    assert tuning.last + 1 == validation.first
    assert tuning.count == length // 2


@pytest.mark.parametrize("length", range(2, 40))
def test_every_small_length_is_partitioned_exactly(length: int) -> None:
    tuning, validation = split_sequence(length)
    assert list(tuning.frames()) + list(validation.frames()) == list(range(1, length + 1))
    assert tuning.count >= 1
    assert validation.count >= tuning.count


def test_halves_have_distinct_types_so_tuning_can_reject_validation() -> None:
    tuning, validation = split_sequence(10)
    assert isinstance(tuning, TuningHalf)
    assert isinstance(validation, ValidationHalf)
    assert not isinstance(validation, TuningHalf)
    assert isinstance(tuning, FrameRange)


@pytest.mark.parametrize("length", [-5, 0, 1])
def test_sequences_too_short_to_split_are_rejected(length: int) -> None:
    with pytest.raises(ValueError, match="at least 2"):
        split_sequence(length)


def test_a_non_integer_length_is_rejected() -> None:
    with pytest.raises(TypeError):
        split_sequence(10.0)  # type: ignore[arg-type]
