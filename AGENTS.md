# 简历人岗匹配 MCP Agent — 操作指南

本仓库提供一个标准 MCP Server（`mcp_server.py`），用于简历职位匹配分析：
关键词命中粗排 → LLM 语义复核 → 单岗位差距分析 → 七维雷达图。

## 工作流（推荐顺序）

1. **读取简历**：用户给出 PDF/DOCX/MD/TXT 路径时，先用 `markitdown`（或直接读文本）把简历转为 Markdown 原文。
2. **粗排**：调用 `rank_resume(resume_text, topk)` 得到 Top-N Role 及七维覆盖率 JSON。
3. **展示**：向用户展示 Top-5，格式建议：
   ```
   #1 大模型算法工程师 45.2%（命中 9/20 技能）
   #2 AIGC应用开发工程师 41.0%
   ...
   ```
4. **雷达图**：用户想看某岗位时，取该 role 的 JSON 调 `visualize_radar(role_json, role_name)`，返回 PNG 直接渲染。
5. **差距分析**：用户问"差距/学习路径"时，调 `analyze_gap(role_json, resume_text)`，输出 Markdown。
6. **语义复核**：用户要求复核是否漏判时，调 `enhance_matches(rank_json, resume_text, topk)`，一次 LLM 调用复核 Top-20。

## 工具清单

| 工具 | 输入 | 说明 |
|------|------|------|
| `rank_resume` | 简历文本 + topk | 纯关键词命中粗排，返回 Top-N + 七维覆盖率 |
| `enhance_matches` | rank JSON + 简历文本 + topk | LLM 复核修正误判，返回带 review_note 的 JSON |
| `visualize_radar` | 单 role JSON + 岗位名 | 七维雷达图 PNG |
| `analyze_gap` | 单 role JSON + 简历文本 | 差距分析 + 学习路径 Markdown |

## 资源

| 资源 | 说明 |
|------|------|
| `dimensions://seven` | 七维画像定义（knowledge/skill/qualifications/preference/motivation/trait/self_concept） |
| `dimensions://category-map` | Neo4j category → 七维 key 映射 |

## 数据源切换

- 默认 `STORE_BACKEND=memory`：内嵌 6 个示例 Role，开箱即用。
- 设为 `STORE_BACKEND=neo4j` 并从 `.env` 提供 `NEO4J_PASSWORD`：加载图谱全部 134 个 Role。

## 环境变量

复制 `.env.example` 为 `.env` 并填写：

```
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
STORE_BACKEND=memory          # memory | neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
```

## 本地运行

```bash
pip install -e .
python mcp_server.py            # stdio（Claude Desktop / Codex / Cursor 本地接入）
python mcp_server.py --transport sse
```

## 注意事项

- `rank_resume` 要求简历为纯文本/Markdown；PDF/DOCX 请先用 markitdown 转换（见 `src/utils/text.py`）。
- `enhance_matches` 和 `analyze_gap` 依赖 DeepSeek API Key。
- 代码分层：`mcp_server.py`（平台边界）→ `src/tools/`（工具实现）→ `src/core/`（纯逻辑，零外部依赖）+ `src/store/`（数据抽象）。
