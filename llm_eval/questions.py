import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from llm_eval import oracle
from llm_eval.results import read_results
from llm_eval.synthetic import write_synthetic

CATEGORIES = ("image_content", "missing_sequence_info", "cross_sequence", "prediction", "real_identity")
DEFAULT_SEED = 20261119
EMPTY_REGION = (100000, 100000, 100100, 100100)


@dataclass(frozen=True)
class Question:
    id: str
    source: str
    text: str
    answerable: bool
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    gold: Any = None
    category: str | None = None
    frames_alternative: int | None = None


def _gold(tool: str, arguments: dict[str, Any], path: Path) -> Any:
    if tool == "count_unique_ids":
        return oracle.unique_ids(path, arguments.get("min_frames", 1))
    if tool == "track_span":
        return list(oracle.track_span(path, arguments["track_id"]) or ())
    if tool == "people_in_frame":
        return oracle.people_in_frame(path, arguments["frame"])
    if tool == "dwell_frames":
        return oracle.dwell_frames(path, arguments["track_id"])
    return oracle.ids_in_region(
        path,
        arguments["x_min"],
        arguments["y_min"],
        arguments["x_max"],
        arguments["y_max"],
        arguments.get("frame_start"),
        arguments.get("frame_end"),
    )


def _answerable(number: int, source: str, text: str, tool: str, arguments: dict[str, Any], path: Path) -> Question:
    return Question(f"q{number:02d}", source, text, True, tool, arguments, _gold(tool, arguments, path))


def _box(x_min: float, y_min: float, x_max: float, y_max: float) -> dict[str, int]:
    return {"x_min": round(x_min), "y_min": round(y_min), "x_max": round(x_max), "y_max": round(y_max)}


def _region_text(box: dict[str, int], frames: tuple[int, int] | None = None) -> str:
    where = f"在第 {frames[0]} 到第 {frames[1]} 張影格之間，" if frames else ""
    return (
        f"{where}框底邊中心點落在 x 介於 {box['x_min']} 到 {box['x_max']}、"
        f"y 介於 {box['y_min']} 到 {box['y_max']}（像素）的矩形內的身分編號有哪些？"
    )


def generate_questions(paths: dict[str, Path], seed: int = DEFAULT_SEED) -> list[Question]:
    rng = random.Random(seed)
    real = paths["real"]
    detections = read_results(real)
    ids = sorted({d.track_id for d in detections})
    frames = sorted({d.frame for d in detections})
    questions: list[Question] = []

    def add(source: str, text: str, tool: str, arguments: dict[str, Any]) -> None:
        questions.append(_answerable(len(questions) + 1, source, text, tool, arguments, paths[source]))

    add("real", "這段追蹤結果共有幾個不同的身分？", "count_unique_ids", {})
    for minimum in (5, 20):
        add(
            "real",
            f"只算至少出現在 {minimum} 張影格的身分，這段追蹤結果共有幾個不同的身分？",
            "count_unique_ids",
            {"min_frames": minimum},
        )
    for identity in rng.sample(ids, 3):
        add("real", f"編號 {identity} 的人第一次與最後一次出現在第幾張影格？", "track_span", {"track_id": identity})
    for frame in rng.sample(frames, 3):
        add("real", f"第 {frame} 張影格裡有幾個人？", "people_in_frame", {"frame": frame})
    for identity in rng.sample(ids, 3):
        add("real", f"編號 {identity} 總共出現在幾張影格？", "dwell_frames", {"track_id": identity})
    feet_x = sorted(d.x + d.width / 2 for d in detections)
    feet_y = sorted(d.y + d.height for d in detections)
    middle = len(detections) // 2
    centre = _box(feet_x[middle] - 150, feet_y[middle] - 150, feet_x[middle] + 150, feet_y[middle] + 150)
    add("real", f"請問{_region_text(centre)}", "ids_in_region", centre)
    sample = rng.choice(detections)
    around = _box(
        sample.x + sample.width / 2 - 200,
        sample.y + sample.height - 200,
        sample.x + sample.width / 2 + 200,
        sample.y + sample.height + 200,
    )
    first, last = sorted(rng.sample(frames, 2))
    add(
        "real",
        f"請問{_region_text(around, (first, last))}",
        "ids_in_region",
        {**around, "frame_start": first, "frame_end": last},
    )
    nobody = dict(zip(("x_min", "y_min", "x_max", "y_max"), EMPTY_REGION, strict=True))
    add("real", f"請問{_region_text(nobody)}", "ids_in_region", nobody)

    add("empty", "這段追蹤結果共有幾個不同的身分？", "count_unique_ids", {})
    add("single", "這段追蹤結果共有幾個不同的身分？", "count_unique_ids", {})
    add("gaps", "編號 2 的人第一次與最後一次出現在第幾張影格？", "track_span", {"track_id": 2})
    add("gaps", "編號 2 總共出現在幾張影格？", "dwell_frames", {"track_id": 2})
    add("crowd", "第 7 張影格裡有幾個人？", "people_in_frame", {"frame": 7})
    add("crowd", "這段追蹤結果共有幾個不同的身分？", "count_unique_ids", {})

    identity = rng.choice(ids)
    unanswerable = [
        ("畫面中穿紅色衣服的人有幾個？", "image_content", None),
        (f"編號 {identity} 的人是在跑步還是在走路？", "image_content", None),
        ("畫面左半邊有幾個人？", "missing_sequence_info", None),
        (
            f"編號 {identity} 在畫面中停留了幾秒？",
            "missing_sequence_info",
            oracle.dwell_frames(real, identity),
        ),
        ("這段影像的解析度是多少？", "missing_sequence_info", None),
        ("這段結果和 MOT17-04 比，哪一段的人比較多？", "cross_sequence", None),
        (f"第 {frames[-1] + 10} 張影格預計會有幾個人？", "prediction", None),
        (f"編號 {identity} 的人接下來會往哪個方向走？", "prediction", None),
        (f"編號 {identity} 的人是誰？叫什麼名字？", "real_identity", None),
    ]
    for text, category, alternative in unanswerable:
        number = len(questions) + 1
        questions.append(
            Question(f"q{number:02d}", "real", text, False, category=category, frames_alternative=alternative)
        )
    return questions


def dump_questions(questions: list[Question]) -> str:
    return json.dumps([asdict(question) for question in questions], ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_questions(text: str) -> list[Question]:
    return [Question(**item) for item in json.loads(text)]


def build_question_set(real_result: Path, output_dir: Path, seed: int = DEFAULT_SEED) -> Path:
    paths = {"real": real_result, **write_synthetic(output_dir / "synthetic")}
    target = output_dir / "questions.json"
    target.write_text(dump_questions(generate_questions(paths, seed)), encoding="utf-8")
    return target
