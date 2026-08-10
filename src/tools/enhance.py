"""enhance_matches 工具 — 用一次 LLM 调用复核 Top-N 命中。"""

import json
from typing import Any, Dict, Optional

from ..prompts.enhance import ENHANCE_PROMPT
from ..utils.llm import call_deepseek_json


def _trim_rank_result(rank_result: Dict[str, Any], topk: int) -> Dict[str, Any]:
    """截取 rank 结果的前 topk 名，并精简每项的维度明细（去掉 miss 列表冗余）。"""
    results = rank_result.get("results", [])[:topk]
    trimmed = []
    for item in results:
        dims = {}
        for dim, detail in (item.get("dimensions") or {}).items():
            dims[dim] = {
                "hit": detail.get("hit", []),
                "miss": detail.get("miss", []),
            }
        trimmed.append(
            {
                "role_name": item.get("role_name", ""),
                "score": item.get("score", 0.0),
                "hit_skills": item.get("hit_skills", 0),
                "total_skills": item.get("total_skills", 0),
                "dimensions": dims,
            }
        )
    return {"topk": len(trimmed), "results": trimmed}


def enhance_matches(
    rank_result: Dict[str, Any],
    resume_text: str,
    topk: int = 20,
    llm_func: Optional[Any] = None,
) -> Dict[str, Any]:
    """调用 LLM 复核排名结果，修正关键词命中的误判。

    Args:
        rank_result: rank_resume() 的返回（含 results 列表）。
        resume_text: 简历 Markdown 原文。
        topk: 复核前 N 名，默认 20。
        llm_func: 可注入的 LLM 调用函数（测试用），缺省用 call_deepseek_json。

    Returns:
        LLM 修正后的 JSON：
        {"topk": N, "results": [{"role_name", "score", "hit_skills",
                                 "total_skills", "review_note", "dimensions"}, ...]}
    """
    text = (resume_text or "").strip()
    if not text:
        raise ValueError("resume_text 不能为空。")
    if not rank_result or not rank_result.get("results"):
        raise ValueError("rank_result 为空，请先调用 rank_resume。")

    trimmed = _trim_rank_result(rank_result, topk)
    prompt = ENHANCE_PROMPT.format(
        topk=len(trimmed["results"]),
        resume_text=text[:12000],  # 控制 token 成本
        rank_json=json.dumps(trimmed, ensure_ascii=False),
    )
    caller = llm_func or call_deepseek_json
    result = caller(prompt)
    if not isinstance(result, dict):
        raise RuntimeError("LLM 复核返回格式异常：期望 JSON 对象。")
    return result
