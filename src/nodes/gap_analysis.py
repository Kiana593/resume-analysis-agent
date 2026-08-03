"""差距分析节点 —— LLM 对比简历六维 vs JD 六维，输出维度级差距清单。

输出结构：
- dimensions: 六维逐一分析（gap_level + summary + missing/satisfied 要点）
- extra_gaps: JD 原文额外发现要求（简洁要点）
- overall_summary / match: 整体结论
"""

from typing import Any, Dict

from src.state import AgentState
from src.utils.llm import call_deepseek_json
from src.prompts.gap_analysis import GAP_ANALYSIS_PROMPT

# 六个维度的固定顺序
DIMENSION_KEYS = ("knowledge", "skill", "qualifications", "motivation", "trait", "self_concept")


def _format_five_dim(five_dim: Dict[str, Any]) -> str:
    """将六维 dict 格式化为易读文本（每维一行）。"""
    lines = []
    labels = {
        "personal_info": "基本信息",
        "knowledge": "知识",
        "skill": "技术",
        "qualifications": "任职条件",
        "motivation": "动机",
        "trait": "特质",
        "self_concept": "自我概念",
    }
    for key in DIMENSION_KEYS:
        if key not in five_dim:
            continue
        value = five_dim[key]
        label = labels.get(key, key)
        if isinstance(value, list):
            items = "；".join(str(v) for v in value) if value else "（无）"
            lines.append(f"- {label}: {items}")
        else:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def gap_analysis(state: AgentState) -> dict:
    """调用 DeepSeek 对比简历与 JD，生成维度级差距清单。

    节点同时负责递增 retry_count 并注入上次校验反馈。
    """
    resume = state.get("resume_data", {})
    jd = state.get("jd_data", {})

    if not resume:
        return {"error": "resume_data 为空，无法进行差距分析"}
    if not jd:
        return {"error": "jd_data 为空，无法进行差距分析"}

    retry_count = state.get("retry_count", 0) + 1

    requirements_block = ""
    if state.get("user_requirements"):
        requirements_block = f"\n## 用户重点关注\n{state['user_requirements']}\n"

    retry_block = ""
    if state.get("retry_feedback"):
        retry_block = f"\n## 上次校验反馈（必须修正）\n{state['retry_feedback']}\n"

    prompt = GAP_ANALYSIS_PROMPT.format(
        resume_five_dim=_format_five_dim(resume.get("five_dim", {})),
        jd_five_dim=_format_five_dim(jd.get("five_dim", {})),
        jd_raw_text=jd.get("raw_text", "（无原文）"),
        resume_raw_text=resume.get("raw_text", "（无原文）"),
        requirements_block=requirements_block + retry_block,
    )

    try:
        gaps_result = call_deepseek_json(prompt)
    except Exception as exc:
        return {"error": f"差距分析 LLM 调用失败: {exc}"}

    # 构建全新 analysis_result，避免复用共享引用
    analysis_result = {
        "dimensions": gaps_result.get("dimensions", {}),
        "extra_gaps": gaps_result.get("extra_gaps", []),
        "overall_summary": gaps_result.get("overall_summary", ""),
        "match": gaps_result.get("match", {}),
    }

    return {"analysis_result": analysis_result, "retry_count": retry_count}
