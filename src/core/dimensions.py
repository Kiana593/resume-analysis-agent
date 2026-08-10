"""维度定义 — 七维画像 key、类别映射、权重、中文标签。

与 Neo4j 图谱 NormalizedSkill.category 对齐：
    知识 / 技术 / 任职条件 / 招聘偏好 / 动机 / 特质 / 自我概念
"""

# 七维 key 顺序
DIMENSION_KEYS = (
    "knowledge",
    "skill",
    "qualifications",
    "preference",
    "motivation",
    "trait",
    "self_concept",
)

# NormalizedSkill.category → 七维画像 key
CATEGORY_TO_DIM = {
    "知识": "knowledge",
    "技术": "skill",
    "任职条件": "qualifications",
    "招聘偏好": "preference",
    "动机": "motivation",
    "特质": "trait",
    "自我概念": "self_concept",
}

# 默认权重（七维）
DEFAULT_WEIGHTS = {
    "knowledge": 0.22,
    "skill": 0.22,
    "qualifications": 0.18,
    "preference": 0.10,
    "motivation": 0.10,
    "trait": 0.09,
    "self_concept": 0.09,
}

# 中文标签（雷达图 / 报告展示用）
DIM_LABELS = {
    "knowledge": "知识",
    "skill": "技术",
    "qualifications": "任职条件",
    "preference": "招聘偏好",
    "motivation": "动机",
    "trait": "特质",
    "self_concept": "自我概念",
}
