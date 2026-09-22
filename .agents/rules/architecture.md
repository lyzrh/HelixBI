# 架构规则

> 动架构、加钩子、做跨层调用之前必读。上游权威文件：根目录 [AGENTS.md](../../AGENTS.md)。

## 分层结构

后端按**业务领域**（而非技术分层）组织，每个目录代表一项能力：

```
React 前端 (frontend/)
   │  fetch + SSE 流解析（统一注入 Authorization / X-Workspace-Id）
   ▼
API 层 (backend/routers/, :8000)
   │  仅参数校验、权限门与路由转发，不写业务逻辑
   ▼
领域层 (backend/ 下各领域目录)
   ├── auth/         认证与 RBAC：UserContext 解析 + 权限判断单一入口
   ├── analysis/     Analysis Runtime：驱动分析链路、SSE 事件映射、落库、Run.trace 可观测；explore 自助分析
   ├── skills/       Skill 沉淀 / 匹配 / 重放（重放轨迹含 llm.calls==0 证据）
   ├── insights/     规则扫描 + 定时调度 + LLM 诊断
   ├── datasource/   文件 / DB 接入与 parquet 物化
   ├── semantic/     语义包运行时（registry 加载 / render 渲染 / resolver 确定性解析）
   ├── evaluation/   评估流水线：固定问题集 + 分阶段指标 + 检索基准 + 自修复基准
   │                 + 成本/延迟基准（冻结开关的改造前 vs 优化后）+ CLI 报告
   └── report/       自包含 HTML 导出
   ▼
Agent 内核 (backend/agent/)
   ├── graph.py     LangGraph：parse_intent → generate_code → execute → classify(错误分类+验收门)
   │                → summarize → suggest_followups（classify 决定 summarize 还是回到 generate_code）
   ├── repair.py    Self-Repair V2：错误分类 / 定向修复策略 / error signature / 有界重试与复读检测（零 token）
   ├── acceptance.py 结果验收硬门槛（execution_ok + has_artifact），validation 与 Self-Repair 共用
   ├── intent.py    确定性意图快路径（命中即跳过 LLM 解析；"解释不了的内容"一律回落）
   ├── context.py   prompt 上下文装配（按命中口径收窄 / 去重 / 限额 + 分块 token 记账）
   ├── budget.py    LLM 运行预算（调用前预检 / 终止原因 / 结构化兜底）
   ├── sandbox_pool.py warm 沙箱容器池（复用 / 回收 / 排队 / 降级冷启动；安全限制与冷启动一致）
   ├── runerrors.py  运行期控制异常（取消 / 总时限；控制流必须穿透一切兜底）
   ├── followups.py 确定性追问推荐（hybrid：凑不满才补一次 LLM）
   ├── tokens.py    token 计数（tiktoken，失败降级为启发式估算并标注方法）
   ├── prompts.py   各节点 system / user 提示词（含按错误类别定制的修复处方）
   ├── profiler.py  数据画像（注入 prompt 的紧凑 schema 摘要；按文件指纹缓存）
   └── sandbox.py   Docker 无网络沙箱客户端 + dahelper 结果契约 + 结构化失败信息

基础设施：backend/config.py（全局配置单一入口）· db.py（SQLite WAL）· models.py / schemas.py · seed.py
配置：semantic_packs/*.yaml（业务语义唯一来源）
独立执行环境：sandbox/（镜像，**不属于** backend 包）
```

请求链路的完整顺序（认证在最前，权限门在每一层收口）：

```
Login → 认证（JWT）→ user_id → User Resolver → 当前 Workspace
  → Role / Permission / Data Scope → UserContext
  → LangGraph Agent → LLM → 权限检查 → Tools → DataSource → 结果
```

## 必须遵守

- **领域优先**：新增后端能力时先判断它属于哪个领域目录（auth / analysis / skills / insights / datasource / semantic / report / agent）；不要新建 `services/`、`utils/` 这类"什么都放"的目录。
- **API 层不写业务逻辑**：`backend/routers/` 只做参数校验、权限门与转发，业务实现放对应领域模块。
- **依赖方向**：`routers/` → 领域模块 → `agent/` 内核 → `config`。领域模块之间穿透调用内部实现是不允许的；跨领域协作走公开入口（例如 Skill 需要重新生成分析时，调用 `backend/analysis/runtime.py`，而不是自己驱动图谱）。`auth/` 处于依赖上游，任何领域模块都可以调用它做权限判断，但 `auth/` 不反向依赖业务领域。
- **内核线性流程不可重构**：`backend/agent/graph.py` 的 LangGraph 链路保持线性，只允许加 `AgentState.skill_block` / `AgentState.user_context` / `AgentState.repair_*` 这类最小钩子。扩展能力优先在领域层实现；`classify` 是自修复 V2 唯一新增的节点，不要借此引入 tool-calling 或循环编排。
- **权限收口在三处**：API 层 `require_permission(...)`、运行时入口（`analysis/runtime.py::run_analysis_stream`）、工具执行前（`agent/graph.py::execute`）。新增链路不要把权限检查"上移"到前端，也不要只在 API 层做。
- **数据隔离按工作区**：写入资产带 `workspace_id`，读取与沙箱入口按工作区过滤；Skill 额外有 `global / workspace / user` 作用域。详见 [auth-rbac.md](auth-rbac.md)。
- **配置单一入口**：路径 / LLM / 沙箱参数统一在 `backend/config.py`；不要在别处再造一份配置常量。设置中心热更新改的是同一个模块对象（`update_llm_config` / `invalidate_prefs_cache`）。运行期目录（`data/ uploads/ runs/`）也在这里统一创建——`runs/` 被静态挂载，缺失会导致启动崩溃。
- **语义层边界**：`semantic_packs/` 是**配置**（业务语义唯一来源），`backend/semantic/` 是**运行时**（加载 + 渲染）。改指标口径只改 YAML，不要改渲染代码。
- **产物路径约定**：分析产物写 `runs/{run_id}/out`（result.json + PNG），由后端静态挂载 `/runs` 提供访问（当前未鉴权，属已知技术债）。
- **元数据库迁移**：改表结构需同步 `backend/models.py`、`backend/schemas.py`，新列在 `backend/db.py:_migrate` 补 ALTER TABLE，并在 `backend/seed.py` 回填存量数据（RBAC 六表与角色 / 权限 / 默认工作区 / 管理员均由 seed 幂等播种）。
- **可观测性跟随运行**：分析链路或 Skill 重放的行为变化，要同步维护 `Run.trace`（`analysis/runtime.py::_build_trace` / `build_self_repair_trace`、`skills/engine.py::_replay_trace`）与 `analysis/validation.py` 的验收项；失败轮同样写 trace。自修复行为变化必须同时更新 `trace.self_repair`，Skill 重放要标注 `not_applicable`（证明"重放不进自修复"）。
- **验收门只有一处**：`execution_ok + has_artifact` 定义在 `backend/agent/acceptance.py`；`analysis/validation.py` 只在其上追加运行期软检查。新增"这轮算不算成功"的判断一律复用 `acceptance_gate()`，不要在别处另写一套。
- **自修复有界且零 token**：错误分类 / 策略选择 / 复读检测 / 额度控制全部是确定性纯函数（`backend/agent/repair.py`），不得在其中调用 LLM；上限永远是 `config.MAX_FIX_ATTEMPTS`（每类额度只会在其之内更早收手）；复读必须提前终止；离线仿真结论必须标注 `measured=false`。
- **成本优化必须可归因、可回退、不牺牲正确性**：所有 LLM 调用经 `agent/budget.py` 预检（`graph._guarded_invoke` 是唯一入口）；prompt 由 `agent/context.py` 统一装配并记账；省调用的路径只有确定性快路径 / Skill 重放 / 确定性追问三条，且每条都有 `config` 开关可回到改造前（`INTENT_MODE=llm`、`CONTEXT_POLICY=v1`、`FOLLOWUP_MODE=llm`）。快路径出现"解释不了的内容"必须回落 LLM——**不允许用更便宜的解析猜意图**。缓存只登记确定性只读内容，身份判定（权限 / 可见性 / 准入）永不入缓存。改动这些行为后跑 `python -m backend.evaluation --cost-benchmark`，`tests/evaluation/test_cost_benchmark.py` 是门禁。
- **指标门禁**：改语义包解析、Skill 匹配等行为后跑 `python -m backend.evaluation`，确认 `tests/evaluation/` 的指标阈值不回退；评估报告缺资源阶段如实标注「未采集」，禁止编造数字。
- **新增 / 移动领域目录或改动认证、权限、工作区行为时**，同一次提交里同步更新 `AGENTS.md` 架构地图、对应 `.agents/rules/*.md` 与 `docs/api.md`。

## 已知的后续重构（P2，本次未做）

- `backend/routers/` → `backend/api/`，`config/db/models/schemas` → `backend/core/`：让"API 层"与"基础设施"的边界更显式。
- `frontend/src/` 增加 `features/` 分域（analysis / skills / insights / dashboards / datasources / agents），把散落的组件、store、api 按功能收敛。

`sandbox/` 与 `examples/` 保持独立，不并入 backend。

## 历史决策

`enterprise-agentbi-refactor-plan.md`（同目录 plans/ 下）记录了从 Streamlit 单页到 FastAPI + React 前后端分离的完整改造决策，含已确认的技术选型与"不变的底层"清单，重构前建议通读。
