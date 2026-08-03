"""公共 LLM 调用工具 —— 统一 DeepSeek 调用与 JSON 解析。"""

import json
import os
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


def call_deepseek_json(prompt: str, temperature: float = 0.0) -> dict[str, Any]:
    """调用 DeepSeek 并解析 JSON 输出。

    Args:
        prompt: 完整提示词
        temperature: 采样温度，默认 0（确定性优先）

    Returns:
        解析后的 JSON 对象

    Raises:
        RuntimeError: LLM 调用失败或返回内容无法解析为 JSON
    """
    llm = ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        temperature=temperature,
    )

    response = llm.invoke(prompt)
    content = response.content.strip() if hasattr(response, "content") else str(response).strip()

    # 清理可能的 markdown 代码块包裹
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        content = content.rsplit("```", 1)[0].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM 返回内容无法解析为 JSON: {exc}\n原始返回: {content[:500]}")
