# 后端规则（FastAPI / Python）

## 分层与文件

- `backend/routers/`：**API 层**，一个业务域一个文件（auth/analysis/sessions/datasources/agents/skills/insights/dashboards/explore/settings/usage/misc）。router 只做参数校验、权限门、调用领域模块、组装响应，**不写业务逻辑、不直接操作 ORM 细节**。
- **领域模块**（按能力划分，不按技术划分）：
  - `backend/auth/`：认证与 RBAC（`security.py` 口令哈希 + JWT、`context.py` UserContext 解析 + `permission_checker`、`deps.py` `get_current_context` / `require_permission`）。
  - `backend/agent/`：Agent 内核与沙箱客户端（`graph.py` / `prompts.py` / `profiler.py` / `sandbox.py`），
    以及 Self-Repair V2 的 `repair.py`（错误分类 / 定向修复策略 / error signature / 有界重试，零 token）
    与 `acceptance.py`（结果验收硬门槛，`analysis/validation.py` 在其上追加软检查）。
  - `backend/analysis/`：`runtime.py` 对话式分析运行器（SSE 流式 + 落库）；`explore.py` 自助分析（本地 pandas / 只读 SQL，零 token）。
  - `backend/skills/`：`engine.py` Skill 捕获 / 重放执行 / few-shot / 失败兜底；`retrieval.py` **检索与重放准入**（候选召回 → 8 路可解释打分 → blocker + 分层准入，零 token、纯确定性，权重在 `config.SKILL_RETRIEVAL_WEIGHTS`）。
  - `backend/insights/`：`engine.py` 规则扫描 + LLM 诊断，`scheduler.py` 定时任务。
  - `backend/datasource/`：`service.py` 文件 / 数据库接入、预览、parquet 物化。
  - `backend/semantic/`：语义包运行时（`registry.py` 加载检索 + `render.py` 渲染 prompt）。
  - `backend/report/`：`builder.py` 单轮分析 HTML，`exporter.py` 仪表板导出。
- 基础设施：`backend/config.py`（全局配置单一入口：路径 / LLM / 沙箱 / 数据接入）、`backend/db.py`（SQLite WAL）、`models.py` / `schemas.py`、`seed.py`。
- 配置数据：`semantic_packs/*.yaml`（业务语义唯一来源）；LLM 连接信息支持 `.env` 与设置中心热更新（改端点保存即生效，无需重启）。

## 认证与权限（详见 [auth-rbac.md](auth-rbac.md)）

- **UserContext 是唯一权限依据**：`backend/auth/context.py::resolve_user_context` 从 `User → WorkspaceMember → Role → Permission` 解析；Login 只认证，token 内不含权限。
- **判断一律走 `permission_checker.has_permission(ctx, "xxx:yyy")`**；禁止 `if user.role == "admin"` 这类硬编码角色判断（角色是权限的容器，新增能力先加权限点再授权）。
- **新增 router 必须挂登录门**（`dependencies=[Depends(get_current_context)]`），写 / 执行类端点挂 `require_permission("...")`；只有 `/api/health`、`/api/semantic/packs`、`/api/stats` 属公开白名单。
- **401 / 403 由 auth 依赖统一抛出**，业务代码不手写状态码；工作区来自 `X-Workspace-Id` 请求头，缺省用用户第一个工作区。
- **口令与密钥**：口令用 `hash_password` / `verify_password`（pbkdf2-sha256，stdlib）；JWT 用 `create_token` / `decode_token`（HS256，密钥取 `HELIX_JWT_SECRET`）。**不引入 JWT / 密码学第三方库**，如需刷新令牌等能力再评估替换。
- **写入资产带 `workspace_id`**（数据源 / Skill / 会话），列表与详情按工作区过滤；跨工作区访问返回 403。

## 惯例

- Python 3.11+，异步接口用 `async def`；SSE 用 StreamingResponse 输出 `spec/code/step/answer/chart/table` 等事件。
- SQLite 处于 WAL 模式，不要关闭；所有元数据访问走 SQLAlchemy，不要裸写 SQL 字符串拼接。
- LLM 调用统一走 OpenAI 兼容接口（DeepSeek / 智谱 / 通义 / vLLM 均可），不要引入厂商私有 SDK。
- Token 纪律：Skill 重放不经过 LLM；连接测试只发 `max_tokens=1` 探测；洞察诊断等批量任务注意控制上下文长度。
  自修复 V2 同样**不新增 LLM 调用**——失败时只是把同一次生成调用的提示换成按错误类别定制的处方
  （`agent/repair.py::STRATEGIES`），复读或额度用尽则直接停止重试。
- **成本纪律**（Cost & Latency Optimization V1）：所有 LLM 调用必须经
  `agent/graph.py::_guarded_invoke`（预检预算 → 调用 → 记账），不允许在节点里直接 `llm.invoke`；
  prompt 一律由 `agent/context.py` 装配（它同时产出分块 token 记账），不要在各节点手拼上下文；
  省调用的三条合法路径是"确定性意图快路径 / Skill 重放 / 确定性追问"，**不要用放宽解析门槛的方式省钱**。
- **缓存纪律**：确定性缓存必须登记在 `analysis/cache.py::describe()`，key 要包含能区分数据归属的维度
  （workspace / 文件指纹 / Skill 更新时间），且**只缓存数据与配置类派生结果**——权限、可见性、
  准入结论一律不缓存（`tests/test_cache_isolation.py` 会拦住）。
- 新增依赖先确认必要性，写入 `requirements.txt`；不要引入重量级框架替换现有 LangGraph 链路。
- 新增 / 移动领域目录、改动认证与权限行为时，同一次提交里同步更新 `AGENTS.md` 架构地图、`.agents/rules/` 与 `docs/api.md`。

## 运行时纪律（Production Runtime V1）

- **沙箱执行一律走 `agent/sandbox.py::run_in_sandbox`**（warm pool → 冷启动 → 结构化失败的
  降级阶梯已内建），不要绕过它直接 `docker run`；warm 容器的安全限制必须与冷启动逐项一致。
- **取消 / 总时限是控制流**：`RunCancelled` / `RunDeadlineExceeded`（`agent/runerrors.py`）
  必须穿透一切兜底分支（Skill 重放兜底、意图解析回退、追问降级都不得吞掉）；
  在任何阻塞动作（LLM 调用 / 沙箱执行）前调用 `check_runtime_limits`。
- **并发必须过 `analysis/concurrency.py::get_registry`**（全局 + 单用户 + 队列）；
  等待队列满 = `RunRejected` 明确拒绝，不允许静默无限排队。
- 运行时指标写 `Run.trace.runtime`（queue_wait / sandbox_acquire / container_reused /
  timeout_type / cancellation_reason）；运行时容量统计在 `/api/health.runtime`。

## 启动与验证

```bash
python -m uvicorn backend.main:app --port 8000
pytest -q
```

改完后端后至少验证：服务能启动、种子数据可加载（首次运行 seed）、**登录可用**
（`curl -X POST localhost:8000/api/auth/login -d '{"username":"admin","password":"admin123"}' -H "Content-Type: application/json"`）、
匿名访问业务接口返回 401、相关 router 的冒烟请求通过、`pytest -q` 全绿。

改动认证 / 权限 / 工作区隔离时，额外跑 `pytest tests/test_auth_rbac.py -q` 并补对应 Case。

改了语义解析 / Skill 匹配 / 沙箱链路等影响指标的行为时，额外跑 `python -m backend.evaluation` 看 Evaluation Report（报告已含 Self-Repair 与 Cost & Latency 两段）（见 `.agents/rules/architecture.md` 的指标门禁条目）。

改了 Skill 检索 / 重放准入后，还要跑 `python -m backend.evaluation --benchmark`：False Replay 必须仍为 0，
Replay Precision 不得低于基线（`tests/evaluation/test_retrieval_gate.py` 会拦住回归）。

改了错误分类 / 修复策略 / 重试额度 / 结果验收门后，跑 `python -m backend.evaluation --repair-benchmark`
（离线策略仿真，`measured=false`）与 `pytest tests/test_self_repair_loop.py tests/test_repair_strategy.py -q`；
`tests/evaluation/test_self_repair_eval.py` 会拦住成功率回退、上限被放大、场景期望不一致等回归。
有 Docker + LLM 时另跑 `--repair-benchmark --repair-live` 采集真实指标（缺资源时如实 skipped）。

改了意图快路径 / 上下文装配 / 预算 / 缓存等成本相关行为后，跑
`python -m backend.evaluation --cost-benchmark`（离线，桩化 LLM 与沙箱；调用次数与 prompt token 是实测，
LLM/沙箱耗时是投影）与 `pytest tests/test_cost_routing.py tests/test_llm_budget.py tests/test_context_assembly.py tests/test_cache_isolation.py -q`；
`tests/evaluation/test_cost_benchmark.py` 会拦住"调用与 token 没降""快路径命中处与人工标注不一致"
"确定性耗时劣化""重放不再是零 LLM"等回归。**默认（零配置）预算只拦病态循环**：
新增预算项时保持"0 / 留空 = 不限制"，不要把正常分析掐掉。
新增准入约束时同时补一条对抗样例到 `backend/evaluation/datasets/admission/`，
否则这类误重放没有回归保护。
