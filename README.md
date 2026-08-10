# 简历职位匹配分析

轻量化简历分析工具：**PDF/DOCX → 原文技能命中搜索 → 134 Role 排名 → 雷达图 + 高亮 → LLM 建议**。

## 架构

`
PDF/DOCX → markitdown → Markdown 原文
                              │
              对 134 Role 核心技能逐条归一化子串命中
                              │
              Role 得分 = 命中数 / 总技能数 × 少技能惩罚
                              │
              Top-N → DeepSeek 精简 prompt → 适配职业 + 缺口 + 学习建议
`

核心思路：**不做六维提取**，直接在简历原文中搜索 Neo4j 图谱中标准职业（Role）的核心技能名。LLM 输入为预计算的命中/未命中清单（非全文），tokens ~500，耗时 ~2s。

## 七维技能分类

图谱中的 NormalizedSkill 按 category 字段分为七类：

| category | 维度 key | 说明 |
|----------|----------|------|
| 知识 | knowledge | 专业理论、行业知识、原理理解 |
| 技术 | skill | 工具使用、开发设计、工程能力 |
| 任职条件 | qualifications | 学历、专业等硬性门槛 |
| 招聘偏好 | preference | 加分项、隐性要求 |
| 动机 | motivation | 主动性、驱动力 |
| 特质 | trait | 行为风格、能力倾向 |
| 自我概念 | self_concept | 角色认知、责任心 |

## 快速开始

`ash
conda activate pyw1
pip install -e .

# CLI
python src/main.py rank -r results/test2.json --topk 10

# Web
streamlit run app.py
`

## Web 前端操作

1. **上传简历** — 拖拽 PDF/DOCX
2. **解析** — markitdown 转 Markdown
3. **匹配** — 对 134 Role 做技能命中搜索，按覆盖率排名
4. **详情** — 选职业查看七维雷达图 + 命中/未命中明细 + 原文高亮
5. **LLM 分析** — 预计算命中清单喂给 DeepSeek，输出差距评估 + 学习路径

## 目录结构

`
src/
├── main.py                  # CLI 入口
├── graph.py                 # LangGraph 双图（保留）
├── state.py                 # AgentState（保留）
├── nodes/                   # 图节点（保留，app 不调用）
├── prompts/                 # LLM 提示词
├── retrieval/
│   ├── scoring.py           # 归一化匹配 + match_skills_in_text + 七维定义
│   ├── role_loader.py       # Role 粗排 + 维度命中明细
│   ├── neo4j_loader.py      # Neo4j 数据加载
│   ├── graph_match.py       # 图谱匹配
│   └── csv_loader.py        # CSV 增强
├── utils/                   # LLM 调用工具
└── config/                  # 提取 schema（保留）
app.py                       # Streamlit Web 前端
`

## 计分公式

`
Role 得分 = (命中技能数 / 核心技能总数) × min(1, 技能数 / 10)
`

- 归一化子串匹配：技能名和原文统一去空白/标点/全角转半角/小写后做 in 判断
- 少技能惩罚：核心技能 < 10 的 Role 按 min(1, n/10) 打折

## 环境变量

`
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=
DEEPSEEK_MODEL=deepseek-v4-pro
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
`
