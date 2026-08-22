# 04 工具层业务流：六步流水线

`src/tools/` 是业务的中枢：每个文件对应流水线的一步，被三个入口共用。
理解这一层的关键是记住**双模式函数对**：

- `prepare_xxx`：MCP 模式，**不调 LLM**，只拼提示包；
- `xxx`（如 `analyze_gap` / `suggest_resume_edit`）：CLI 模式，**自己调 LLM**。

| 文件 | MCP 函数 | CLI 函数 | 调 LLM |
|------|----------|----------|--------|
| `rank.py` | `rank_resume` | 同左 | 否 |
| `enhance.py` | `prepare_enhance` + `apply_enhance_review` | `enhance_matches` | MCP 否 / CLI 是 |
| `analyze.py` | `prepare_gap` | `analyze_gap` | MCP 否 / CLI 是 |
| `modify.py` | `prepare_resume_edit` + `validate_resume_edit` | `suggest_resume_edit` | MCP 否 / CLI 是 |
| `resume_extract.py` | `prepare_resume_extract` + `apply_resume_extract` | `extract_resume_profile` / `extract_resume_batch` | MCP 否 / CLI 是 |
| `visualize.py` | `visualize_radar`（在 mcp_server 里包装） | `render_radar` | 否 |

---

## 1. rank.py：① 关键词粗排

`rank_resume` 是流水线的起点，内部三步：

```python
roles = store.get_all_roles()                    # 1. 拿全部岗位
ranked = rank_roles(text, roles, topk=...)       # 2. 打分排序（核心层）
for item in ranked:                              # 3. 补七维明细 + 权重表
    role = store.get_role_by_name(item["role_name"])
    results.append({
        "role_name": ..., "score": ...,
        "skill_weights": {name: weight for s in skills},   # 给复核合并用
        "dimensions": compute_dimension_hits(text, skills), # 七维 hit/miss
    })
```

返回结构（关键字段）：

```json
{
  "topk": 5,
  "count": 5,
  "results": [
    {
      "role_name": "Java开发工程师",
      "score": 0.3333,
      "hit_skills": 10,
      "total_skills": 30,
      "skill_weights": {"Java": 2.0, "Spring Boot": 1.5},
      "dimensions": {
        "skill": {"hit": ["Java", "MySQL"], "miss": ["Kafka"], "coverage": 0.43, "total": 21, "hit_count": 9, "miss_count": 12}
      }
    }
  ]
}
```

`skill_weights` 是"复核合并重算加权分"的查表依据，`dimensions` 是后面所有步骤的
输入。粗排本身**不调 LLM**，毫秒级出结果。

## 2. enhance.py：② 语义复核

关键词匹配会误判（同义词、上下文、缩写）。复核步骤让 LLM 看一遍 hit/miss，
把"假命中"删掉、"漏命中"补上。

### MCP 模式（两段式）

```python
# 第一段：prepare_enhance —— 只拼包
trimmed = _trim_rank_result(rank_result, topk)   # 截前 N 名 + 精简明细
prompt = ENHANCE_PROMPT.format(topk=..., resume_text=text[:12000], rank_json=...)
return {"mode": "agent_review", "prompt": prompt, "rank_data": trimmed, "output_schema": ...}

# 第二段：apply_enhance_review —— Agent 复核完，纯逻辑合并
problems = check_review_structure(review_json)   # 结构校验
return merge_enhance_review(raw_rank_result, review_json)  # 确定性重算
```

两个细节：

- `resume_text[:12000]`：控制送入提示词的简历长度，控 token 成本。
- `_trim_rank_result` 把每维明细精简成 `{hit, miss}`，去掉冗余计数，让模型
  少读无用字段。

### CLI 模式

`enhance_matches` 自己调 `call_llm_json(prompt)`，拿到 LLM 复核结果后**同样走
`apply_enhance_review` 确定性重算**，与 MCP 模式分数口径一致（不信任模型自报
数字）。

## 3. analyze.py：③ 差距分析 + 学习路径

### 提示包的输入是怎么组织的

`_build_dimension_details` 把 role 的七维命中明细格式化成人话：

```text
- 知识 (knowledge): 命中=['RAG', 'Agent工作流'] | 缺失=['大模型API']
- 技术 (skill): 命中=['智能客服', '知识库问答'] | 缺失=['Python', 'LangChain']
...
```

`weighted_missing_skills` 是纯逻辑函数：从七维 miss 里提取缺失技能，用
`skill_weights` 查权重，**按权重降序**排好。这样喂给 LLM 的缺失清单自带优先级，
LLM 不用自己从 0 排序。

### 输出三件套

`analyze_gap`（CLI 版）返回：

```json
{
  "role_name": "AIGC应用开发工程师",
  "analysis": { "match": {...}, "dimensions": {...}, "learning_path": [...] },
  "markdown": "## AIGC应用开发工程师 — 差距分析\n...",
  "report": { "role_name": ..., "match": {...}, "dimensions": {...},
              "missing_skills": [...], "learning_path": [...], "overall_advice": "..." }
}
```

三者分工：

| 字段 | 内容 | 用途 |
|------|------|------|
| `analysis` | LLM 原始 JSON | 调试、二次加工 |
| `markdown` | 格式化报告 | 直接给人看 / 写文件 |
| `report` | 结构化合并结果 | 前端渲染、契约（B2/B3）对接 |

`build_gap_report` 是**"LLM 判断 + 纯逻辑数据"的合流点**：

- 每维 `score` 用粗排的 coverage（纯逻辑），`gap_level` 优先取 LLM 的，缺失时
  用 coverage 兜底（0 → missing，<1 → partial，=1 → sufficient）；
- 缺失技能清单**以纯逻辑的权重排序为准**，LLM 的 `importance` 只作为标注合并
  进去——防止模型自己发明缺失清单；
- 学习路径按 B3 契约规范化（step/skill/importance/prerequisite/resources/
  estimated_effort/why），LLM 缺字段就给空值，保证前端可直接渲染。

## 4. modify.py：④ 简历修改建议

### 提示包：把"岗位技能命中情况"喂给 LLM

```python
prompt = MODIFY_PROMPT.format(
    role_name=...,
    skill_lines=_build_skill_lines(role),   # 每维 hit/miss 明细
    resume_text=text[:12000],
    ai_phrase_hint="赋能、抓手、闭环、打通...",   # 前 8 个 AI 味词
)
```

提示词里写了三条硬约束（真实性红线）：不虚构公司/项目/指标、不添加简历不支持的
技能、改写示例只能基于已有事实。输出 schema 固定为：

```json
{
  "target_role": "...",
  "summary": "...",
  "suggestions": [
    {"skill": "技能名", "status": "hit|reinforce|missing",
     "suggestion": "具体建议", "example_rewrite": "改写示例"}
  ],
  "risks": ["风险提示"]
}
```

### 防造假校验紧随其后

`validate_resume_edit` 直接转发到 `src/core/edit_validation.py`（纯逻辑），
检查：技能是否在岗位清单里、hit/missing 状态是否与命中明细一致、量化指标在
原简历里有没有依据、有没有 AI 味词汇。校验细节见
[06 防造假与质量保障](06-quality-validation.md)。

## 5. resume_extract.py：⑤ 简历 → 7 维画像

这一步和前面方向相反：前面是"简历 vs 岗位"算匹配，这里是**把简历本身结构化**
成 7 维画像，与岗位画像同 schema，为"画像对画像"匹配做准备。

### 规范化三连（apply_resume_extract）

```python
profile = build_resume_profile(text, position, llm_result)  # 1. 规范化
validation = validate_resume_profile(profile, text)          # 2. 防幻觉校验
profile["stats"] = validation["stats"]                       # 3. 统计合并
profile["validation"] = validation
```

`build_resume_profile` 里几个动作：

- `DIMENSION_ALIASES` 把 LLM 可能输出的中文维度名（"知识"）翻译成 key
  （`knowledge`）；
- 条目去重、空条目丢弃；
- **超 30 字自动截断**并记入 `truncations`（审计标注），保证画像条目短而准。

`validate_resume_profile` 的防幻觉核心是 `_grounded`：

```python
if len(item_norm) <= 4:
    return item_norm in text_norm                       # 短词完全包含
return any(text_norm.find(item_norm[i:i+4]) >= 0 ...)  # 长词：任意连续 4 字窗口命中
```

允许"数据库设计与优化" → "数据库设计与优化知识"这类轻度改写，但完全虚构的内容
会被拦截。

### 批量提取（CLI 独有）

`extract_resume_batch` 用 `ThreadPoolExecutor` 并发调 LLM（IO 密集，多线程合适），
三个细节值得学习：

1. **失败隔离**：单条简历异常只记录 error，不中断整批；
2. **稳定输出顺序**：结果按原始 index 排序，多线程也不乱序；
3. **进度回调**：`progress_cb(done, total)`，CLI 里打印 `进度: 3/100`。

## 6. visualize.py：⑥ 七维雷达图

```python
matplotlib.use("Agg")                                  # 无 GUI 环境强制后端
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
```

两个要点：

- **Agg 后端**：服务器/子进程环境没有显示器，默认后端会崩，强制 Agg 直接画到
  文件。
- **中文字体**：SimHei / 微软雅黑，否则中文维度名会变成方块。

渲染逻辑很直白：取 7 维 coverage → 极坐标 7 个轴 → 闭合折线 + 半透明填充 →
`savefig(dpi=150)`。输出到系统临时目录（或指定路径），MCP 模式由
`Image(path=...)` 直接渲染到对话里。

## 7. 数据怎么在六步之间流动

```mermaid
flowchart LR
    R["rank_resume 结果"] -->|results[i]| E["apply_enhance_review<br/>合并出 review_note"]
    E -->|results[i]| A["prepare_gap / analyze_gap"]
    E -->|results[i]| M["prepare_resume_edit / validate_resume_edit"]
    E -->|results[i]| V["visualize_radar"]
    R -->|results[i]| V
    X["resume_extract<br/>7 维画像"] -.同 schema.-> A
```

中间产物 `results[i]`（单个 role JSON）是通用货币：它同时是差距分析、修改建议、
雷达图的输入。**任何一步都可以单独用**——比如只跑 `rank + radar`，或只跑
`extract-resume`，不必走完整个流水线。
