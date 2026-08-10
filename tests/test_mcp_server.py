"""MCP stdio 端到端集成测试 — 需要已安装 mcp SDK，未安装时自动跳过。

运行前提：pip install -e .
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_server.py"


async def _run_handshake() -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            result = await session.call_tool(
                "rank_resume",
                {"resume_text": "熟悉 Python 与 PyTorch，做过深度学习训练", "topk": 3},
            )
            text = "".join(
                c.text for c in result.content if getattr(c, "type", "") == "text"
            )
            resources = await session.list_resources()
            return {
                "tool_names": names,
                "rank_text": text,
                "resource_uris": {r.uri for r in resources.resources},
            }


def test_stdio_handshake_and_rank():
    out = asyncio.run(_run_handshake())
    assert {
        "rank_resume",
        "prepare_enhance",
        "apply_enhance_review",
        "prepare_gap",
        "prepare_resume_edit",
        "validate_resume_edit",
        "visualize_radar",
    } <= out["tool_names"]
    assert "results" in out["rank_text"]
    assert {"dimensions://seven", "dimensions://category-map"} <= out["resource_uris"]
