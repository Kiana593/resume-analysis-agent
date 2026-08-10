# 简历人岗匹配 MCP 工具套件

基于关键词命中的简历-岗位匹配分析 **MCP Server**（产品名：简历岗位匹配分析；MCP 注册名：resume-analysis）：`PDF/DOCX → Markdown → 关键词命中粗排 → Agent 语义复核 → 差距分析 → 简历修改建议 → 七维雷达图`。

双模式：**MCP 模式**的语义复核 / 差距分析 / 简历修改由调用方 Agent 用自己的大模型完成，服务器不调用外部 LLM API；**CLI 模式**的 `enhance` / `analyze` / `modify` 直接调用 DeepSeek API。

支持 Claude Desktop、Codex、Cursor、Continue 等任意兼容 MCP 的 Agent 平台即插即用；无 MCP 环境时也可用 CLI 或直接调用 `src/tools/` 函数。

## 快速开始

```bash
# 1. 安装依赖
pip install -e .

# 2. 复制 .env.example 为 .env 并填写 DEEPSEEK_API_KEY

# 3. 启动 MCP Server（stdio 默认，供桌面 Agent 接入）
python mcp_server.py

# SSE 传输（可选）
python mcp_server.py --transport sse
```

## MCP 工具

| 工具 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `rank_resume` | 简历文本 (str) + topk (int) | Top-N Role + 七维覆盖率 JSON | 纯关键词命中粗排 |
| `prepare_enhance` | 排名 JSON + 简历原文 + topk | 复核提示包（prompt + rank_data + schema） | Agent 用自己的模型复核 |
| `apply_enhance_review` | rank JSON + 复核 JSON | 合并后的 JSON（含 review_note） | 纯逻辑合并 + 重算覆盖率/得分 |
| `visualize_radar` | 单 Role JSON + 岗位名 | PNG 图片（对话中直接渲染） | 七维雷达图 |
| `prepare_gap` | 单 Role JSON + 简历原文 | 差距分析提示包 | Agent 用自己的模型输出 Markdown |
| `prepare_resume_edit` | 单 Role JSON + 简历原文 | 简历修改提示包（含真实性红线） | Agent 用自己的模型输出修改建议 |
| `validate_resume_edit` | 单 Role JSON + 简历原文 + 修改建议 JSON | 防造假校验报告（valid/violations/stats） | 纯逻辑校验技能地基、指标地基、AI 味词汇 |

两个静态资源供 Agent 参考：`dimensions://seven`（七维定义）、`dimensions://category-map`（Neo4j category → 七维 key 映射）。

## Agent 接入（mcp.json）

将 `mcp.json.example` 复制到对应平台的 MCP 配置（路径按实际安装目录调整）：

- **Claude Desktop**：`claude_desktop_config.json` 的 `mcpServers`
- **Cursor**：项目 `.cursor/mcp.json`
- **Continue**：`~/.continue/config.json` 的 `mcpServers`
- **Codex / 其他 CLI**：`codex mcp add` 指向 `python mcp_server.py`

> 注意：`command` 必须指向**装有 mcp SDK 的 Python**（如 conda 环境的绝对路径），
> 不能是系统默认 `python`（若其未安装 mcp）。`mcp.json.example` 已给出本机示例。

### Codex 接入（config.toml）

编辑 `C:\Users\Kianak901\.codex\config.toml`，在 `[mcp_servers]` 段后追加：

```toml
[mcp_servers.resume-analysis]
command = 'C:\Users\Kianak901\anaconda3\envs\pyw1\python.exe'
args = ['D:\个人资料\26暑假科研项目\小挑\简历提取分析agent\mcp_server.py']
startup_timeout_sec = 30

[mcp_servers.resume-analysis.env]
STORE_BACKEND = "memory"
```

保存后**重启 Codex**（MCP server 在启动时加载），新会话即可使用
`rank_resume` / `prepare_enhance` / `apply_enhance_review` / `prepare_gap` / `prepare_resume_edit` / `validate_resume_edit` / `visualize_radar` 七个工具。

或使用 CLI 命令添加（效果相同）：

```powershell
codex mcp add resume-analysis -- C:\Users\Kianak901\anaconda3\envs\pyw1\python.exe "D:\个人资料\26暑假科研项目\小挑\简历提取分析agent\mcp_server.py"
```

## CLI 用法（非 MCP 场景）

```bash
python src/main.py rank -r 简历.pdf --topk 10
python src/main.py enhance -r rank_result.json --resume 简历.pdf --topk 20
python src/main.py enhance -r rank_result.json --resume 简历.pdf --topk 20 --analyze   # 复核后自动对第 1 名做差距分析
python src/main.py analyze -r role.json --resume 简历.pdf
python src/main.py modify -r role.json --resume 简历.pdf   # 针对目标岗位的简历修改建议
python src/main.py --store neo4j rank -r 简历.pdf
```

## 目录结构

```
resume-analysis-agent/
├── mcp_server.py            # MCP 入口（唯一平台边界）
├── AGENTS.md                # Agent 操作指南（工作流 + 工具表）
├── mcp.json.example         # 各平台配置模板
├── src/
│   ├── main.py              # CLI 入口（rank / enhance / analyze / modify）
│   ├── core/                # 纯逻辑层（零外部依赖，可单测）
│   │   ├── dimensions.py    #   七维定义 + category 映射 + 权重
│   │   ├── matching.py      #   归一化 + 命中搜索
│   │   ├── ranking.py       #   覆盖率粗排 + 少条目惩罚 + IDF
│   │   └── review.py        #   复核结果合并（重算覆盖率/得分）
│   ├── store/               # 数据抽象层（依赖倒置，核心不 import neo4j）
│   │   ├── interface.py     #   RoleStore 抽象接口
│   │   ├── memory_store.py  #   内存示例数据（默认，开箱即用）
│   │   └── neo4j_store.py   #   Neo4j 实现
│   ├── tools/               # 工具纯函数（rank/enhance/analyze/modify/visualize）
│   ├── prompts/             # LLM 提示词模板
│   └── utils/               # LLM / markitdown 封装
├── tests/                   # pytest 单测（core / store / tools）
└── archive/legacy/          # 旧 Streamlit/LangGraph 代码（已隔离，不参与运行）
```

## 数据源切换

`STORE_BACKEND` 环境变量选择数据实现（`src/store/interface.py` 依赖倒置）：

- `memory`（默认）：内嵌 6 个示例 Role，适合演示 / CI / 测试
- `neo4j`：加载图谱全部 Role（134 个），需配置 `NEO4J_PASSWORD`

## 评分公式

```
Role 得分 = (命中技能数 / 核心技能总数) × min(1, 核心技能数 / 10)
```

匹配方式：技能名与简历原文统一去空白/标点、全角转半角、转小写后做归一化子串包含判断。

## 测试

```bash
python -m pytest tests -q
```

核心层（`src/core/`）为零外部依赖纯函数，测试不触网、不调 LLM。
