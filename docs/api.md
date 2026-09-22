# API 参考

> 后端 FastAPI 服务的 HTTP 接口清单，与 `backend/routers/` 实现保持一致。
> 权限模型与开发约定见 [.agents/rules/auth-rbac.md](../.agents/rules/auth-rbac.md)。
> 交互式文档：服务启动后访问 `/docs`（Swagger UI）或 `/openapi.json`。

## 认证

所有业务接口默认需要登录（`backend/routers/misc.py` 的公开端点除外）。

| 请求头 | 说明 |
| --- | --- |
| `Authorization: Bearer <token>` | 登录后签发的 JWT；缺失或过期 → `401` |
| `X-Workspace-Id: <id>` | 当前工作区；缺省时后端使用该用户的第一个工作区 |

- **Token 生命周期**（Security Hardening V1）：`access token` 是短期 JWT
  （默认 1 小时，`HELIX_ACCESS_TOKEN_TTL`，载荷含 `typ="access"`）；`refresh token`
  是不透明随机串，默认 30 天，**只存哈希**、每次刷新轮换、可撤销、可重放检测。
- Token 内只含身份（`sub` / `username` / `iat` / `exp` / `typ`），**不含角色与权限**；角色与权限由后端按 `User → WorkspaceMember → Role → Permission` 实时解析（改权限立即生效，无需重新登录）。
- 用户不能自选角色；不属于该工作区时返回 `401`，权限不足返回 `403`，资源不属于该工作区返回 `404`（不暴露存在性）。
- 业务接口只接受 `typ="access"` 的 JWT；refresh token 无法当 access 用。
- access 过期时前端自动用 refresh 换新并重试一次（`frontend/src/api/client.ts`）。

### 登录示例

```bash
# 1. 登录（用户名或邮箱 + 口令）
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → {"token":"eyJ...","refresh_token":"...","expires_in":3600,"refresh_expires_in":2592000,
#     "user":{...},"workspaces":[{"workspace_id":1,"name":"默认工作区","role_code":"admin"}]}

# 2. 带 token 调用业务接口
TOKEN=eyJ...
curl http://127.0.0.1:8000/api/datasources -H "Authorization: Bearer $TOKEN"

# 3. access 过期后刷新（旧 refresh 立即失效；重复使用会被判定重放并吊销整族）
curl -X POST http://127.0.0.1:8000/api/auth/refresh   -H "Content-Type: application/json" -d '{"refresh_token":"..."}'

# 4. 登出（吊销 refresh token；幂等，永远 200）
curl -X POST http://127.0.0.1:8000/api/auth/logout   -H "Content-Type: application/json" -d '{"refresh_token":"..."}'

# 3. 切换到另一个工作区后重新获取 UserContext
curl -X POST http://127.0.0.1:8000/api/auth/switch-workspace \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"workspace_id":2}'
```

内置管理员由 seed 创建：`admin` / `admin123`（**首次部署后请立即修改密码并新建账号**）。

### 状态码约定

| 码 | 含义 |
| --- | --- |
| `400` | 参数错误 / 业务校验失败（如 SQL 非只读、文件类型不支持） |
| `401` | 未登录、token 过期、用户不属于该工作区 |
| `403` | 已登录但缺少所需权限点（前端隐藏按钮不构成保护） |
| `404` | 资源不存在，或资源不属于当前工作区（产物 / 运行 / 数据源 / 仪表板 / Skill） |
| `413` | 上传文件超过大小限制 |
| `429` | 已有分析任务在执行（沙箱并发护栏） |

## 认证与工作区

| 方法 | 路径 | 所需权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | 公开 | 用户名 / 邮箱 + 口令 → access + refresh token + 工作区列表 |
| POST | `/api/auth/refresh` | 公开（凭 refresh token） | 轮换刷新：旧 refresh 立即失效；**重放会吊销整族**（`401`） |
| POST | `/api/auth/logout` | 公开（凭 refresh token） | 吊销该 refresh token（幂等，返回 `revoked` 布尔） |
| POST | `/api/auth/revoke-all` | 登录 | 使本人全部令牌失效（改密 / 怀疑泄露时的自助操作） |
| GET | `/api/auth/sessions` | 登录 | 本人当前有效的登录会话（refresh 令牌族） |
| POST | `/api/auth/users/{uid}/revoke` | `member:manage` | 管理员强制下线指定用户（仅限本工作区成员） |
| GET | `/api/auth/audit` | `member:manage` | 安全事件审计（支持 `event` 前缀过滤与 `limit`），内容已脱敏 |
| POST | `/api/auth/register` | 公开 | 自助注册：仅创建认证身份（pbkdf2 哈希存储），不授予任何工作区与角色；角色字段即使传入也被忽略 |
| GET | `/api/auth/me` | 登录 | 当前 UserContext（含角色与权限集合）+ 工作区列表 |
| POST | `/api/auth/switch-workspace` | 登录 | 切换工作区并重新解析 UserContext |
| GET | `/api/auth/permissions` | `member:manage` | 全部权限点定义（原先匿名可读，已收口） |
| GET | `/api/auth/roles` | `member:manage` | 角色及其权限集合（同上） |
| GET | `/api/auth/users` | `workspace:manage` | 用户列表 |
| POST | `/api/auth/users` | `workspace:manage` | 新建用户（用户名 / 口令 / 显示名） |
| GET | `/api/auth/workspaces/{wsid}/members` | `member:manage` | 工作区成员列表 |
| POST | `/api/auth/workspaces/{wsid}/members` | `member:manage` | 添加成员并指定角色 |
| PATCH | `/api/auth/workspaces/{wsid}/members/{uid}` | `member:manage` | 调整成员角色（末位管理员不可降级） |
| DELETE | `/api/auth/workspaces/{wsid}/members/{uid}` | `member:manage` | 移除成员（立即失去本工作区权限；末位管理员不可移除；仅限本工作区） |

`/api/auth/me` 返回的 `context` 结构：

```json
{
  "user_id": 1, "username": "admin", "workspace_id": 1,
  "role": "admin",
  "permissions": ["analysis:create", "analysis:execute", "analysis:read", "..."],
  "data_scope": "all"
}
```

## 对话与分析

| 方法 | 路径 | 所需权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/sessions` | `analysis:create` | 新建会话（记录创建者与工作区） |
| GET | `/api/sessions` | 登录 | 会话列表（按当前工作区过滤） |
| GET | `/api/sessions/{sid}` | 登录 | 会话详情 + 历史消息（跨工作区访问 `403`） |
| PATCH | `/api/sessions/{sid}` | 登录 | 改标题 / 绑定场景 Agent |
| DELETE | `/api/sessions/{sid}` | 登录 | 删除会话及其消息与运行记录 |
| POST | `/api/sessions/{sid}/analyze` | `analysis:execute` | **SSE** 流式分析（Skill 匹配 → 图谱执行 → 落库） |
| POST | `/api/sessions/{sid}/parse` | `analysis:execute` | 仅跑意图解析，返回 QuerySpec 供人工确认 |
| GET | `/api/runs/recent` | `analysis:read` | 最近分析运行（按工作区过滤） |
| GET | `/api/runs/{rid}` | `analysis:read` | 运行详情（含 `trace`：`stages` / `semantic` / `skill.retrieval` / `self_repair` / **`cost_control`（调用归因 / 预算使用 / 终止原因）** / **`performance`（阶段耗时 / 瓶颈 / 缓存命中 / 上下文分块记账）** / `llm` / `validation`；跨工作区 404） |
| GET | `/api/runs/{rid}/export` | `analysis:read` | 导出单轮分析 HTML 报告（图表路径越界一律忽略） |
| POST | `/api/runs/{rid}/rerun` | `analysis:execute` | 用存量 spec 重跑（**SSE**，同样受工作区隔离约束） |

### SSE 事件协议（`text/event-stream`）

帧格式 `event: <type>\ndata: <json>\n\n`；每 15s 发一次 `: ping` 保活；响应头含 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。

| event | data | 时机 |
| --- | --- | --- |
| `step` | `{node, label, status: running\|done\|error, detail, attempt?}` | 每节点开始 / 结束（node 含图谱的 `classify`，以及后端附加的 `materialize` / `skill`；`classify` 的 label 会写明「识别为『列不存在』→ 定向修复（schema_alignment）」，终止重试时 `detail` 给出决策原因） |
| `spec` | `{spec}` | 意图解析完成 |
| `code` | `{plan, code, attempt}` | 每次生成代码（自修复会多次） |
| `execute` | `{ok, stdout, stderr, run_dir, charts, tables, text}` | 每次沙箱执行（自修复会多次；失败原因与修复决策见 `Run.trace.self_repair`） |
| `answer` | `{answer}` | 结论生成完成 |
| `followups` | `{followups}` | 追问推荐完成 |
| `charts` / `tables` | 产物相对 URL / 表格数据 | 最终成功后 |
| `done` | `{run_id, message_id, ok, duration_ms}` | 结束且已落库 |
| `error` | `{message}` | 异常后关闭流 |

## 数据源与自助分析

| 方法 | 路径 | 所需权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/datasources/upload` | `datasource:write` | 上传文件（csv/tsv/xlsx/xls/json/parquet），归属当前工作区 |
| POST | `/api/datasources/db` | `datasource:write` | 新建数据库连接（先测试后保存） |
| POST | `/api/datasources/test` | `datasource:write` | 连接探测（`max_tokens=1` 级轻量测试） |
| GET | `/api/datasources` | `datasource:read` | 数据源列表（按工作区过滤） |
| GET | `/api/datasources/{dsid}` | `datasource:read` | 详情（含语义包详情） |
| GET | `/api/datasources/{dsid}/tables` | `datasource:read` | 数据库表清单 |
| GET | `/api/datasources/{dsid}/preview` | `datasource:read` | 数据预览 |
| POST | `/api/datasources/{dsid}/materialize` | `datasource:write` | 物化为 parquet 缓存 |
| PATCH | `/api/datasources/{dsid}` | `datasource:write` | 改名 / 切换语义包 |
| DELETE | `/api/datasources/{dsid}` | `datasource:write` | 删除（内置示例不可删） |
| GET | `/api/explore/schema` | `datasource:read` | 字段结构（自助分析左侧字段栏；数据源按工作区校验，越权 404） |
| GET | `/api/explore/profile` | `datasource:read` | 字段画像（缺失率 / 基数 / 分布 / 高频值） |
| POST | `/api/explore/query` | `analysis:execute` | 拖拽聚合查询（本地 pandas，零 token） |
| POST | `/api/explore/sql` | `sql:execute` | 只读 SQL 查询（本地 SQLite 执行，表名 `t`；数据源按工作区校验） |

## Skill / 仪表板 / 洞察

| 方法 | 路径 | 所需权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/skills` | `skill:read` | Skill 列表（`global ∪ 本工作区 ∪ 本人`） |
| POST | `/api/skills` | `skill:write` | 手工创建 Skill |
| POST | `/api/skills/from-run` | `skill:write` | 从成功运行沉淀 Skill |
| GET / PATCH | `/api/skills/{skid}` | `skill:read` / `skill:write` | 详情 / 改名停用 |
| DELETE | `/api/skills/{skid}` | `skill:write` | 删除（内置不可删） |
| POST | `/api/skills/{skid}/run` | `analysis:execute` | **SSE** 运行 Skill（列匹配 → 重放，零 LLM） |
| GET | `/api/dashboards`、`/{did}` | `dashboard:read` | 仪表板列表 / 详情 |
| POST / PATCH / DELETE | `/api/dashboards...` | `dashboard:write` | 仪表板与条目增删改、排序 |
| GET | `/api/dashboards/{did}/export` | `dashboard:read` | 导出自包含 HTML |
| POST | `/api/insights/generate` | `analysis:execute` | 立即扫描并生成洞察（数据源须属当前工作区） |
| GET | `/api/insights`、`/{iid}`、`/export`、`/schedule` | `datasource:read` | 列表 / 详情 / CSV 导出 / 调度配置查看（按工作区过滤） |
| POST | `/api/insights/{iid}/report` | `analysis:execute` | LLM 诊断报告（本工作区洞察） |
| PATCH | `/api/insights/{iid}` | `datasource:write` | 状态流转（本工作区洞察） |
| PUT / POST | `/api/insights/schedule`、`/schedule/run` | `workspace:manage` | 修改调度 / 立即全量扫描（系统级，仅 admin） |

## 场景 Agent / 设置 / 用量 / 工具

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/agents`、`/{aid}` | 登录 | 场景 Agent 列表 / 详情（全员可用） |
| POST / PATCH / DELETE | `/api/agents...` | `workspace:manage` | 场景 Agent 管理（仅 admin） |
| GET | `/api/settings/llm` | 登录 | LLM 接口配置（含密钥掩码返回） |
| PUT | `/api/settings/llm`、`/llm/test` | `workspace:manage` | 修改 LLM 配置 / 连通性测试（仅 admin） |
| GET / PUT | `/api/settings/profile`、`/preferences` | 登录 | 个人资料 / 回答风格偏好 |
| GET | `/api/usage/summary`、`/history` | `usage:read` | 用量与成本聚合（**按工作区过滤**；平台级经营信息，当前仅 admin） |
| GET | `/api/usage/sessions/{sid}/tokens` | `analysis:read` | 单次运行的 token 明细（跨工作区 404） |
| POST | `/api/utils/export_table` | 登录 | 前端表格导出 Excel（仅导出请求方已持有的数据） |

## 公开端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（沙箱可用性 / LLM 配置状态 / 语义包列表） |
| GET | `/api/semantic/packs` | 语义包列表 |
| GET | `/api/stats` | 首页统计 |
| GET | `/runs/{run_key}/...` | `analysis:read` | Run 产物下发；`run_key` 支持运行目录名或 `Run.id`，归属由 `Run → Session.workspace_id` 反查，孤儿运行 404，`../` 一律拒绝 |
| GET | `/uploads/{file}`、`/data/materialized/{file}` | `datasource:read` | 上传源文件 / 物化 parquet 下发（只认服务登记过的路径；`/data` 其余内容不暴露） |

## 技术债

- 静态挂载未鉴权问题已修复（`backend/routers/files.py` 鉴权下发，按工作区校验）。
- `settings` / `agents` / `insights` / `usage` 的权限点已细分（Security Hardening V1 新增 `analysis:read` / `usage:read` / `settings:write`；`agents` 的写入沿用 `workspace:manage`；`utils/export_table` 仅导出前端已持有数据，保持登录门）。
- 完整安全设计、审计事件清单与剩余限制见 [docs/security.md](security.md)。
- 成本 / 延迟指标口径、预算终止原因与上下文分块记账见 [docs/cost-latency-v1.md](cost-latency-v1.md)；一键复现 `python -m backend.evaluation --cost-benchmark`。
- SSE 生命周期（`state` 事件）：`queued → preparing → running → repairing → validating → completed`，
  异常终态 `cancelled / timeout / resource_limited / failed`——每个 run 必有终态，前端不会一直 loading；
  运行时容量与沙箱池状态见 `GET /api/health` 的 `runtime` 段。
- 运行时设计（warm pool / 并发 / 超时取消）见 [docs/production-runtime-v1.md](production-runtime-v1.md)。
- 无刷新令牌与注销黑名单；`data_sources.password` 仍为明文字段。
