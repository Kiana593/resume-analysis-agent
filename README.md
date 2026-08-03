# 简历提取分析 Agent

基于 LangGraph 图编排的简历分析工具：**六维提取 → 差距分析 → 学习路径 → 六维加权初筛**。

## 功能总览

| 模块 | 命令 | 说明 |
|------|------|------|
| 六维提取 | `extract` | 读取 PDF/Word 简历，LLM 提取六维画像（知识/技术/任职条件/动机/特质/自我概念），保存 JSON |
| 差距分析 | `analyze` | 简历 vs 单个 JD：六维逐维差距 + 匹配结论 + 学习路径（LLM，含幻觉校验与自动重试） |
| 批量分析 | `batch-analyze` | 简历 vs JD 目录，多线程并行，结果汇总到 `results/analysis/batch_summary.json` |
| 初筛排名 | `rank` | **非 LLM** 六维加权打分，对 JD 目录快速排序，供 LLM 精排前置筛选 |

## 环境准备

```bash
# 1. conda 环境（示例：pyw1）
conda activate pyw1

# 2. 安装依赖
pip install -e .

# 3. 配置 API Key（DeepSeek）
copy .env.example .env
# 编辑 .env，填入真实 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
```

## 运行方法

```bash
# 提取简历六维（支持 pdf / docx / doc）
python src/main.py extract samples/test2.pdf -r "重点关注AI项目经验"

# 简历 vs 单 JD 差距分析 + 学习路径
python src/main.py analyze -r results/test2.json -j jds/01_ic_design.json

# 批量分析（--workers 并行路数，--fast 用轻量模型）
python src/main.py batch-analyze -r results/test2.json -j jds --workers 3

# 六维加权初筛（非 LLM，毫秒级）
python src/main.py rank -r results/test2.json -j jds --topk 6

# 自定义权重初筛（JSON：{"knowledge": 0.3, "skill": 0.3, ...}，自动归一化）
python src/main.py rank -r results/test2.json -j jds --weights my_weights.json
```

## 六维加权打分模块（src/retrieval/）

阶段1 初筛：纯算法、不调用 LLM，对 JD 目录快速打分排序，减少 LLM 精排的候选数量。

### 打分公式

```
维度得分 = 该维度 JD 要求条目中被候选人覆盖的比例（JD 无要求 → 1.0）
总分     = Σ(权重 × 维度得分)
```

默认权重（与 LLM 判定规则一致：硬性维度权重高）：

| 维度 | 权重 |
|------|------|
| knowledge 知识 | 0.25 |
| skill 技术 | 0.25 |
| qualifications 任职条件 | 0.20 |
| motivation 动机 | 0.10 |
| trait 特质 | 0.10 |
| self_concept 自我概念 | 0.10 |

### 条目匹配算法（_item_match）

多级模糊匹配，容忍浓缩摘录、同时拦截虚构：

1. 归一化（去空白/标点、全角转半角、小写）后完全相等
2. 整串包含（如 "模拟IC设计经验" ⊂ "5年以上模拟IC设计经验"）
3. 短语滑窗（≥4 字符连续子串；3 字符子串需排除通用词，防 "士学历" 类误报）
4. 英文/数字 token（≥4 字符）跨文本命中（如 Agent/LangChain）
5. 学历等级特判：按 博士>硕士>本科>大专 等级比较（"本科及以上" vs "硕士" → 满足；"博士" vs "硕士" → 不满足）
6. 字符重叠率兜底（≥0.75，按较短文本计）

### Neo4j 数据接入（待知识图谱模块实现）

`src/retrieval/neo4j_loader.py` 定义统一数据契约：

```python
{
    "job_title": str,                  # 岗位名称
    "five_dim": Dict[str, List[str]],  # 六维要求（knowledge/skill/qualifications/motivation/trait/self_concept）
    "source": str,                     # 可选，来源标识
    "raw_text": str,                   # 可选，JD 原文
}
```

- `load_jds_from_local(jd_dir)`：已实现，读取本地 `jds/*.json`
- `load_jds_from_neo4j()`：**接口占位**，由知识图谱模块负责实现，实现后 `rank --source neo4j` 即切换数据源

## 测试

```bash
python tests/test_local_logic.py   # 幻觉校验逻辑（7 项）
python tests/test_scoring.py       # 六维打分逻辑（29 项）
```

## 目录结构

```
src/
├── main.py                  # CLI 入口（extract / analyze / batch-analyze / rank）
├── graph.py                 # LangGraph 双图（提取图 A + 分析图 B）
├── state.py                 # AgentState 状态定义
├── nodes/                   # 图节点（文档加载/提取/差距分析/学习路径/校验/输出）
├── prompts/                 # 提示词（提取 / 差距分析）
├── utils/                   # LLM 调用工具（JSON 容错修复）
├── config/                  # 提取 schema 配置
└── retrieval/               # 检索初筛（阶段1：六维加权打分；neo4j 接入占位）
jds/                         # 示例 JD（含 raw_text + five_dim）
samples/                     # 示例简历（pdf/docx）
results/                     # 输出（提取结果 / 分析结果，git 忽略）
tests/                       # 测试
```

## 后续计划

- 阶段2：混合检索（BM25 + 稠密向量 + RRF）与交叉编码器重排，候选集扩到数百 JD
- 阶段3：向量数据库（JD 数千级 / 在线服务）
- 简历提取 schema 由 `src/config/extraction_schema.json` 驱动，上游知识图谱完成后只需修改该文件
