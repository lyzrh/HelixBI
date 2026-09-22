# 认证 / 工作区 / RBAC 规则

> 动登录、权限、工作区隔离、数据作用域之前必读。上游权威文件：根目录 [AGENTS.md](../../AGENTS.md)。
> 接口清单见 [docs/api.md](../../docs/api.md)。

## 信任边界（不可协商）

1. **Login 只负责认证**：`POST /api/auth/login` 校验口令后签发 JWT，token 内**只放身份**（`sub`/`username`），不放角色与权限——权限每次请求实时从数据库解析，避免 token 内权限过期。
2. **UserContext 只来自可信后端数据**：`backend/auth/context.py::resolve_user_context` 是唯一构造入口，链路固定为
   `User → WorkspaceMember → Workspace → Role → Permission`。用户不能自选角色（不做角色下拉），前端传的任何角色字段都不可信。
3. **LLM 不允许决定或修改权限**：权限块不进 prompt，也不允许由模型输出推导权限。
4. **前端隐藏按钮不是安全措施**：所有权限判断在后端执行；Viewer 绕过前端直调 API 必须被拒。

## 唯一权限入口

```python
from backend.auth.context import permission_checker

# 业务代码里唯一允许的权限判断写法
if permission_checker.has_permission(user_context, "datasource:write"):
    ...
```

禁止在业务代码里写 `if user.role == "admin"`、`if ctx.role in ("admin", "analyst")` 这类硬编码角色判断——
角色是权限的**容器**，不是判断依据；新增能力先加权限点，再给角色授权（`backend/seed.py` 的 `ROLE_PERMISSIONS`）。

## 角色与权限点

| 角色 code | 名称 | 权限集合 |
| --- | --- | --- |
| `admin` | 管理员 | 全部 14 项 |
| `analyst` | 分析师 | datasource:read/write、sql:execute、analysis:create/execute/**read**、dashboard:read/write、skill:read/write |
| `viewer` | 查看者 | datasource:read、**analysis:read**、dashboard:read、skill:read |

> Security Hardening V1 新增三个权限点：`analysis:read`（查看分析结果与产物，含下载导出）、
> `usage:read`（用量与成本，平台级 → 仅 admin）、`settings:write`（LLM 接口等平台设置 → 仅 admin）。
> 只用"确实对应一类资源 / 一类操作"的权限点，`agents` 的写操作继续复用 `workspace:manage`。

权限点（`permissions` 表）：`datasource:read` `datasource:write` `sql:execute` `analysis:create`
`analysis:execute` `dashboard:read` `dashboard:write` `skill:read` `skill:write` `workspace:manage` `member:manage`。

同一用户在不同工作区可以有不同角色（Sales → analyst，Marketing → viewer），切换工作区即重新解析 UserContext。

## API 层用法

```python
from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission

# 1) 需要某权限：权限门依赖
router = APIRouter(prefix="/xxx", dependencies=[Depends(get_current_context)])  # 至少登录

@router.post("")
def create_xxx(body: XxxBody, db: Session = Depends(get_db),
               ctx: UserContext = Depends(require_permission("datasource:write"))):
    ...  # ctx.workspace_id 用于写入归属

# 2) 只需要登录、不需要具体权限：注入 get_current_context 取工作区
@router.get("")
def list_xxx(db: Session = Depends(get_db), ctx: UserContext = Depends(get_current_context)):
    ...
```

- 新增业务 router **必须**挂 `dependencies=[Depends(get_current_context)]`；只有显式列入「公开端点」的才允许匿名。
- 403 由 `require_permission` 统一抛出；401 由 `get_current_context` 统一抛出，不要在业务代码里手写状态码。
- 工作区来源：请求头 `X-Workspace-Id`；缺省时用用户的第一个成员关系作默认工作区。

### 公开端点（白名单）

`GET /api/health`、`GET /api/semantic/packs`、`GET /api/stats`（`backend/routers/misc.py` 未挂认证依赖）、
`POST /api/auth/register`（自助注册：只创建认证身份，不授予任何工作区与角色；请求体中的角色字段被忽略）。
新增公开端点必须在评审中说明理由。

## Agent / Tool 集成

- `AgentState.user_context` 是 RBAC 的唯一钩子（与 `skill_block` 同模式），图谱结构**不得**因权限重构。
- 双层校验：`analysis/runtime.py::run_analysis_stream` 入口检查 `analysis:execute`；`agent/graph.py::execute` 节点在执行沙箱前再检查一次（纵深防御，覆盖"有人绕过 API 直接驱动图谱"的情况）。
- `analysis/runtime.py::_prepare` 按 `workspace_id` 过滤数据源：非本工作区且非全局（`workspace_id IS NULL`）的数据源即使拿到 id 也进不了沙箱。
- `Run.trace.user` 记录本轮的用户 / 工作区 / 角色，便于审计；失败轮同样留痕。

## 隔离规则（防串数据）

| 资产 | 规则 |
| --- | --- |
| 数据源 | `data_sources.workspace_id` 归属工作区；列表/单查/物化/改删全链路过滤，越权 404 |
| Skill | `scope` = `global` / `workspace` / `user`；匹配与列表按 `global ∪ 本工作区 ∪ 本人` 过滤 |
| 会话（Memory 载体） | `sessions.workspace_id`；跨工作区访问 403；改名/删除需 `analysis:create` + 本工作区 |
| 运行记录 Run | 随所属 session 工作区隔离；读取/导出/重跑/token 用量明细一律校验，越权 404 |
| 仪表板 | `dashboards.workspace_id`；列表按「本工作区 ∪ 历史 NULL」过滤，跨工作区按 id 直取统一 404 |
| 洞察 Insight | 经 `data_source_id` 间接归属工作区；读需 `datasource:read`、生成/诊断需 `analysis:execute`、状态流转需 `datasource:write`、调度修改需 `workspace:manage` |
| 文件（Run 产物/上传文件/物化 parquet） | 不再使用 StaticFiles 匿名挂载；`backend/routers/files.py` 鉴权下发：产物需 `analysis:read`、上传/物化需 `datasource:read`；URL key 支持运行目录名或 `Run.id`，归属一律由 `Run → Session.workspace_id` 反查；**孤儿运行（会话已删）404**；路径遍历（含 URL 编码变体）拒绝；`/data` 只暴露已登记的物化文件；被拒写 `artifact.access_denied` 审计 |
| 导出链路（仪表板 / 单轮报告） | `chart_url_to_path` 拒绝越界路径；仪表板导出按「本工作区可见产物目录 + 可见运行 id」过滤条目；`source_run_id` 写入时校验归属 |
| 成员与工作区管理 | `workspace:manage` / `member:manage`，只有 admin 拥有；成员管理只能作用于自己所在工作区；末位管理员不可降级 / 移除 |
| 系统配置（LLM / 场景 Agent / 洞察调度） | LLM 设置：读分级（`base_url` 仅 `settings:write`，密钥只回掩码）、写需 `settings:write`；场景 Agent 写在 `workspace:manage`；洞察调度 `workspace:manage`；个人资料 / 偏好**按 user_id 隔离存储**（创意度属平台级 → 需 `settings:write`） |
| 用量与成本 | 聚合（summary / history）需 `usage:read` 且**按工作区过滤**；单次运行明细走 `analysis:read`（跨工作区 404） |

## Token 生命周期

| 项 | 策略 | 位置 |
| --- | --- | --- |
| access token | 短期 JWT（默认 1h，`HELIX_ACCESS_TOKEN_TTL`），载荷含 `typ="access"`；业务接口只认这一种 | `auth/security.py`、`auth/deps.py` |
| refresh token | 不透明随机串，库里只存 `sha256`；默认 30 天（`HELIX_REFRESH_TOKEN_TTL`） | `auth/tokens.py`、`refresh_tokens` 表 |
| 轮换 / 重放 | 每次 refresh 吊销旧行签发新行；旧 token 再次出现 → 整族（`family_id`）吊销 + `auth.token_replay` 审计 | 同上 |
| 撤销 | `/auth/logout`（本会话）、`/auth/revoke-all`（本人）、`/auth/users/{uid}/revoke`（管理员） | `routers/auth.py` |
| 多进程 | 撤销状态在 DB，任意实例同一结论（不依赖进程内存） | 同上 |

**不变量**：token 只放身份（`sub`/`username`/`typ`），权限永远实时解析——改权限立即生效、
不需要重新登录；refresh 不能当 access 用；任何"把权限写进 token"的改动都是回退。

## 秘密与审计

- 口令哈希：`pbkdf2_sha256$iterations$salt$hash`（`backend/auth/security.py`，stdlib，零新依赖）。
- JWT 密钥：环境变量 `HELIX_JWT_SECRET`；未设置时进程内随机生成（**单机开发可接受，多进程 / 生产部署必须显式配置**）。
- **数据源凭据加密**：`backend/datasource/secrets.py`，主密钥只来自 `HELIX_SECRET_KEY`；
  明文只在落库前加密、取连接时在后端解密，API 只回 `has_password`；缺密钥时**拒绝保存**并给出
  生成密钥的提示，任何环境都不得静默降级写明文；启动时 `migrate_plaintext_passwords()` 幂等迁移历史明文。
- **审计**：`backend/auth/audit.py` + `audit_logs` 表，记录 login / logout / refresh / token_replay /
  token_revoked / token_rejected / permission_denied / workspace_denied / artifact.access_denied /
  credential_saved|migrated / settings.llm_updated|tested；`redact()` 按 key 名抹除 password / token /
  secret / api_key / credential，超长截断；审计用**独立 Session** 落库（被拒请求的事务会回滚），失败不影响主流程。
- 红线：不提交 `.env`、不把密钥/凭据写进日志与异常信息、不把 token 落进前端日志、
  **新增审计事件必须过 `redact()`**。

## 新增受权限控制的功能：检查清单

1. 需要什么权限点？不存在就先在 `backend/seed.py` 的 `PERMISSIONS` 与 `ROLE_PERMISSIONS` 里加，并走 `db.py::_migrate` + seed 幂等播种。
2. Router 挂 `get_current_context`，写操作挂 `require_permission(...)`。
3. 业务代码用 `permission_checker.has_permission`，不硬编码角色。
4. 写库的资产带 `workspace_id`（必要时带 `user_id`）；读取路径按工作区过滤。
5. 若是分析链路 / Skill 重放的行为变化，同步维护 `Run.trace` 与 `analysis/validation.py`。
6. 补测试：`tests/test_auth_rbac.py` 是按 Case 组织的，新权限照该文件补用例（正面 + 绕过直调被拒）；
   安全相关用例放 `tests/test_security_*.py`（token 生命周期 / 产物访问 / 凭据加密 / 越权矩阵）。

## 已知缺口（技术债，勿当成"已安全"）

- 数据源凭据已加密（`datasource/secrets.py`），但用的是**标准库自研构造**（HKDF + HMAC-CTR +
  encrypt-then-MAC）；生产建议换成 AES-GCM / Fernet（格式已带 `v1` 版本号，替换成本低）。
- **元数据库本身未加密**：`data/app.db` 可读到业务元数据与凭据密文（拿不到明文，但仍是单点）。
- 无登录限流 / 爆破防护、审计表无保留期与轮转、`HELIX_*` 密钥缺失只在日志告警而不阻止启动。
- 后端重启（`HELIX_JWT_SECRET` 未显式配置时密钥进程内随机）会使全部 access token 失效；
  refresh token 存在库里不受影响，但仍应在生产显式配置。
- 场景 Agent（scene_agents）为全局资产，无 workspace 归属；读写已按「读登录门 / 写 workspace:manage」收口，若需按工作区隔离需加列。
- `data_scope` 仍是占位（未实现行级数据权限）；HTTP 层不含 TLS / HSTS，部署必须由反代提供 HTTPS。

完整清单见 [docs/security.md](../../docs/security.md) 的 Remaining Limitations。
