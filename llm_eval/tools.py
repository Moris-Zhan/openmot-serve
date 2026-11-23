from collections.abc import Sequence
from typing import Any

from llm_eval.results import Detection

_NUMBER = (int, float)


def _function(name: str, description: str, properties: dict[str, dict[str, str]], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _function(
        "count_unique_ids",
        "Count the distinct track ids in the result. By default every id counts (raw count); min_frames keeps "
        "only ids that appear in at least that many frames.",
        {"min_frames": {"type": "integer", "description": "Keep ids present in at least this many frames (>= 1)."}},
        [],
    ),
    _function(
        "track_span",
        "First and last frame in which a track id appears.",
        {"track_id": {"type": "integer", "description": "The track id."}},
        ["track_id"],
    ),
    _function(
        "people_in_frame",
        "Number of distinct track ids present in one frame (frames start at 1).",
        {"frame": {"type": "integer", "description": "Frame number, starting at 1."}},
        ["frame"],
    ),
    _function(
        "dwell_frames",
        "Number of frames in which a track id appears (frames, not seconds: the result has no frame rate).",
        {"track_id": {"type": "integer", "description": "The track id."}},
        ["track_id"],
    ),
    _function(
        "ids_in_region",
        "Track ids whose box bottom-centre (x + w/2, y + h) lies inside the pixel rectangle, borders included, "
        "optionally limited to a frame range.",
        {
            "x_min": {"type": "number", "description": "Left edge in pixels."},
            "y_min": {"type": "number", "description": "Top edge in pixels."},
            "x_max": {"type": "number", "description": "Right edge in pixels."},
            "y_max": {"type": "number", "description": "Bottom edge in pixels."},
            "frame_start": {"type": "integer", "description": "First frame to consider."},
            "frame_end": {"type": "integer", "description": "Last frame to consider."},
        },
        ["x_min", "y_min", "x_max", "y_max"],
    ),
]

_SCHEMA_BY_NAME = {item["function"]["name"]: item["function"]["parameters"] for item in TOOL_SCHEMAS}


def _type_ok(kind: str, value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) if kind == "integer" else isinstance(value, _NUMBER)


def validate_arguments(name: str, arguments: dict[str, Any]) -> list[str]:
    schema = _SCHEMA_BY_NAME.get(name)
    if schema is None:
        return [f"unknown tool {name!r}"]
    problems = [f"missing required argument {key!r}" for key in schema["required"] if key not in arguments]
    for key, value in arguments.items():
        spec = schema["properties"].get(key)
        if spec is None:
            problems.append(f"unexpected argument {key!r}")
        elif not _type_ok(spec["type"], value):
            problems.append(f"argument {key!r} must be {spec['type']}")
    if problems:
        return problems
    if name == "count_unique_ids" and arguments.get("min_frames", 1) < 1:
        problems.append("min_frames must be at least 1")
    if name == "people_in_frame" and arguments["frame"] < 1:
        problems.append("frame must be at least 1")
    if name == "ids_in_region":
        if arguments["x_min"] > arguments["x_max"]:
            problems.append("x_min must not exceed x_max")
        if arguments["y_min"] > arguments["y_max"]:
            problems.append("y_min must not exceed y_max")
    return problems


def _frames_per_id(detections: Sequence[Detection]) -> dict[int, set[int]]:
    seen: dict[int, set[int]] = {}
    for detection in detections:
        seen.setdefault(detection.track_id, set()).add(detection.frame)
    return seen


def execute_tool(name: str, arguments: dict[str, Any], detections: Sequence[Detection]) -> dict[str, Any]:
    problems = validate_arguments(name, arguments)
    if problems:
        raise ValueError("; ".join(problems))
    if name == "count_unique_ids":
        minimum = arguments.get("min_frames", 1)
        return {"count": sum(1 for frames in _frames_per_id(detections).values() if len(frames) >= minimum)}
    if name == "track_span":
        frames = _frames_per_id(detections).get(arguments["track_id"])
        if not frames:
            return {"found": False}
        return {"found": True, "first_frame": min(frames), "last_frame": max(frames)}
    if name == "people_in_frame":
        return {"count": len({d.track_id for d in detections if d.frame == arguments["frame"]})}
    if name == "dwell_frames":
        return {"frames": len(_frames_per_id(detections).get(arguments["track_id"], set()))}
    start, end = arguments.get("frame_start"), arguments.get("frame_end")
    inside = {
        d.track_id
        for d in detections
        if (start is None or d.frame >= start)
        and (end is None or d.frame <= end)
        and arguments["x_min"] <= d.x + d.width / 2 <= arguments["x_max"]
        and arguments["y_min"] <= d.y + d.height <= arguments["y_max"]
    }
    return {"ids": sorted(inside)}
