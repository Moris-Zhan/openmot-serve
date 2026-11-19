import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from llm_eval.ollama import OllamaClient, OllamaError
from llm_eval.questions import CATEGORIES, Question
from llm_eval.results import Detection
from llm_eval.score import ToolCall, Trial
from llm_eval.tools import TOOL_SCHEMAS, execute_tool, validate_arguments

MAX_TOOL_ROUNDS = 4
SEED = 20261119
FINAL_KEYS = ("refused", "category", "value", "unit", "definition")

SYSTEM_PROMPT = (
    "你是追蹤結果的查詢助理。使用者問的是一份行人追蹤結果（每一列是影格、身分編號、框的位置與大小）。"
    "數字一律用工具查詢，不要自己計算或猜測。結果檔只有影格、身分與框座標，沒有影像內容、影像尺寸與幀率。"
    "「不同身分數」預設回報原始身分數，只有使用者明確要求才套用最少影格數的過濾，並在答案註明用的定義。"
    "區域只接受明確的像素座標。資料裡沒有的資訊要拒答，並指出原因類別："
    + "、".join(CATEGORIES)
    + "（image_content：影像內容；missing_sequence_info：需要影像尺寸或幀率；cross_sequence：跨序列比較；"
    "prediction：預測未來；real_identity：對應真實人物）。不要猜數字。"
)

FINAL_INSTRUCTION = (
    "請依照格式輸出最終答案：refused 表示是否拒答；拒答時 category 填原因類別；"
    "回答時 value 填數值（身分清單用陣列，第一次與最後一次出現用 [第一張, 最後一張]），"
    "unit 填單位（例如 frames），definition 簡述所用的定義。"
)

FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "refused": {"type": "boolean"},
        "category": {"enum": [*CATEGORIES, None]},
        "value": {"anyOf": [{"type": "number"}, {"type": "array", "items": {"type": "number"}}, {"type": "null"}]},
        "unit": {"type": ["string", "null"]},
        "definition": {"type": ["string", "null"]},
    },
    "required": list(FINAL_KEYS),
}


class _TrialError(RuntimeError):
    pass


def _options() -> dict[str, Any]:
    return {"temperature": 0, "seed": SEED}


def _run_call(call: dict[str, Any], detections: Sequence[Detection]) -> tuple[ToolCall, str]:
    function = call.get("function") or {}
    name, arguments = function.get("name"), function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = None
    if not isinstance(arguments, dict):
        problems = ["arguments are not a JSON object"]
        return ToolCall(str(name), {}, None, False, problems), json.dumps({"error": problems[0]})
    problems = validate_arguments(str(name), arguments)
    if problems:
        return ToolCall(str(name), arguments, None, False, problems), json.dumps({"error": "; ".join(problems)})
    result = execute_tool(str(name), arguments, detections)
    return ToolCall(str(name), arguments, result, True, []), json.dumps(result)


def _parse_final(content: str) -> dict[str, Any]:
    try:
        final = json.loads(content)
    except json.JSONDecodeError as error:
        raise _TrialError(f"final answer is not valid JSON: {content[:120]!r}") from error
    if not isinstance(final, dict) or not set(FINAL_KEYS) <= set(final) or not isinstance(final["refused"], bool):
        raise _TrialError(f"final answer is missing required fields: {content[:120]!r}")
    return final


def run_trial(
    client: OllamaClient,
    model: str,
    question: Question,
    detections: Sequence[Detection],
    run: int,
    warmup: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> Trial:
    started = clock()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question.text},
    ]
    calls: list[ToolCall] = []
    raw: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    error: str | None = None
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = client.chat(
                {"model": model, "messages": messages, "stream": False, "tools": TOOL_SCHEMAS, "options": _options()}
            )
            raw.append(response)
            message = response.get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                messages.append({"role": "assistant", "content": message.get("content", "")})
                break
            messages.append(message)
            for tool_call in tool_calls:
                record, content = _run_call(tool_call, detections)
                calls.append(record)
                messages.append({"role": "tool", "tool_name": record.name, "content": content})
        else:
            raise _TrialError(f"the model kept calling tools after {MAX_TOOL_ROUNDS} tool rounds")
        messages.append({"role": "user", "content": FINAL_INSTRUCTION})
        response = client.chat(
            {"model": model, "messages": messages, "stream": False, "format": FINAL_SCHEMA, "options": _options()}
        )
        raw.append(response)
        final = _parse_final((response.get("message") or {}).get("content", ""))
    except (OllamaError, _TrialError) as failure:
        error = str(failure)
    return Trial(question.id, run, tuple(calls), final, error, clock() - started, warmup, tuple(raw))
