"""MCP 服务入口 — 简历人岗匹配工具套件。

唯一平台边界：所有逻辑都在 src/tools/ 与 src/core/ 中，
本文件只负责注册工具与资源，不包含业务代码。

运行:
    python mcp_server.py          # stdio 传输（默认）
    python mcp_server.py --transport sse  # SSE 传输
"""

import json
import sys
from pathlib import Path

# 保证以源码方式运行时能 import src
sys.path.insert(0, str(Path(__file__).resolve().parent))

# mcp SDK 版本兼容：1.x 用 FastMCP，2.x 用 MCPServer（两者对下方用法接口一致）
try:
    from mcp.server.fastmcp import FastMCP, Image  # mcp 1.x
    _ServerCls = FastMCP
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer, Image
    _ServerCls = MCPServer

from src.core.dimensions import DIMENSION_KEYS, DIM_LABELS, CATEGORY_TO_DIM
from src.tools.rank import rank_resume as _rank_resume
from src.tools.enhance import enhance_matches as _enhance_matches
from src.tools.analyze import analyze_gap as _analyze_gap
from src.tools.visualize import render_radar as _render_radar

mcp = _ServerCls("resume-analysis", instructions="简历职位匹配分析工具：关键词命中粗排 → LLM 复核 → 差距分析 → 雷达图。")


# ==================== 静态资源 ====================

@mcp.resource("dimensions://seven")
def dimensions_resource() -> str:
    """七维技能分类定义（供 Agent 参考）。"""
    lines = [f"- {dim}: {DIM_LABELS[dim]}（category: {dim}）" for dim in DIMENSION_KEYS]
    return "七维画像定义：\n" + "\n".join(lines)


@mcp.resource("dimensions://category-map")
def category_map_resource() -> str:
    """Neo4j NormalizedSkill.category → 七维 key 映射。"""
    return json.dumps(CATEGORY_TO_DIM, ensure_ascii=False, indent=2)


# ==================== 工具 ====================

@mcp.tool()
def rank_resume(resume_text: str, topk: int = 10) -> dict:
    """对简历原文做关键词命中粗排，返回 Top-N Role 及七维覆盖率。

    Args:
        resume_text: 简历 Markdown 原文（PDF/DOCX 需先用 markitdown 转换）。
        topk: 返回前 N 名（默认 10）。

    Returns:
        {"topk", "count", "results": [{role_name, family_name, domain_name,
                                       score, hit_skills, total_skills, dimensions}]}
    """
    return _rank_resume(resume_text, topk=topk)


@mcp.tool()
def enhance_matches(rank_json: str, resume_text: str, topk: int = 20) -> dict:
    """用一次 LLM 调用复核 rank_resume 的结果，修正关键词误判。

    Args:
        rank_json: rank_resume 返回结果的 JSON 字符串。
        resume_text: 简历 Markdown 原文。
        topk: 复核前 N 名（默认 20）。

    Returns:
        LLM 修正后的 JSON（含 review_note 说明修正内容）。
    """
    rank_result = json.loads(rank_json)
    return _enhance_matches(rank_result, resume_text, topk=topk)


@mcp.tool()
def visualize_radar(role_json: str, role_name: str = "") -> Image:
    """渲染单个 Role 的七维雷达图并返回 PNG 图片。

    Args:
        role_json: rank_resume 结果中单个 role 的 JSON 字符串。
        role_name: 显示用岗位名（缺省取 role_name 字段）。

    Returns:
        PNG 图片，可直接在对话中渲染。
    """
    role = json.loads(role_json)
    path = _render_radar(role, role_name)
    return Image(path=path)


@mcp.tool()
def analyze_gap(role_json: str, resume_text: str) -> dict:
    """生成单个 Role 的差距分析与学习路径（LLM 生成）。

    Args:
        role_json: rank_resume 结果中单个 role 的 JSON 字符串。
        resume_text: 简历 Markdown 原文。

    Returns:
        {"role_name", "analysis": {...}, "markdown": "..."}
    """
    role = json.loads(role_json)
    return _analyze_gap(role, resume_text)


# ==================== 入口 ====================

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="简历人岗匹配 MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="传输方式（默认 stdio，供 Claude Desktop/Codex/Cursor 等本地接入）",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
