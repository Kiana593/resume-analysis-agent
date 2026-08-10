"""简历存储节点 —— 提取完成后保存简历 JSON 到 results/。"""

import json
import re
import time
from pathlib import Path

from src.state import AgentState

# results/ 位于项目根目录
RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results"


def save_resume(state: AgentState) -> dict:
    """将五维提取结果 + 原文保存为 JSON 文件。

    Returns:
        包含 resume_data（含 five_dim + raw_text）的字典，或含 error。
    """
    result = state.get("extraction_result")
    file_path = state.get("file_path", "")

    if not result:
        return {"error": "extraction_result 为空，无法保存"}

    RESULTS_DIR.mkdir(exist_ok=True)

    source = Path(file_path)
    safe_stem = re.sub(r"[^\w\-]", "_", source.stem)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{safe_stem}_{timestamp}.json"

    resume_data = {
        "file_name": source.name,
        "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "raw_text": state.get("raw_text", ""),
        "is_markdown": state.get("is_markdown", False),
        "five_dim": result,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(resume_data, f, ensure_ascii=False, indent=2)

    print(f"\n  [save_resume] 已保存: {out_path}")
    return {"resume_data": resume_data}
