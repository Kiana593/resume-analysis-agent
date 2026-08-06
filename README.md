# 简历提取分析 Agent

基于 LangGraph 图编排的简历分析工具：**六维提取 → 差距分析 → 学习路径 → 六维加权初筛**。

## 功能总览

| 模块 | 命令 | 说明 |
|------|------|------|
| 六维提取 | `extract` | 读取 PDF/Word 简历，LLM 提取六维画像（知识/技术/任职条件/动机/特质/自我概念），保存 JSON |
| 差距分析 | `analyze` | 简历 vs 单个 JD：六维逐维差距 + 匹配结论 + 学习路径（LLM，含幻觉校验与自动重试） |
| 批量分析 | `batch-analyze` | 简历 vs JD 目录，多线程并行，结果汇总到 `results/analysis/batch_summary.json` |
| 初筛排名 | `rank` | **非 LLM** 六维加权打分；`--source neo4j` 两阶段（Role 粗排 → JD 细排） |
| 数据统计 | `stats` | 查看数据源统计（本地 JD 数量 / Neo4j 节点数） |

## 环境准备

```bash
# 1. conda 环境（示例：pyw1）
conda activate pyw1

# 2. 安装依赖（首次需联网；代码以源码方式运行，此命令仅安装依赖、不打包）
pip install -e .

# 或使用 pip 直接按 pyproject.toml 声明安装（效果相同）
pip install -e . --no-build-isolation

# 3. 配置 API Key 与 Neo4j
copy .env.example .env
# 编辑 .env，填入真实 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
# 以及 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD（使用图谱模式时需要）
```

> 说明：`pip install -e .` 会读取 `pyproject.toml` 的 `dependencies` 安装全部依赖。
> 本仓库代码不打包成 Python 包（`[tool.setuptools] packages = []`），统一以
> `python src/main.py` 从项目根目录运行。

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

# Neo4j 两阶段排名：阶段1 Role 粗排 → 阶段2 候选 Role 旗下 JD 细排
python src/main.py rank -r results/test2.json --source neo4j --topk 10 --role-topk 10

# CSV 增强：提供"提取后原始数据"目录后，JD 细排优先使用 CSV 完整六维
python src/main.py rank -r results/test2.json --source neo4j --csv-dir "D:\path\to\提取后原始数据" --topk 10

# 图谱统计
python src/main.py stats --source neo4j
```

## 六维加权打分模块（src/retrieval/）

阶段1 初筛：纯算法、不调用 LLM，对 JD 目录快速打分排序，减少 LLM 精排的候选数量。

### 打分公式

```
维度得分 = 该维度 JD 要求条目中被候选人覆盖的比例（JD 无要求 → 1.0）
总分     = Σ(权重 × 维度得分) × 少条目惩罚 min(1, JD要求总条目数 / 12)

> 少条目惩罚：图谱 JD 的技能挂载稀疏（平均 5.6 条），仅 2-5 条要求的 JD 容易被
> 简历全量覆盖而虚高得满分。要求条目数不足 12 条时按比例打折（K 可在
> `scoring.JD_MIN_FEATURES_K` 调整）。
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

### Neo4j 数据接入与两阶段排名

`src/retrieval/neo4j_loader.py` 定义统一数据契约：

```python
{
    "job_title": str,                  # 岗位名称
    "five_dim": Dict[str, List[str]],  # 六维要求（knowledge/skill/qualifications/motivation/trait/self_concept）
    "source": str,                     # 可选，来源标识
    "raw_text": str,                   # 可选，JD 原文
}
```

- `load_jds_from_local(jd_dir)`：读取本地 `jds/*.json`
- `load_jds_from_neo4j()`：从 Neo4j 查询 JD 技能（`MENTIONS_NORMALIZED_SKILL` 关系）
- `role_loader.py`：阶段 1 Role 粗排（核心技能权重覆盖率 + IDF 稀有度加权 + 少技能惩罚）
- `csv_loader.py`：图谱 JD → 原始 CSV 完整六维增强（`--csv-dir` 启用）

阶段 1 粗排公式：Role 得分 = Σ(命中技能×final_score) / Σ(final_score) × min(1, 技能数/10)。

### 少条目惩罚

图谱 JD 技能稀疏（平均 5.6 条），若直接用覆盖率打分，2-3 条要求的 JD 会被
全量覆盖而虚高。`scoring.py` 对六维总条目数少于 `JD_MIN_FEATURES_K=12` 的 JD
按 `min(1, 条目数/12)` 打折总分；`role_loader.py` 对核心技能 < 10 的 Role 同样打折。

## 测试

```bash
python tests/test_local_logic.py   # 幻觉校验逻辑（7 项）
python tests/test_scoring.py       # 六维打分逻辑（29 项，含少条目惩罚断言）
```

> 测试依赖 `jds/`（已提交）与 `tests/fixtures/test2_five_dim.json`（测试简历样本）。

## 目录结构

```
src/
├── main.py                  # CLI 入口（extract / analyze / batch-analyze / rank / stats）
├── graph.py                 # LangGraph 双图（提取图 A + 分析图 B）
├── state.py                 # AgentState 状态定义
├── nodes/                   # 图节点（文档加载/提取/差距分析/学习路径/校验/输出）
├── prompts/                 # 提示词（提取 / 差距分析）
├── utils/                   # LLM 调用工具（JSON 容错修复）
├── config/                  # 提取 schema 配置
└── retrieval/               # 检索初筛（六维打分 + Neo4j 图谱 + Role 粗排 + CSV 增强）
tools/                       # 开发脚本（FairCV 采样/提取/对比）
tests/fixtures/              # 测试样本（合成简历六维）
jds/                         # 示例 JD（含 raw_text + five_dim）
samples/                     # 示例简历（pdf/docx）
results/                     # 输出（提取结果 / 分析结果，git 忽略）
tests/                       # 测试
```

## 后续计划

- 阶段2：混合检索（BM25 + 稠密向量 + RRF）与交叉编码器重排，候选集扩到数百 JD
- 阶段3：向量数据库（JD 数千级 / 在线服务）
- 简历提取 schema 由 `src/config/extraction_schema.json` 驱动，上游知识图谱完成后只需修改该文件
