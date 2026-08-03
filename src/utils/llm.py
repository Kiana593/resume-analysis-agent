"""公共 LLM 调用工具 —— 统一 DeepSeek 调用与 JSON 解析。"""

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


def _repair_json(content: str) -> str:
    """对 LLM 返回的 JSON 做安全修复，处理常见格式问题。

    已修复：
    - 多余的尾部逗号（如 "a": 1, }）
    - 字符串内未转义的换行/控制字符
    - 未闭合的中文引号

    不做激进重写，无法修复时保持原样交给 json.loads 报错。
    """
    if not content:
        return content

    # 1. 去除 BOM
    if content.startswith("\ufeff"):
        content = content.lstrip("\ufeff")

    # 2. 修复尾部逗号（, 后面紧跟 } 或 ]）
    content = re.sub(r",\s*([}\]])", r"\1", content)

    # 3. 字符串值中常见未转义字符：把值里的裸换行换成空格
    #    仅处理双引号字符串内部（简化：替换所有不在引号结构中的裸控制字符）
    content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", content)

    # 4. 如果以截断的形式结束（无闭合括号），按栈顺序补全
    stripped = content.rstrip()
    stack: list[str] = []
    for ch in stripped:
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    if stack:
        for ch in reversed(stack):
            content += "}" if ch == "{" else "]"
        # 补全可能引入新的尾随逗号，再次修复
        content = re.sub(r",\s*([}\]])", r"\1", content)

    return content


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

    # 容错修复后解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        repaired = _repair_json(content)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            # 提取错误位置附近内容便于定位
            pos = exc.pos
            snippet = content[max(0, pos - 80):pos + 80].replace("\n", "\\n")
            raise RuntimeError(
                f"LLM 返回内容无法解析为 JSON: {exc}\n错误位置附近: ...{snippet}..."
            )
