"""analyze_gap 工具 — 基于结构化命中清单的 LLM 差距分析 + 学习路径。"""

import json
from typing import Any, Dict, Optional

from ..core.dimensions import DIM_LABELS
from ..prompts.gap_analysis import ROLE_GAP_PROMPT
from ..utils.llm import call_llm_json


def _build_dimension_details(role: Dict[str, Any]) -> str:
    """把单 Role 的七维命中明细格式化为 prompt 输入块。"""
    dims = role.get("dimensions") or {}
    lines = []
    for dim in ("knowledge", "skill", "qualifications", "preference",
                "motivation", "trait", "self_concept"):
        detail = dims.get(dim) or {}
        label = DIM_LABELS.get(dim, dim)
        hit = detail.get("hit", [])
        miss = detail.get("miss", [])
        lines.append(f"- {label} ({dim}): 命中={hit or '无'} | 缺失={miss or '无'}")
    return "\n".join(lines)


def _format_markdown(role: Dict[str, Any], llm_result: Dict[str, Any]) -> str:
    """把 LLM JSON 结果格式化为可读 Markdown。"""
    name = role.get("role_name", "目标岗位")
    match = llm_result.get("match") or {}
    verdict = "匹配" if match.get("verdict") == "yes" else "不匹配"
    md = [
        f"## {name} — 差距分析",
        "",
        f"**匹配结论**: {verdict}  ·  {match.get('reason', '')}",
        "",
    ]
    dims = llm_result.get("dimensions") or {}
    for dim, label in DIM_LABELS.items():
        detail = dims.get(dim) or {}
        if not detail:
            continue
        md.append(f"### {label}")
        md.append(detail.get("summary", ""))
        md.append("")
    if llm_result.get("overall_summary"):
        md.append("### 总体建议")
        md.append(llm_result["overall_summary"])
        md.append("")
    learning_path = llm_result.get("learning_path") or []
    if learning_path:
        md.append("### 学习路径")
        for step in learning_path:
            md.append(f"{step.get('step', '?')}. **{step.get('skill', '')}**"
                      f"（{step.get('importance', '')}）")
        md.append("")
    return "\n".join(md)


def prepare_gap(role: Dict[str, Any], resume_text: str) -> Dict[str, Any]:
    """Agent mode: build the gap-analysis payload without calling any LLM API.

    The agent uses its own model to write the gap analysis + learning path,
    and returns a Markdown report directly (no server-side normalization).
    """
    text = (resume_text or "").strip()
    if not role or not role.get("role_name"):
        raise ValueError("role invalid: missing role_name")
    prompt = ROLE_GAP_PROMPT.format(
        role_name=role.get("role_name", ""),
        family_name=role.get("family_name", ""),
        domain_name=role.get("domain_name", ""),
        dimension_details=_build_dimension_details(role),
        resume_raw_text=text[:12000],
    )
    return {
        "mode": "agent_analysis",
        "purpose": "Write a gap analysis and learning path with your own model",
        "prompt": prompt,
        "role_name": role.get("role_name", ""),
        "dimension_details": _build_dimension_details(role),
        "resume_text": text[:12000],
        "output_format": (
            "Markdown report with: match verdict, per-dimension analysis, "
            "overall advice, numbered learning path"
        ),
    }


def analyze_gap(
    role: Dict[str, Any],
    resume_text: str,
    llm_func: Optional[Any] = None,
) -> Dict[str, Any]:
    """生成单个 Role 的差距分析与学习路径。

    Args:
        role: 单个 Role 的完整 JSON（含 dimensions 命中明细），
              来自 rank_resume() 的 results[i]。
        resume_text: 简历 Markdown 原文。
        llm_func: 可注入的 LLM 调用函数（测试用）。

    Returns:
        {
            "role_name": str,
            "analysis": {...},   # LLM 原始 JSON（match/dimensions/learning_path...）
            "markdown": str,     # 格式化后的 Markdown
        }
    """
    text = (resume_text or "").strip()
    if not text:
        raise ValueError("resume_text 不能为空。")
    if not role or not role.get("role_name"):
        raise ValueError("role 无效：缺少 role_name。")

    prompt = ROLE_GAP_PROMPT.format(
        role_name=role.get("role_name", ""),
        family_name=role.get("family_name", ""),
        domain_name=role.get("domain_name", ""),
        dimension_details=_build_dimension_details(role),
        resume_raw_text=text[:12000],
    )
    caller = llm_func or call_llm_json
    llm_result = caller(prompt)
    if not isinstance(llm_result, dict):
        raise RuntimeError("LLM 差距分析返回格式异常：期望 JSON 对象。")

    return {
        "role_name": role.get("role_name", ""),
        "analysis": llm_result,
        "markdown": _format_markdown(role, llm_result),
    }
