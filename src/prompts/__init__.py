"""提示词模块 —— 差距分析与学习路径。"""

# ==================== 差距分析提示词 ====================

GAP_ANALYSIS_PROMPT = """你是一位资深的人才发展顾问。请对比候选人的五维画像与目标岗位的五维要求，找出候选人与岗位之间的差距。

## 候选人五维画像（来自简历）
{resume_five_dim}

## 目标岗位五维要求（来自JD）
{jd_five_dim}

## 目标岗位描述原文（JD 原文，供引用证据使用）
{jd_raw_text}

## 候选人简历原文（供引用证据使用）
{resume_raw_text}
{requirements_block}
## 差距分析规则
1. 逐项对比：以 JD 要求为基准，逐条判断候选人是否满足。每条 JD 要求生成一个 gap 条目。
2. gap_level 判定：
   - missing：简历中完全没有对应内容
   - partial：简历中有部分相关经历或能力，但未完全覆盖 JD 要求
   - sufficient：简历已充分满足
3. importance 判定（结合 JD 原文语气与职责描述）：
   - high：JD 中强调的核心职责、任职资格、加分项中的明确要求
   - medium：一般性要求
   - low：边缘性要求

## 证据引用规则（防止幻觉，务必遵守）
1. resume_evidence 必须**逐字摘录**简历原文中支持判定结果的句子；简历中找不到任何相关内容的写 "无"。
2. jd_evidence 必须**逐字摘录** JD 原文中对应要求的那句话。
3. 禁止自行编造、改写或扩展原文中不存在的内容。
4. 摘录时保留原文措辞，不得添加自己的解释。

## 输出格式（仅输出 JSON，不要任何其他文字）
{{
  "overall_summary": "两到三句话的总体差距结论",
  "gaps": [
    {{
      "dimension": "knowledge|skill|motivation|trait|self_concept",
      "item": "JD 要求条目原文",
      "gap_level": "missing|partial|sufficient",
      "importance": "high|medium|low",
      "resume_evidence": "简历原文逐字摘录，无则写 无",
      "jd_evidence": "JD 原文逐字摘录",
      "reason": "判定理由，必须引用双方证据"
    }}
  ]
}}
"""


# ==================== 学习路径提示词 ====================

LEARNING_PATH_PROMPT = """你是一位职业学习规划专家。请基于以下差距分析结果，为候选人设计一条可执行的学习路径。

## 差距分析结果
{gaps_json}

## 学习路径设计规则
1. 只针对 gap_level 为 missing 或 partial 且 importance 为 high/medium 的条目生成学习步骤；sufficient 和 low 重要性条目跳过。
2. 排序原则：importance 为 high 的优先；同重要性下，缺失程度更严重的优先；有前置依赖关系的必须先学。
3. 每个步骤给出具体、可执行的资源建议（如课程平台、官方文档、开源项目、书籍），并预估学习投入时间。
4. 禁止编造不存在的课程或资源名称，不确定时写通用资源类型（如"开源项目实战"）。

## 输出格式（仅输出 JSON，不要任何其他文字）
{{
  "learning_path": [
    {{
      "step": 1,
      "target_skill": "学习目标（对应差距条目）",
      "importance": "high|medium",
      "prerequisite": "前置要求，无则写 无",
      "resources": ["推荐资源1", "推荐资源2"],
      "estimated_effort": "预估投入，如 2-3 周",
      "why": "为什么排在当前顺序"
    }}
  ]
}}
"""
