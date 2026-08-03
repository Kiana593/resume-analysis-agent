"""数据加载节点 —— 读取简历 JSON 与 JD JSON 到 state。"""

import json
from pathlib import Path

from src.state import AgentState


def load_resume(state: AgentState) -> dict:
    """加载简历数据。

    优先使用 state 中已就绪的 resume_data（CLI 层提取后直传）；
    否则从 resume_json 文件路径读取。

    Returns:
        包含 resume_data 的字典，或含 error。
    """
    if state.get("resume_data"):
        return {"resume_data": state["resume_data"]}

    resume_json = state.get("resume_json", "")
    if not resume_json:
        return {"error": "未提供 resume_json 路径且 resume_data 为空"}

    path = Path(resume_json)
    if not path.exists():
        return {"error": f"简历 JSON 不存在: {resume_json}"}

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            resume_data = json.load(f)
    except json.JSONDecodeError as exc:
        return {"error": f"简历 JSON 解析失败: {exc}"}

    if "five_dim" not in resume_data:
        return {"error": f"简历 JSON 缺少 five_dim 字段: {resume_json}"}

    return {"resume_data": resume_data}


def load_jd(state: AgentState) -> dict:
    """加载 JD 数据。

    优先使用 state 中已就绪的 jd_data；否则从 jd_json 文件路径读取。

    Returns:
        包含 jd_data 的字典，或含 error。
    """
    if state.get("jd_data"):
        return {"jd_data": state["jd_data"]}

    jd_json = state.get("jd_json", "")
    if not jd_json:
        return {"error": "未提供 jd_json 路径且 jd_data 为空"}

    path = Path(jd_json)
    if not path.exists():
        return {"error": f"JD JSON 不存在: {jd_json}"}

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            jd_data = json.load(f)
    except json.JSONDecodeError as exc:
        return {"error": f"JD JSON 解析失败: {exc}"}

    if "five_dim" not in jd_data:
        return {"error": f"JD JSON 缺少 five_dim 字段: {jd_json}"}

    return {"jd_data": jd_data}
