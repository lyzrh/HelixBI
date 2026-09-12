# 架构规则

> 动架构、加钩子、做跨层调用之前必读。上游权威文件：根目录 [AGENTS.md](../../AGENTS.md)。

## 分层结构

后端按**业务领域**（而非技术分层）组织，每个目录代表一项能力：

```
React 前端 (frontend/)
   │  fetch + SSE 流解析
   ▼
API 层 (backend/routers/, :8000)
   │  仅参数校验与路由转发，不写业务逻辑
   ▼
领域层 (backend/ 下各领域目录)
   ├── analysis/     Analysis Runtime：驱动分析链路、SSE 事件映射、落库、Run.trace 可观测；explore 自助分析
   ├── skills/       Skill 沉淀 / 匹配 / 重放（重放轨迹含 llm.calls==0 证据）
   ├── insights/     规则扫描 + 定时调度 + LLM 诊断
   ├── datasource/   文件 / DB 接入与 parquet 物化
   ├── semantic/     语义包运行时（registry 加载 / render 渲染 / resolver 确定性解析）
   ├── evaluation/   评估流水线：固定问题集 + 分阶段指标 + CLI 报告
   └── report/       自包含 HTML 导出
   ▼
Agent 内核 (backend/agent/)
   ├── graph.py     LangGraph：parse_intent → generate_code → execute(自修复) → summarize → suggest_followups
   ├── prompts.py   各节点 system / user 提示词
   ├── profiler.py  数据画像（注入 prompt 的紧凑 schema 摘要）
   └── sandbox.py   Docker 无网络沙箱客户端 + dahelper 结果契约

基础设施：backend/config.py（全局配置单一入口）· db.py（SQLite WAL）· models.py / schemas.py · seed.py
配置：semantic_packs/*.yaml（业务语义唯一来源）
独立执行环境：sandbox/（镜像，**不属于** backend 包）
```

## 必须遵守

- **领域优先**：新增后端能力时先判断它属于哪个领域目录（analysis / skills / insights / datasource / semantic / report / agent）；不要新建 `services/`、`utils/` 这类"什么都放"的目录。
- **API 层不写业务逻辑**：`backend/routers/` 只做参数校验与转发，业务实现放对应领域模块。
- **依赖方向**：`routers/` → 领域模块 → `agent/` 内核 → `config`。领域模块之间穿透调用内部实现是不允许的；跨领域协作走公开入口（例如 Skill 需要重新生成分析时，调用 `backend/analysis/runtime.py`，而不是自己驱动图谱）。
- **内核线性流程不可重构**：`backend/agent/graph.py` 的 LangGraph 链路保持线性，只允许加 `AgentState.skill_block` 这类最小钩子。扩展能力优先在领域层实现。
- **配置单一入口**：路径 / LLM / 沙箱参数统一在 `backend/config.py`；不要在别处再造一份配置常量。设置中心热更新改的是同一个模块对象（`update_llm_config` / `invalidate_prefs_cache`）。
- **语义层边界**：`semantic_packs/` 是**配置**（业务语义唯一来源），`backend/semantic/` 是**运行时**（加载 + 渲染）。改指标口径只改 YAML，不要改渲染代码。
- **产物路径约定**：分析产物写 `runs/{run_id}/out`（result.json + PNG），由后端静态挂载 `/runs` 提供访问。
- **元数据库迁移**：改表结构需同步 `backend/models.py`、`backend/schemas.py`，新列在 `backend/db.py:_migrate` 补 ALTER TABLE，并在 `backend/seed.py` 回填存量数据。
- **可观测性跟随运行**：分析链路或 Skill 重放的行为变化，要同步维护 `Run.trace`（`analysis/runtime.py::_build_trace`、`skills/engine.py::_replay_trace`）与 `analysis/validation.py` 的验收项；失败轮同样写 trace。
- **指标门禁**：改语义包解析、Skill 匹配等行为后跑 `python -m backend.evaluation`，确认 `tests/evaluation/` 的指标阈值不回退；评估报告缺资源阶段如实标注「未采集」，禁止编造数字。

## 已知的后续重构（P2，本次未做）

- `backend/routers/` → `backend/api/`，`config/db/models/schemas` → `backend/core/`：让"API 层"与"基础设施"的边界更显式。
- `frontend/src/` 增加 `features/` 分域（analysis / skills / insights / dashboards / datasources / agents），把散落的组件、store、api 按功能收敛。

`sandbox/` 与 `examples/` 保持独立，不并入 backend。

## 历史决策

`enterprise-agentbi-refactor-plan.md`（同目录 plans/ 下）记录了从 Streamlit 单页到 FastAPI + React 前后端分离的完整改造决策，含已确认的技术选型与"不变的底层"清单，重构前建议通读。
