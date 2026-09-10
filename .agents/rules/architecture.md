# 架构规则

> 动架构、加钩子、做跨层调用之前必读。上游权威文件：根目录 [AGENTS.md](../../AGENTS.md)。

## 分层结构

```
React 前端 (frontend/)
   │  fetch + SSE 流解析
   ▼
FastAPI (backend/, :8000)
   ├── routers/   仅做参数校验与路由转发，不写业务逻辑
   ├── services/  业务逻辑层（analysis_runner / skill_engine / insight_engine / …）
   ├── models.py / schemas.py  ORM 与 API 模型，SQLite 元数据（WAL）
   └── 调用 app/ 内核
         ├── app/graph.py  LangGraph：parse_intent → generate_code → execute(自修复) → summarize → suggest_followups
         ├── app/semantic.py + semantics/*.yaml  行业语义包渲染
         ├── app/sandbox.py  Docker 无网络沙箱 + dahelper 结果契约
         └── app/report.py   HTML 报告导出
```

## 必须遵守

- **内核线性流程不可重构**：`app/graph.py` 的 LangGraph 链路保持线性，只允许加 `AgentState.skill_block` 这类最小钩子。扩展能力优先在 `backend/services/` 层实现。
- **单向依赖**：`backend/` → `app/`；禁止 `app/` 反向 import `backend/`；前端只通过 HTTP/SSE 与后端通信。
- **产物路径约定**：分析产物写 `runs/{run_id}/out`（result.json + PNG），由后端静态挂载 `/runs` 提供访问。
- **语义包是口径唯一来源**：新增指标/维度先改 `semantics/*.yaml`，再考虑代码；不要在 prompt 或业务代码里硬编码指标定义。
- **元数据库迁移**：改表结构需同步 `backend/models.py`、`backend/schemas.py`，并确认 `backend/seed.py` 的内置数据兼容。
- 旧 Streamlit UI（`app/ui/app.py`，:8501）保留可独立运行，改动内核时不得破坏它。

## 历史决策

`enterprise-agentbi-refactor-plan.md`（同目录 plans/ 下）记录了从 Streamlit 单页到 FastAPI + React 前后端分离的完整改造决策，含已确认的技术选型与"不变的底层"清单，重构前建议通读。
