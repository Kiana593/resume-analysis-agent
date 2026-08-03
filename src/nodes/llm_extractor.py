"""LLM 提取节点 —— 将简历文本发给 DeepSeek，按六维度模型提取结构化信息。"""

from typing import Any, Dict

from src.state import AgentState
from src.utils.llm import call_deepseek_json

# ==================== 六维度分类标准 ====================

CLASSIFICATION_CRITERIA = """
## 分类标准（请严格按以下规则归类）

1. 知识（knowledge）：
候选人掌握的专业理论、行业知识、流程认知、原理理解。
关键词包括：了解、熟悉理论、掌握原理、具备知识、专业背景。

2. 技术（skill）：
候选人能够实际操作、设计、开发、调试、验证或交付的能力。
包括工具使用能力、模块设计能力、工程实现能力、项目交付成果。
若内容涉及"经验年限、项目案例、tape out、量产、工具使用、模块设计"，优先归入技术。

3. 任职条件（qualifications）：
候选人的学历层次（本科/硕士/博士）与专业背景是否满足岗位硬性门槛，属于客观任职资格信息。
关键词包括：专业、学历、学位、本科、硕士、博士。

4. 动机（motivation）：
候选人的主动性、工作意愿、职业驱动力、积极性。

5. 特质（trait）：
相对稳定的行为风格和能力倾向，如学习能力、沟通能力、问题解决能力、细致程度、抗压能力。

6. 自我概念（self_concept）：
候选人对自身角色、责任、团队关系和职业身份的认知，如责任心、团队合作意识、主人翁意识。
"""


def build_extraction_prompt(
    schema: Dict[str, Any],
    user_requirements: str | None,
    is_markdown: bool = False,
) -> str:
    """根据 extraction_schema.json 动态构造 prompt，含六维度分类标准。

    Args:
        schema: 提取字段配置
        user_requirements: 用户指定的重点关注方向
        is_markdown: True 时注入 Markdown 结构提示
    """
    field_descriptions = "\n".join(
        f"  - {f['label']}（字段名: {f['name']}）：{f['description']}"
        for f in schema.get("fields", [])
    )

    requirements_block = ""
    if user_requirements:
        requirements_block = f"\n## 用户重点关注\n{user_requirements}\n"

    format_block = ""
    if is_markdown:
        format_block = """\n## 输入格式说明
简历已预处理为 Markdown 格式。`##` 表示章节标题，`-` 表示列表项，
`**粗体**` 可能为重点内容，`|` 分隔的为表格。请充分利用这些结构信息辅助分类。
"""

    return f"""你是一位专业的简历信息提取助手。请从以下简历文本中提取结构化信息。

## 提取字段说明
{field_descriptions}

{CLASSIFICATION_CRITERIA}
{requirements_block}{format_block}
## 输出要求
1. 以 JSON 格式输出，knowledge / skill / qualifications / motivation / trait / self_concept 字段的值均为字符串数组，每项一条。
2. personal_info 字段为对象（非数组），包含姓名、性别、年龄/出生日期、联系电话、邮箱、现居城市、求职意向等子字段，缺失则留空字符串 ""。
3. 如果某个字段在简历中没有对应内容，knowledge/skill/qualifications/motivation/trait/self_concept 返回空数组 []。
4. 不要编造简历中不存在的信息。
5. 仅输出 JSON，不要包含其他文字。

## 简历文本
{{raw_text}}

## JSON 输出
"""


def llm_extract(state: AgentState) -> dict:
    """调用 DeepSeek 从 raw_text 中提取结构化信息。"""
    raw_text = state.get("raw_text", "").strip()
    schema = state.get("extraction_schema", {})
    user_requirements = state.get("user_requirements")

    if not raw_text:
        return {"error": "raw_text 为空，无法提取"}

    if not schema:
        return {"error": "extraction_schema 为空，请检查 config/extraction_schema.json"}

    prompt = build_extraction_prompt(
        schema,
        user_requirements,
        state.get("is_markdown", False),
    ).format(raw_text=raw_text)

    try:
        result = call_deepseek_json(prompt)
        return {"extraction_result": result}
    except Exception as exc:
        return {"error": f"LLM 调用失败: {exc}"}
