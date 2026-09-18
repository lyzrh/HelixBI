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
| `admin` | 管理员 | 全部 11 项 |
| `analyst` | 分析师 | datasource:read/write、sql:execute、analysis:create/execute、dashboard:read/write、skill:read/write |
| `viewer` | 查看者 | datasource:read、dashboard:read、skill:read |

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

`GET /api/health`、`GET /api/semantic/packs`、`GET /api/stats`（`backend/routers/misc.py` 未挂认证依赖）。
新增公开端点必须在评审中说明理由。

## Agent / Tool 集成

- `AgentState.user_context` 是 RBAC 的唯一钩子（与 `skill_block` 同模式），图谱结构**不得**因权限重构。
- 双层校验：`analysis/runtime.py::run_analysis_stream` 入口检查 `analysis:execute`；`agent/graph.py::execute` 节点在执行沙箱前再检查一次（纵深防御，覆盖"有人绕过 API 直接驱动图谱"的情况）。
- `analysis/runtime.py::_prepare` 按 `workspace_id` 过滤数据源：非本工作区且非全局（`workspace_id IS NULL`）的数据源即使拿到 id 也进不了沙箱。
- `Run.trace.user` 记录本轮的用户 / 工作区 / 角色，便于审计；失败轮同样留痕。

## 隔离规则（防串数据）

| 资产 | 规则 |
| --- | --- |
| 数据源 | `data_sources.workspace_id` 归属工作区；列表与沙箱入口双重过滤 |
| Skill | `scope` = `global` / `workspace` / `user`；匹配与列表按 `global ∪ 本工作区 ∪ 本人` 过滤 |
| 会话（Memory 载体） | `sessions.workspace_id`；跨工作区访问返回 403 |
| 成员与工作区管理 | `workspace:manage` / `member:manage`，只有 admin 拥有 |

## 密码与密钥

- 口令哈希：`pbkdf2_sha256$iterations$salt$hash`（`backend/auth/security.py`，stdlib，零新依赖）。
- JWT：stdlib HS256 手写实现，密钥取环境变量 `HELIX_JWT_SECRET`；未设置时进程内随机生成（**单机开发可接受，多进程 / 生产部署必须显式配置**）。
- 红线：不提交 `.env`、不把密钥/凭据写进日志与异常信息、不把 token 落进前端日志。

## 新增受权限控制的功能：检查清单

1. 需要什么权限点？不存在就先在 `backend/seed.py` 的 `PERMISSIONS` 与 `ROLE_PERMISSIONS` 里加，并走 `db.py::_migrate` + seed 幂等播种。
2. Router 挂 `get_current_context`，写操作挂 `require_permission(...)`。
3. 业务代码用 `permission_checker.has_permission`，不硬编码角色。
4. 写库的资产带 `workspace_id`（必要时带 `user_id`）；读取路径按工作区过滤。
5. 若是分析链路 / Skill 重放的行为变化，同步维护 `Run.trace` 与 `analysis/validation.py`。
6. 补测试：`tests/test_auth_rbac.py` 是按 Case 组织的，新权限照该文件补用例（正面 + 绕过直调被拒）。

## 已知缺口（技术债，勿当成"已安全"）

- `/runs`、`/uploads`、`/data` 三个静态挂载未鉴权，知道 URL 即可访问产物；后续需改为带鉴权的文件下发。
- `settings` / `agents` / `insights` 等 router 目前只挂登录门，未细分权限点。
- `data_sources.password` 仍是明文字段（沿袭改造前状态），需加密存储或改由外部密钥托管。
- 无刷新令牌 / 注销黑名单，token 过期即需重新登录。
