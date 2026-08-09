"""LangGraph 图定义 —— 双流程：简历提取 + 差距分析。"""

import json

from langgraph.graph import StateGraph, END

from src.state import AgentState
from src.nodes.document_loader import load_document
from src.nodes.llm_extractor import llm_extract
from src.nodes.rule_extractor import rule_extract
from src.nodes.resume_saver import save_resume
from src.nodes.data_loader import load_resume, load_jd
from src.nodes.gap_analysis import gap_analysis
from src.nodes.learning_path import learning_path
from src.nodes.verify_output import verify_output


# ==================== 通用节点 ====================

def _error_handler(state: AgentState) -> dict:
    """错误处理节点 —— 仅透传，未来可加日志/告警。"""
    return {}


def _output_result(state: AgentState) -> dict:
    """输出节点 —— 打印分析结果或提取结果到终端。"""
    error = state.get("error")
    if error:
        print(f"\n  [ERROR] {error}\n")
        return {}

    analysis = state.get("analysis_result")
    if analysis:
        verify = state.get("verify_result") or {}
        match = analysis.get("match", {})
        verdict = match.get("verdict", "")
        verdict_label = "匹配" if verdict == "yes" else "不匹配"
        print("\n" + "=" * 70)
        print("  差距分析 + 学习路径")
        print("=" * 70)
        print(f"\n  >>> 匹配结论: {verdict_label}")
        if match.get("reason"):
            print(f"  >>> 理由: {match['reason']}")
        if analysis.get("overall_summary"):
            print(f"  >>> 概述: {analysis['overall_summary']}")
        print("\n" + "-" * 70)
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
        if not verify.get("passed", True):
            print(f"\n  [!] 校验未完全通过（已重试 {state.get('retry_count', 0)} 次），请人工复核")
        print("=" * 70 + "\n")
        return {}

    result = state.get("extraction_result", {})
    if result:
        print("\n" + "=" * 70)
        print("  简历五维提取结果")
        print("=" * 70)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("=" * 70 + "\n")
    return {}


# ==================== 图 A: 简历提取 ====================

def _extract_router(state: AgentState) -> str:
    """load_document 后选择提取路线：llm / rule。"""
    if state.get("error"):
        return "error_handler"
    mode = state.get("extraction_mode", "llm")
    if mode == "rule":
        return "rule_extract"
    return "llm_extract"


def _build_extract_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("load_document", load_document)
    graph.add_node("llm_extract", llm_extract)
    graph.add_node("rule_extract", rule_extract)
    graph.add_node("save_resume", save_resume)
    graph.add_node("error_handler", _error_handler)
    graph.add_node("output_result", _output_result)

    graph.set_entry_point("load_document")

    graph.add_conditional_edges(
        "load_document",
        _extract_router,
    )
    graph.add_conditional_edges(
        "llm_extract",
        lambda s: "error_handler" if s.get("error") else "save_resume",
    )
    graph.add_conditional_edges(
        "rule_extract",
        lambda s: "error_handler" if s.get("error") else "save_resume",
    )
    graph.add_edge("save_resume", "output_result")
    graph.add_edge("error_handler", "output_result")
    graph.add_edge("output_result", END)

    return graph


# ==================== 图 B: 差距分析 ====================

def _analysis_router(state: AgentState) -> str:
    """verify 后的路由：出错→错误节点；校验失败且未超限→重试；否则输出。"""
    if state.get("error"):
        return "error_handler"
    verify = state.get("verify_result") or {}
    if not verify.get("passed", True) and state.get("retry_count", 0) < 2:
        return "gap_analysis"
    return "output_result"



def _build_analysis_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("load_resume", load_resume)
    graph.add_node("load_jd", load_jd)
    graph.add_node("gap_analysis", gap_analysis)
    graph.add_node("learning_path", learning_path)
    graph.add_node("verify_output", verify_output)
    graph.add_node("error_handler", _error_handler)
    graph.add_node("output_result", _output_result)

    graph.set_entry_point("load_resume")

    graph.add_conditional_edges(
        "load_resume",
        lambda s: "error_handler" if s.get("error") else "load_jd",
    )
    graph.add_conditional_edges(
        "load_jd",
        lambda s: "error_handler" if s.get("error") else "gap_analysis",
    )
    graph.add_edge("gap_analysis", "learning_path")
    graph.add_edge("learning_path", "verify_output")
    graph.add_conditional_edges("verify_output", _analysis_router)
    graph.add_edge("error_handler", "output_result")
    graph.add_edge("output_result", END)

    return graph


# 编译双图（单例）
extract_graph = _build_extract_graph().compile()
analysis_graph = _build_analysis_graph().compile()
