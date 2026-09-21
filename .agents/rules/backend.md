# 后端规则（FastAPI / Python）

## 分层与文件

- `backend/routers/`：**API 层**，一个业务域一个文件（auth/analysis/sessions/datasources/agents/skills/insights/dashboards/explore/settings/usage/misc）。router 只做参数校验、权限门、调用领域模块、组装响应，**不写业务逻辑、不直接操作 ORM 细节**。
- **领域模块**（按能力划分，不按技术划分）：
  - `backend/auth/`：认证与 RBAC（`security.py` 口令哈希 + JWT、`context.py` UserContext 解析 + `permission_checker`、`deps.py` `get_current_context` / `require_permission`）。
  - `backend/agent/`：Agent 内核与沙箱客户端（`graph.py` / `prompts.py` / `profiler.py` / `sandbox.py`）。
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
- 新增依赖先确认必要性，写入 `requirements.txt`；不要引入重量级框架替换现有 LangGraph 链路。
- 新增 / 移动领域目录、改动认证与权限行为时，同一次提交里同步更新 `AGENTS.md` 架构地图、`.agents/rules/` 与 `docs/api.md`。

## 启动与验证

```bash
python -m uvicorn backend.main:app --port 8000
pytest -q
```

改完后端后至少验证：服务能启动、种子数据可加载（首次运行 seed）、**登录可用**
（`curl -X POST localhost:8000/api/auth/login -d '{"username":"admin","password":"admin123"}' -H "Content-Type: application/json"`）、
匿名访问业务接口返回 401、相关 router 的冒烟请求通过、`pytest -q` 全绿。

改动认证 / 权限 / 工作区隔离时，额外跑 `pytest tests/test_auth_rbac.py -q` 并补对应 Case。

改了语义解析 / Skill 匹配 / 沙箱链路等影响指标的行为时，额外跑 `python -m backend.evaluation` 看 Evaluation Report（见 `.agents/rules/architecture.md` 的指标门禁条目）。

改了 Skill 检索 / 重放准入后，还要跑 `python -m backend.evaluation --benchmark`：False Replay 必须仍为 0，
Replay Precision 不得低于基线（`tests/evaluation/test_retrieval_gate.py` 会拦住回归）。
新增准入约束时同时补一条对抗样例到 `backend/evaluation/datasets/admission/`，
否则这类误重放没有回归保护。
