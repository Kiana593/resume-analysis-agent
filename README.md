# 简历提取分析 Agent

基于 LangGraph 的简历分析工具：**六维提取 → Role 级别职位匹配 → LLM 差距分析 + 学习路径**。
提供 CLI 命令行和 Streamlit Web 前端两种使用方式。

## 功能总览

| 模块 | 命令 | 说明 |
|------|------|------|
| 六维提取 | `extract` | 读取 PDF/Word，LLM 或规则提取六维画像，保存 JSON |
| 差距分析 | `analyze` | 简历 vs 单个 JD：逐维差距 + 匹配结论 + 学习路径（LLM，含幻觉校验） |
| 批量分析 | `batch-analyze` | 简历 vs JD 目录，多线程并行 |
| Role 职位匹配 | `rank` | **非 LLM**：Neo4j 134 个标准职业核心技能覆盖率粗排 |
| Web 前端 | `streamlit run app.py` | 拖拽上传 → 六维预览 → Role 排名 → 雷达图（真实维度分） → 命中/未命中明细 → LLM 精简分析 |
| 数据统计 | `stats` | 查看 Neo4j 图数据库统计 |

## 环境准备

```bash
conda activate pyw1
pip install -e .
# 配置 .env: DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / NEO4J_PASSWORD 等
```

## CLI 用法

```bash
# 提取（LLM / 规则模式）
python src/main.py extract samples/test2.pdf --mode llm
python src/main.py extract samples/test2.pdf --mode rule

# 差距分析
python src/main.py analyze -r results/test2.json -j jds/01_ic_design.json

# 批量分析
python src/main.py batch-analyze -r results/test2.json -j jds --workers 3

# Role 级别职位匹配
python src/main.py rank -r results/test2.json --topk 10

# 图谱统计
python src/main.py stats --source neo4j
```

## Web 前端

```bash
streamlit run app.py
```

### 操作流程

1. **上传简历** — 拖拽 PDF/DOCX
2. **提取六维** — 侧边栏选 LLM / 规则模式
3. **预览结果** — 六维 Tab 展示提取内容
4. **职位匹配** — 点击按钮 → Top 20 Role 排名表
5. **查看详情** — 下拉选择职业，六维雷达图（每轴使用真实覆盖率）+ 命中/未命中明细
6. **LLM 分析** — 点击触发，使用预计算的命中/未命中清单（精简 prompt，tokens ~500，耗时 ~2s），输出差距评估 + 学习路径

### 架构优化

- **雷达图不再用统一近似值**：`compute_dimension_hits()` 按技能 `category` 映射到六维，逐条 `_item_match` 判定，每维输出独立的覆盖率和命中/未命中清单
- **LLM 分析走精简路线**：不扔全文给 LLM 让它自己"发现"差距，而是把算法预计算的命中/未命中清单直接喂入 `ROLE_GAP_PROMPT`，tokens 降低 80%+，幻觉风险更低

## 打分与匹配原则

### Role 级别匹配

基于 Neo4j 中的 134 个标准职业（Role），每个 Role 通过 `HAS_CORE_SKILL` 挂载核心技能（含 `final_score` 权重）。

**粗排公式**：

```
Role 得分 = Σ(命中技能 × final_score × IDF) / Σ(全部技能 × final_score × IDF)
           × min(1, 技能数 / 10)
```

- **IDF 稀有度加权**：通用技能（如"本科及以上学历"）权重压低，职业特有技能权重保留
- **少技能惩罚**：核心技能 < 10 的 Role 按 `min(1, n/10)` 打折

### 条目匹配算法（_item_match）

多级模糊匹配，容忍浓缩摘录、拦截虚构：

1. 归一化（去空白/标点、全角转半角、小写）后完全相等
2. 整串包含（"模拟IC设计经验" ⊂ "5年以上模拟IC设计经验"）
3. 短语滑窗（≥4 字符连续子串；3 字符需排除通用词防误报）
4. 英文/数字 token（≥4 字符）跨文本命中
5. 学历等级特判：博士>硕士>本科>大专 等级比较
6. 字符重叠率兜底（≥75%）

### 维度命中明细

`compute_dimension_hits()` 对单个 Role 的所有技能按六维分组，逐条匹配后输出：

```
{
  "knowledge": {"hit": ["Java原理", ...], "miss": ["分布式理论", ...], "coverage": 0.67, "total": 3},
  "skill":    {"hit": ["Spring Boot", ...], "miss": ["K8s", ...], "coverage": 0.60, "total": 10},
  ...
}
```

## 目录结构

```
src/
├── main.py                  # CLI 入口
├── graph.py                 # LangGraph 双图
├── state.py                 # AgentState
├── nodes/
│   ├── document_loader.py   # PDF/DOCX → Markdown
│   ├── llm_extractor.py     # LLM 六维提取
│   ├── rule_extractor.py    # 脚本规则六维提取
│   ├── resume_saver.py      # 保存结果
│   ├── data_loader.py       # 加载简历/JD JSON
│   ├── gap_analysis.py      # LLM 差距分析
│   ├── learning_path.py     # LLM 学习路径
│   └── verify_output.py     # 幻觉校验
├── prompts/                 # LLM 提示词（含精简 ROLE_GAP_PROMPT）
├── retrieval/
│   ├── scoring.py           # 条目匹配 + 加权打分
│   ├── role_loader.py       # Role 粗排 + 维度命中明细
│   ├── neo4j_loader.py      # Neo4j JD 加载
│   ├── graph_match.py       # 图谱匹配
│   └── csv_loader.py        # CSV 增强
├── utils/                   # LLM 调用工具
└── config/                  # 提取 schema
app.py                       # Streamlit Web 前端
jds/                         # 示例 JD
samples/                     # 示例简历
results/                     # 输出（gitignore）
tools/                       # 开发脚本
tests/                       # 测试
```
