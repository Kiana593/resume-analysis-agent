"""学习路径节点 —— LLM 基于维度差距生成简洁学习路径（仅技术栈，失败自动重试一次）。"""

import json

from src.state import AgentState
from src.utils.llm import call_deepseek_json
from src.prompts.gap_analysis import LEARNING_PATH_PROMPT

# 重试时附加的严格 JSON 提示
_STRICT_HINT = """请特别注意：输出必须是合法 JSON 对象。所有字符串用英文双引号，字符串内的引号必须转义，禁止尾随逗号。再次尝试输出完整的 learning_path JSON。"""

# 维度与缺失要点合并到 prompt 时的 importance：六维条目 high，extra_gaps 用自身值
_CORE_DIMS = ("knowledge", "skill", "qualifications")


def _flatten_dimension_inputs(dimensions: dict, extra_gaps: list) -> list[dict]:
    """把维度级差距扁平化为条目列表，供 learning path LLM 使用。"""
    entries: list[dict] = []

    for dim_key, dim_data in dimensions.items():
        if not isinstance(dim_data, dict):
            continue
        gap_level = dim_data.get("gap_level")
        if gap_level not in ("missing", "partial"):
            continue
        # 六维条目的 importance 固定 high
        for item in dim_data.get("missing", []) or []:
            if isinstance(item, str) and item.strip():
                entries.append({
                    "dimension": dim_key,
                    "item": item.strip(),
                    "gap_level": gap_level,
                    "importance": "high" if dim_key in _CORE_DIMS else "high",
                })

    for extra in extra_gaps:
        if isinstance(extra, dict) and extra.get("item"):
            entries.append({
                "item": extra["item"],
                "gap_level": extra.get("gap_level", "missing"),
                "importance": extra.get("importance", "high"),
            })

    return entries


def learning_path(state: AgentState) -> dict:
    """调用 DeepSeek 生成简洁学习路径（仅技术栈 + 优先级）。

    解析失败或结果为空时自动重试一次（最多 2 次调用）。
    """
    analysis = state.get("analysis_result", {})
    dimensions = analysis.get("dimensions", {})
    extra_gaps = analysis.get("extra_gaps", [])

    entries = _flatten_dimension_inputs(dimensions, extra_gaps)
    if not entries:
        analysis["learning_path"] = []
        return {"analysis_result": analysis}

    gaps_json = json.dumps(entries, ensure_ascii=False, indent=2)
    prompt = LEARNING_PATH_PROMPT.format(gaps_json=gaps_json)

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            path_result = call_deepseek_json(prompt)
            steps = path_result.get("learning_path", [])
            if steps:
                analysis["learning_path"] = steps
                return {"analysis_result": analysis}
            last_error = RuntimeError("LLM 返回的 learning_path 为空")
        except Exception as exc:
            last_error = exc
        prompt = _STRICT_HINT + "\n\n" + prompt

    return {"error": f"学习路径生成失败: {last_error}"}
