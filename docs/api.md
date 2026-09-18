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

- Token 内只含身份（`sub` / `username` / `iat` / `exp`，有效期 12 小时），**不含角色与权限**；角色与权限由后端按 `User → WorkspaceMember → Role → Permission` 实时解析。
- 用户不能自选角色；不属于该工作区时返回 `401`，权限不足返回 `403`。

### 登录示例

```bash
# 1. 登录（用户名或邮箱 + 口令）
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → {"token":"eyJ...","user":{...},"workspaces":[{"workspace_id":1,"name":"默认工作区","role_code":"admin"}]}

# 2. 带 token 调用业务接口
TOKEN=eyJ...
curl http://127.0.0.1:8000/api/datasources -H "Authorization: Bearer $TOKEN"

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
| `404` | 资源不存在 |
| `413` | 上传文件超过大小限制 |
| `429` | 已有分析任务在执行（沙箱并发护栏） |

## 认证与工作区

| 方法 | 路径 | 所需权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | 公开 | 用户名 / 邮箱 + 口令 → JWT + 工作区列表 |
| GET | `/api/auth/me` | 登录 | 当前 UserContext（含角色与权限集合）+ 工作区列表 |
| POST | `/api/auth/switch-workspace` | 登录 | 切换工作区并重新解析 UserContext |
| GET | `/api/auth/permissions` | 登录 | 全部权限点定义 |
| GET | `/api/auth/roles` | 登录 | 角色及其权限集合 |
| GET | `/api/auth/users` | `workspace:manage` | 用户列表 |
| POST | `/api/auth/users` | `workspace:manage` | 新建用户（用户名 / 口令 / 显示名） |
| GET | `/api/auth/workspaces/{wsid}/members` | `member:manage` | 工作区成员列表 |
| POST | `/api/auth/workspaces/{wsid}/members` | `member:manage` | 添加成员并指定角色 |
| PATCH | `/api/auth/workspaces/{wsid}/members/{uid}` | `member:manage` | 调整成员角色 |

`/api/auth/me` 返回的 `context` 结构：

```json
{
  "user_id": 1, "username": "admin", "workspace_id": 1,
  "role": "admin",
  "permissions": ["analysis:create", "analysis:execute", "..."],
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
| GET | `/api/runs/recent` | 登录 | 最近分析运行 |
| GET | `/api/runs/{rid}` | 登录 | 运行详情（含 `trace` 可观测数据） |
| GET | `/api/runs/{rid}/export` | 登录 | 导出单轮分析 HTML 报告 |
| POST | `/api/runs/{rid}/rerun` | `analysis:execute` | 用存量 spec 重跑（**SSE**，同样受工作区隔离约束） |

### SSE 事件协议（`text/event-stream`）

帧格式 `event: <type>\ndata: <json>\n\n`；每 15s 发一次 `: ping` 保活；响应头含 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。

| event | data | 时机 |
| --- | --- | --- |
| `step` | `{node, label, status: running\|done\|error, detail, attempt?}` | 每节点开始 / 结束（node 含后端附加的 `materialize` / `skill`） |
| `spec` | `{spec}` | 意图解析完成 |
| `code` | `{plan, code, attempt}` | 每次生成代码（自修复会多次） |
| `execute` | `{ok, stdout, stderr, run_dir, charts, tables, text}` | 每次沙箱执行 |
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
| GET | `/api/explore/schema` | `datasource:read` | 字段结构（自助分析左侧字段栏） |
| GET | `/api/explore/profile` | `datasource:read` | 字段画像（缺失率 / 基数 / 分布 / 高频值） |
| POST | `/api/explore/query` | `analysis:execute` | 拖拽聚合查询（本地 pandas，零 token） |
| POST | `/api/explore/sql` | `sql:execute` | 只读 SQL 查询（本地 SQLite 执行，表名 `t`） |

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
| POST | `/api/insights/generate` | 登录 | 立即扫描并生成洞察 |
| GET / POST / PATCH | `/api/insights...` | 登录 | 列表 / 诊断报告 / 状态流转 / 定时扫描配置 |

## 场景 Agent / 设置 / 用量 / 工具

以下接口当前**只要求登录**（未细分权限点，见技术债）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST / PATCH / DELETE | `/api/agents...` | 场景 Agent 管理 |
| GET / PUT | `/api/settings/llm` | LLM 接口配置（含密钥掩码返回） |
| POST | `/api/settings/llm/test` | LLM 连通性测试 |
| GET / PUT | `/api/settings/profile` | 个人资料 |
| GET / PUT | `/api/settings/preferences` | 回答风格 / 创意度 / 追问开关 / 自定义指令 |
| GET | `/api/usage/summary`、`/history`、`/sessions/{sid}/tokens` | Token 用量统计 |
| POST | `/api/utils/export_table` | 前端表格导出 Excel |

## 公开端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（沙箱可用性 / LLM 配置状态 / 语义包列表） |
| GET | `/api/semantic/packs` | 语义包列表 |
| GET | `/api/stats` | 首页统计 |
| GET | `/runs/...`、`/uploads/...`、`/data/...` | 静态产物（**当前未鉴权**，见技术债） |

## 技术债

- 三个静态挂载未鉴权，持有 URL 即可访问分析产物与上传文件。
- `settings` / `agents` / `insights` / `usage` / `utils` 只做登录校验，未细分权限点。
- 无刷新令牌与注销黑名单；`data_sources.password` 仍为明文字段。
