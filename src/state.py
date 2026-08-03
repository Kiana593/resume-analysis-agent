"""Agent 状态定义 —— 图节点间传递的数据结构。"""

from typing import TypedDict, Optional, Dict, Any, List


class AgentState(TypedDict, total=False):
    """LangGraph 图状态（双流程共用）。

    节点通过返回部分字段来更新状态，无需显式 reducer（LangGraph 默认浅合并）。
    """

    # ==================== 提取流程 (extract_flow) ====================
    file_path: str
    file_type: str  # "pdf" | "docx" | "unknown"
    raw_text: str
    is_markdown: bool  # True 表示 raw_text 为 Markdown 格式
    extraction_schema: Dict[str, Any]
    user_requirements: Optional[str]  # 用户指定的重点关注方向
    extraction_result: Optional[Dict[str, Any]]  # 五维提取结果

    # ==================== 分析流程 (analysis_flow) ====================
    resume_json: Optional[str]            # 简历 JSON 文件路径（load_resume 使用）
    jd_json: Optional[str]                # JD JSON 文件路径（load_jd 使用）
    resume_data: Optional[Dict[str, Any]]  # 简历 JSON（含 five_dim + raw_text）
    jd_data: Optional[Dict[str, Any]]      # JD JSON（含 five_dim + raw_text）
    analysis_result: Optional[Dict[str, Any]]  # 差距分析 + 学习路径
    verify_result: Optional[Dict[str, Any]]    # 幻觉校验结果
    retry_count: int                       # verify 失败重试计数（上限 2）
    retry_feedback: Optional[str]          # 上次校验失败的错误清单（注入 prompt）

    # ==================== 通用 ====================
    error: Optional[str]
