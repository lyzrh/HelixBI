# AGENTS.md

> 本文件是所有 AI 编程助手（Claude Code / Codex / Cursor / Windsurf / Copilot 等）在本仓库工作的**唯一权威规则入口**。各工具的专属配置请引用本文件，不要在此之外另立一套规则。

## 项目概览

**Helix BI（绎数）**：对话式 AgentBI 数据分析平台，面向制造业与零售业。
自然语言提问 → LangGraph Agent 生成分析代码 → Docker 沙箱安全执行 → 图表 / 表格 / 结论。
分析资产自动沉淀为 Skill（秒级重放）、洞察、仪表板——系统越用越聪明。

详细产品介绍见 [README.zh-CN.md](README.zh-CN.md) / [README.md](README.md)。

## 常用命令

```bash
# 后端（FastAPI，:8000）
python -m uvicorn backend.main:app --port 8000

# 前端开发模式（Vite，:5173，代理到 :8000）
cd frontend && npm install && npm run dev

# 前端生产构建（产物由后端静态托管）
cd frontend && npm run build

# 沙箱镜像构建（需先 docker pull python:3.11-slim）
docker build -t helix-sandbox:latest sandbox/

# 依赖安装
pip install -r requirements.txt
```

沙箱不可用时，分析 / Skill 重放不可用，其余功能正常。

## 架构地图

后端按**业务领域**组织，而不是按技术分层堆砌——`backend/` 下每个目录代表一项能力：

| 目录 | 职责 |
| --- | --- |
| `backend/routers/` | API 层：参数校验与路由转发，不写业务逻辑（analysis/sessions/datasources/agents/skills/insights/dashboards/explore/settings/usage/misc） |
| `backend/agent/` | Agent 内核：`graph.py`（parse_intent → generate_code → execute 自修复 → summarize → followup）、`prompts.py`、`profiler.py`、`sandbox.py`（Docker 沙箱客户端） |
| `backend/analysis/` | Analysis Runtime：`runtime.py`（驱动分析链路 + SSE 事件映射 + 落库）、`explore.py`（自助分析，本地零 token） |
| `backend/skills/` | Skill 领域：沉淀 / 匹配 / 重放 / few-shot 渲染 |
| `backend/insights/` | 主动洞察：`engine.py` 规则扫描 + LLM 诊断，`scheduler.py` 定时调度 |
| `backend/datasource/` | 数据源接入：文件 / DB 连接、预览、parquet 物化 |
| `backend/semantic/` | 语义层运行时：解释 `semantic_packs/`（`registry.py` 加载检索、`render.py` 渲染 prompt） |
| `backend/report/` | 报告导出：`builder.py` 单轮分析 HTML、`exporter.py` 仪表板导出 |
| `backend/config.py` `db.py` `models.py` `schemas.py` `seed.py` | 基础设施：全局配置单一入口、SQLite 引擎（WAL）、ORM 与 API 模型、内置种子数据 |
| `frontend/` | React 18 + AntD 5 + Zustand + Vite；`src/pages/` 与 `src/stores/`（chatStore 为 SSE 状态机） |
| `semantic_packs/` | 行业语义包 YAML（retail_sales / manufacturing_production）——业务语义唯一来源，由 `backend/semantic/` 运行时解释 |
| `sandbox/` | 独立执行环境：沙箱镜像（pandas/pyarrow/matplotlib/CJK 字体 + dahelper 结果契约），**不并入 backend** |
| `examples/` | 示例数据（零售 / 制造 CSV） |

运行期目录（已 gitignore）：`uploads/`、`data/`、`runs/`。

## 核心不变式（修改代码前必读）

1. **LLM 生成的代码只允许在 Docker 沙箱内执行**（`--network none`、CPU/内存限额、只读数据）。永远不要让生成的代码直接跑在宿主机进程里。
2. **分析数据永远以文件形式进入沙箱 `/data`**：文件源直接挂载；数据库数据先物化为 parquet 缓存再进入沙箱。
3. **沙箱结果必须通过 `dahelper` JSON 契约返回**（charts/tables/text），不要引入其他返回通道。
4. **`backend/agent/graph.py` 保持线性 LangGraph 流程**，只允许通过 `AgentState.skill_block` 这类最小钩子扩展，不做 tool-calling 重构。
5. **指标定义以语义包为唯一来源**：生成提示词必须经 `backend/semantic/` 注入 `semantic_packs/*.yaml`，防止 LLM 即兴发挥导致口径漂移。
6. **元数据统一存 SQLite（WAL 模式）**，经 SQLAlchemy 访问，新增表改 `backend/models.py` + `backend/schemas.py`。
7. **后端按领域组织，不按技术分层**：新增能力落到 `agent/ analysis/ skills/ insights/ datasource/ semantic/ report/` 中对应的领域目录，不要新建 `services/`、`utils/` 这类"什么都放"的目录。

## 按需阅读的规则索引

在对应领域动手前，先读 `.agents/rules/` 下的相应文件：

| 任务 | 先读 |
| --- | --- |
| 改动架构、新增钩子、跨层调用 | [.agents/rules/architecture.md](.agents/rules/architecture.md) |
| 后端 routers / 领域模块 / 模型改动 | [.agents/rules/backend.md](.agents/rules/backend.md) |
| 前端页面/状态/图表改动 | [.agents/rules/frontend.md](.agents/rules/frontend.md) |
| 沙箱、数据源、安全相关改动 | [.agents/rules/sandbox-and-data.md](.agents/rules/sandbox-and-data.md) |

历史重构决策与设计文档见 `.agents/plans/`。

## 代码与协作规范

- Python 3.11+；类型注解尽量齐全；遵循「API 层（routers）→ 领域模块 → Agent 内核」的分层，不要在 router 里写业务逻辑。
- 前端使用 TypeScript + AntD 5 + Zustand；SSE 逻辑集中在 `chatStore`，页面组件不直接管理流状态。
- 提交信息用中文，格式 `type: 摘要`（feat / fix / docs / refactor）。
- 涉及品牌视觉时使用"绎紫"设计系统：主色 `#5645D4`，深海军蓝 `#0A1530`。
- 不要提交：`.env`、`CLAUDE.local.md`、`.claude/settings.local.json`、`.workbuddy/`、运行期目录内容。
