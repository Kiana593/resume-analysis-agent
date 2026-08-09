# 简历提取分析 Agent

基于 LangGraph 的简历分析工具：**六维提取 → Role 级别职位匹配 → LLM 差距分析 + 学习路径**。
提供 CLI 命令行 和 Streamlit Web 前端两种使用方式。

## 功能总览

| 模块 | 命令 | 说明 |
|------|------|------|
| 六维提取 | `extract` | 读取 PDF/Word 简历，LLM 或脚本规则提取六维画像（知识/技术/任职条件/动机/特质/自我概念），保存 JSON |
| 差距分析 | `analyze` | 简历 vs 单个 JD：六维逐维差距 + 匹配结论 + 学习路径（LLM，含幻觉校验与自动重试） |
| 批量分析 | `batch-analyze` | 简历 vs JD 目录，多线程并行 |
| Role 职位匹配 | `rank` | **非 LLM**：Neo4j 中 134 个标准职业（Role）核心技能覆盖率粗排 |
| Web 前端 | `streamlit run app.py` | 拖拽上传简历 → 六维预览 → Role 排名 → 雷达图 → LLM 差距分析 + 学习路径 |
| 数据统计 | `stats` | 查看 Neo4j 图数据库统计 |

## 环境准备

```bash
# 1. conda 环境
conda activate pyw1

# 2. 安装依赖
pip install -e .

# 3. 配置 .env
# DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
# NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
```

## CLI 运行方法

```bash
# 提取简历六维（LLM 模式 / 规则模式）
python src/main.py extract samples/test2.pdf --mode llm
python src/main.py extract samples/test2.pdf --mode rule

# 简历 vs 单 JD 差距分析 + 学习路径
python src/main.py analyze -r results/test2_20260803_171335.json -j jds/01_ic_design.json

# 批量分析
python src/main.py batch-analyze -r results/test2.json -j jds --workers 3

# Role 级别职位匹配（Neo4j 核心技能覆盖率）
python src/main.py rank -r results/test2_20260803_171335.json --topk 10

# 图谱统计
python src/main.py stats --source neo4j
```

## Web 前端

```bash
streamlit run app.py
```

### 操作流程

1. **上传简历** — 拖拽 PDF/DOCX 文件到界面
2. **选择提取模式** — 侧边栏选 LLM 或规则提取，点击"提取简历六维"
3. **预览六维** — 查看提取结果（知识/技能/学历/动机/特质/自我概念）
4. **职位匹配** — 点击"分析职位匹配"，展示 Top 20 Role 排名
5. **查看详情** — 下拉选择职业，显示雷达图和命中技能数
6. **LLM 深度分析** — 点击"生成匹配分析和学习路径"，对该 Role 全部核心技能进行六维差距分析并输出学习路径

## 打分与匹配原则

### Role 级别匹配（rank 命令 / Web 前端）

基于 Neo4j 知识图谱中的 134 个标准职业（Role），每个 Role 通过 `HAS_CORE_SKILL` 关系挂载核心技能（含 `final_score` 权重）。

**匹配公式**：

```
Role 得分 = Σ(简历命中的核心技能 × final_score) / Σ(全部核心技能 final_score)
           × min(1, 核心技能数 / 10)
```

- **命中判定**：简历六维条目通过 `_item_match` 模糊匹配技能名
- **技能→维度映射**：技能 `category` 字段映射到六维（知识/技术/任职条件/动机/特质/自我概念）
- **IDF 稀有度加权**：通用技能权重降低，职业特有技能权重保留
- **少技能惩罚**：核心技能 < 10 的 Role 按 `min(1, n/10)` 打折，防止稀疏数据虚高

### 条目匹配算法（_item_match）

多级模糊匹配，容忍浓缩摘录、同时拦截虚构：

1. 归一化（去空白/标点、全角转半角、小写）后完全相等
2. 整串包含（如 "模拟IC设计经验" ⊂ "5年以上模拟IC设计经验"）
3. 短语滑窗（≥4 字符连续子串；3 字符子串需排除通用词，防 "士学历" 类误报）
4. 英文/数字 token（≥4 字符）跨文本命中
5. 学历等级特判：按 博士>硕士>本科>大专 等级比较
6. 字符重叠率兜底（≥75%）

### LLM 差距分析

当从前端手动触发 LLM 分析时，系统将该 Role 的**全部核心技能**按六维分类构建合成 JD，送入 DeepSeek 进行六维差距分析，输出：

- 六维差距级别（missing / partial / sufficient）
- 匹配结论（yes / no）
- 学习路径（按优先级排序，最多 10 步）
- 含幻觉校验与自动重试

## 目录结构

```
src/
├── main.py                  # CLI 入口
├── graph.py                 # LangGraph 双图（提取图 + 分析图）
├── state.py                 # AgentState 状态定义
├── nodes/                   # 图节点
│   ├── document_loader.py   # PDF/DOCX → Markdown
│   ├── llm_extractor.py     # LLM 六维提取
│   ├── rule_extractor.py    # 脚本规则六维提取
│   ├── resume_saver.py      # 保存提取结果
│   ├── data_loader.py       # 加载简历/JD JSON
│   ├── gap_analysis.py      # LLM 六维差距分析
│   ├── learning_path.py     # LLM 学习路径生成
│   └── verify_output.py     # 幻觉校验
├── prompts/                 # LLM 提示词
├── retrieval/               # 检索匹配
│   ├── scoring.py           # 条目匹配 + 六维加权打分
│   ├── role_loader.py       # Role 粗排 + Neo4j 数据接入
│   ├── neo4j_loader.py      # Neo4j JD 加载
│   ├── graph_match.py       # 图谱匹配（resume-graph-match 对接）
│   └── csv_loader.py        # CSV 六维增强
├── utils/                   # LLM 调用工具
└── config/                  # 提取 schema 配置
app.py                       # Streamlit Web 前端
jds/                         # 示例 JD
samples/                     # 示例简历
results/                     # 输出目录
tools/                       # 开发脚本
tests/                       # 测试
```
