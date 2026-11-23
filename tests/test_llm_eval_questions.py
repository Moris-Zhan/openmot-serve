import random
from pathlib import Path

import pytest

from llm_eval import oracle
from llm_eval.questions import (
    CATEGORIES,
    Question,
    dump_questions,
    generate_questions,
    load_questions,
)
from llm_eval.synthetic import SYNTHETIC_SOURCES, write_synthetic

REAL_RESULT = Path(__file__).resolve().parents[1] / "data/results/full-fp16-3/results/MOT17-09-FRCNN.txt"


@pytest.fixture
def stand_in(tmp_path: Path) -> Path:
    generator = random.Random(7)
    rows = [
        f"{frame},{identity},{generator.uniform(0, 1800):.2f},{generator.uniform(0, 900):.2f},60.0,150.0,0.9,-1,-1,-1\n"
        for frame in range(1, 121)
        for identity in range(1, 31)
        if generator.random() < 0.5
    ]
    path = tmp_path / "real.txt"
    path.write_text("".join(rows))
    return path


@pytest.fixture
def synthetic(tmp_path: Path) -> dict[str, Path]:
    return write_synthetic(tmp_path / "synthetic")


def _paths(stand_in: Path, synthetic: dict[str, Path]) -> dict[str, Path]:
    return {"real": stand_in, **synthetic}


def test_generation_is_reproducible_byte_for_byte(stand_in: Path, synthetic: dict[str, Path]) -> None:
    first = dump_questions(generate_questions(_paths(stand_in, synthetic), seed=1))
    second = dump_questions(generate_questions(_paths(stand_in, synthetic), seed=1))
    assert first == second


def test_a_different_seed_changes_the_sampled_parameters(stand_in: Path, synthetic: dict[str, Path]) -> None:
    one = dump_questions(generate_questions(_paths(stand_in, synthetic), seed=1))
    two = dump_questions(generate_questions(_paths(stand_in, synthetic), seed=2))
    assert one != two


def test_the_set_has_about_thirty_questions_with_unique_ids(stand_in: Path, synthetic: dict[str, Path]) -> None:
    questions = generate_questions(_paths(stand_in, synthetic), seed=1)
    assert 28 <= len(questions) <= 32
    assert len({question.id for question in questions}) == len(questions)
    assert sum(question.answerable for question in questions) >= 20


def test_every_unanswerable_category_is_present_and_labelled(stand_in: Path, synthetic: dict[str, Path]) -> None:
    questions = generate_questions(_paths(stand_in, synthetic), seed=1)
    seen = {question.category for question in questions if not question.answerable}
    assert seen == set(CATEGORIES) and len(CATEGORIES) == 5
    assert all(question.category is None for question in questions if question.answerable)


def test_the_synthetic_edge_cases_are_all_covered(stand_in: Path, synthetic: dict[str, Path]) -> None:
    questions = generate_questions(_paths(stand_in, synthetic), seed=1)
    assert set(SYNTHETIC_SOURCES) == {"empty", "single", "gaps", "crowd"}
    for source in SYNTHETIC_SOURCES:
        assert any(question.source == source for question in questions), source


def test_the_synthetic_sequences_have_the_intended_shape(synthetic: dict[str, Path]) -> None:
    assert oracle.unique_ids(synthetic["empty"]) == 0
    assert oracle.unique_ids(synthetic["single"]) == 1
    assert oracle.track_span(synthetic["gaps"], 2) == (3, 12) and oracle.dwell_frames(synthetic["gaps"], 2) == 6
    assert oracle.people_in_frame(synthetic["crowd"], 7) == 40


def test_every_answerable_gold_equals_the_oracle_on_its_source(stand_in: Path, synthetic: dict[str, Path]) -> None:
    paths = _paths(stand_in, synthetic)
    for question in generate_questions(paths, seed=1):
        if not question.answerable:
            continue
        assert question.tool is not None and question.arguments is not None
        path, arguments = paths[question.source], question.arguments
        expected: object
        if question.tool == "count_unique_ids":
            expected = oracle.unique_ids(path, arguments.get("min_frames", 1))
        elif question.tool == "track_span":
            expected = list(oracle.track_span(path, arguments["track_id"]) or ())
        elif question.tool == "people_in_frame":
            expected = oracle.people_in_frame(path, arguments["frame"])
        elif question.tool == "dwell_frames":
            expected = oracle.dwell_frames(path, arguments["track_id"])
        else:
            expected = oracle.ids_in_region(
                path,
                arguments["x_min"],
                arguments["y_min"],
                arguments["x_max"],
                arguments["y_max"],
                arguments.get("frame_start"),
                arguments.get("frame_end"),
            )
        assert question.gold == expected, question.id


def test_the_seconds_question_also_accepts_frames_with_a_unit(stand_in: Path, synthetic: dict[str, Path]) -> None:
    questions = generate_questions(_paths(stand_in, synthetic), seed=1)
    seconds = [question for question in questions if question.frames_alternative is not None]
    assert len(seconds) == 1 and seconds[0].category == "missing_sequence_info" and not seconds[0].answerable
    assert isinstance(seconds[0].frames_alternative, int)


def test_questions_survive_a_json_round_trip(stand_in: Path, synthetic: dict[str, Path]) -> None:
    questions = generate_questions(_paths(stand_in, synthetic), seed=1)
    assert load_questions(dump_questions(questions)) == questions
    assert all(isinstance(question, Question) for question in questions)


@pytest.mark.skipif(not REAL_RESULT.is_file(), reason=f"real MOT17-09 result not found at {REAL_RESULT}")
def test_generation_works_on_the_real_result(synthetic: dict[str, Path]) -> None:
    questions = generate_questions({"real": REAL_RESULT, **synthetic}, seed=1)
    assert 28 <= len(questions) <= 32


def test_building_the_question_set_writes_the_questions_and_the_synthetic_sequences(
    stand_in: Path, tmp_path: Path
) -> None:
    from llm_eval.questions import build_question_set

    output = tmp_path / "llm_eval"
    path = build_question_set(stand_in, output, seed=1)
    assert path == output / "questions.json"
    assert {name: (output / "synthetic" / f"{name}.txt").is_file() for name in SYNTHETIC_SOURCES} == dict.fromkeys(
        SYNTHETIC_SOURCES, True
    )
    assert len(load_questions(path.read_text())) >= 28
