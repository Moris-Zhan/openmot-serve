import ast
import random
from pathlib import Path

import pytest

from llm_eval import oracle, tools
from llm_eval.results import read_results
from tests.test_llm_eval_oracle import HAND_ROWS, write_rows

REAL_RESULT = Path(__file__).resolve().parents[1] / "data/results/full-fp16-3/results/MOT17-09-FRCNN.txt"
PACKAGE = Path(__file__).resolve().parents[1] / "llm_eval"


def _imports(module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse((PACKAGE / f"{module}.py").read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_five_tools_have_the_documented_schemas() -> None:
    schemas = {item["function"]["name"]: item["function"]["parameters"] for item in tools.TOOL_SCHEMAS}
    assert set(schemas) == {"count_unique_ids", "track_span", "people_in_frame", "dwell_frames", "ids_in_region"}
    assert schemas["count_unique_ids"]["required"] == []
    assert schemas["track_span"]["required"] == ["track_id"]
    assert schemas["people_in_frame"]["required"] == ["frame"]
    assert schemas["dwell_frames"]["required"] == ["track_id"]
    assert schemas["ids_in_region"]["required"] == ["x_min", "y_min", "x_max", "y_max"]
    assert all(item["type"] == "function" for item in tools.TOOL_SCHEMAS)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("count_unique_ids", {}),
        ("count_unique_ids", {"min_frames": 3}),
        ("track_span", {"track_id": 4}),
        ("people_in_frame", {"frame": 2}),
        ("dwell_frames", {"track_id": 4}),
        ("ids_in_region", {"x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10}),
        ("ids_in_region", {"x_min": 0, "y_min": 0, "x_max": 10.5, "y_max": 10, "frame_start": 1, "frame_end": 9}),
    ],
)
def test_valid_arguments_pass_validation(name: str, arguments: dict) -> None:
    assert tools.validate_arguments(name, arguments) == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("no_such_tool", {}),
        ("track_span", {}),
        ("track_span", {"track_id": "4"}),
        ("track_span", {"track_id": True}),
        ("track_span", {"track_id": 4, "extra": 1}),
        ("count_unique_ids", {"min_frames": 0}),
        ("people_in_frame", {"frame": 0}),
        ("ids_in_region", {"x_min": 10, "y_min": 0, "x_max": 0, "y_max": 10}),
        ("ids_in_region", {"x_min": 0, "y_min": 10, "x_max": 10, "y_max": 0}),
        ("ids_in_region", {"x_min": 0, "y_min": 0, "x_max": 10}),
    ],
)
def test_invalid_arguments_are_rejected_with_a_reason(name: str, arguments: dict) -> None:
    assert tools.validate_arguments(name, arguments)


def test_execution_returns_the_documented_shapes(tmp_path: Path) -> None:
    detections = read_results(write_rows(tmp_path / "r.txt", HAND_ROWS))
    assert tools.execute_tool("count_unique_ids", {"min_frames": 2}, detections) == {"count": 2}
    assert tools.execute_tool("track_span", {"track_id": 2}, detections) == {
        "found": True,
        "first_frame": 1,
        "last_frame": 5,
    }
    assert tools.execute_tool("track_span", {"track_id": 99}, detections) == {"found": False}
    assert tools.execute_tool("people_in_frame", {"frame": 2}, detections) == {"count": 3}
    assert tools.execute_tool("dwell_frames", {"track_id": 2}, detections) == {"frames": 3}
    region = {"x_min": 0, "y_min": 0, "x_max": 200, "y_max": 200}
    assert tools.execute_tool("ids_in_region", region, detections) == {"ids": [1, 2, 3]}


def _agree(path: Path) -> None:
    detections = read_results(path)
    ids = sorted({d.track_id for d in detections}) + [10**6]
    frames = sorted({d.frame for d in detections}) + [10**6]
    for min_frames in (1, 2, 5):
        assert tools.execute_tool("count_unique_ids", {"min_frames": min_frames}, detections)["count"] == (
            oracle.unique_ids(path, min_frames)
        )
    for identity in ids:
        span = oracle.track_span(path, identity)
        got = tools.execute_tool("track_span", {"track_id": identity}, detections)
        if span is None:
            assert got == {"found": False}
        else:
            assert (got["first_frame"], got["last_frame"]) == span
        assert tools.execute_tool("dwell_frames", {"track_id": identity}, detections)["frames"] == (
            oracle.dwell_frames(path, identity)
        )
    for frame in frames:
        assert tools.execute_tool("people_in_frame", {"frame": frame}, detections)["count"] == (
            oracle.people_in_frame(path, frame)
        )
    for box in ((0, 0, 500, 500), (100, 100, 900, 700), (0, 0, 1, 1)):
        arguments = {"x_min": box[0], "y_min": box[1], "x_max": box[2], "y_max": box[3]}
        assert tools.execute_tool("ids_in_region", arguments, detections)["ids"] == oracle.ids_in_region(path, *box)
        ranged = {**arguments, "frame_start": 3, "frame_end": 9}
        assert tools.execute_tool("ids_in_region", ranged, detections)["ids"] == oracle.ids_in_region(
            path, *box, frame_start=3, frame_end=9
        )


def test_tools_and_oracle_agree_on_the_hand_sequence(tmp_path: Path) -> None:
    _agree(write_rows(tmp_path / "r.txt", HAND_ROWS))


@pytest.mark.parametrize("seed", range(5))
def test_tools_and_oracle_agree_on_random_sequences(tmp_path: Path, seed: int) -> None:
    generator = random.Random(seed)
    rows = [
        (frame, identity, generator.uniform(0, 600), generator.uniform(0, 400), 20.0, 40.0)
        for frame in range(1, 15)
        for identity in range(1, 8)
        if generator.random() < 0.6
    ]
    _agree(write_rows(tmp_path / "r.txt", rows))


@pytest.mark.skipif(not REAL_RESULT.is_file(), reason=f"real MOT17-09 result not found at {REAL_RESULT}")
def test_tools_and_oracle_agree_on_the_real_mot17_09_result() -> None:
    _agree(REAL_RESULT)


def test_the_oracle_and_the_tools_share_no_code() -> None:
    assert not {name for name in _imports("oracle") if name.startswith("llm_eval")}
    assert not any(name.startswith(("llm_eval.oracle", "tests")) for name in _imports("tools"))
    assert "llm_eval" not in _imports("tools")
