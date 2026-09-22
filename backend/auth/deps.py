"""FastAPI 认证依赖：从请求解析 JWT → 解析 UserContext → 权限门。

- `get_current_context`：无 token / token 无效或过期 / 用户停用 → 401；
  请求头里的工作区不是你的成员关系 → 401（UserContext 无法建立，见状态码约定）；
- `require_permission(code)`：依赖工厂，注到需要该权限的路由上；
  Viewer 绕过前端直调 API 时同样在后端被拒（403）；
- 工作区来源：`X-Workspace-Id` 头（前端切换工作区后重取 UserContext）；
  缺省时用用户默认工作区（第一个成员关系）。

状态码约定（全项目统一，安全事件都进审计）：

| 码 | 含义 |
| --- | --- |
| 400 | 请求参数本身不合法 |
| 401 | 未认证 / 令牌无效或过期 / 请求的工作区不在你的成员关系内 |
| 403 | 已认证，但对该操作缺少权限点 |
| 404 | 资源不存在，**或**资源不属于你的工作区（不暴露存在性） |

token 类型在这里被强制收敛：只有 `typ=access` 的 JWT 能建立 UserContext，
refresh token（不透明串）与任何其它类型都无法访问业务 API。
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth import audit
from backend.auth.context import UserContext, permission_checker, resolve_user_context
from backend.auth.security import ACCESS_TYP, decode_token
from backend.db import get_db


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def get_current_context(request: Request, db: Session = Depends(get_db)) -> UserContext:
    raw = _extract_token(request)
    payload = decode_token(raw or "", expected_type=ACCESS_TYP)
    if not payload:
        if raw:
            # 带了令牌却被拒（过期 / 被篡改 / 类型不对）——值得留痕；匿名访问不记，避免噪声
            audit.record("auth.token_rejected", "denied",
                         detail={"reason": "invalid_or_expired"}, request=request)
        raise HTTPException(401, "未登录或登录已过期")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(401, "登录态无效")

    workspace_id = request.headers.get("X-Workspace-Id")
    try:
        ws_id = int(workspace_id) if workspace_id else None
    except ValueError:
        raise HTTPException(400, "X-Workspace-Id 无效")
    try:
        return resolve_user_context(db, user_id, ws_id)
    except PermissionError as exc:
        # 伪造 / 越界的 X-Workspace-Id：既拒又留痕（跨工作区访问被拒事件）
        audit.record("authz.workspace_denied", "denied", user_id=user_id,
                     workspace_id=ws_id, detail={"reason": str(exc)},
                     request=request)
        raise HTTPException(401, str(exc))


def require_permission(permission: str):
    """权限门依赖工厂：require_permission("datasource:write")。"""

    def _checker(request: Request,
                 context: UserContext = Depends(get_current_context)) -> UserContext:
        if not permission_checker.has_permission(context, permission):
            audit.record_denied("authz.permission_denied", ctx=context,
                                target=permission, detail={"required": permission},
                                request=request)
            raise HTTPException(403, f"权限不足：需要 {permission}")
        return context

    return _checker


def require_any_permission(*permissions: str):
    """任一权限点满足即放行（用于"读类"接口的宽口径门）。"""

    def _checker(request: Request,
                 context: UserContext = Depends(get_current_context)) -> UserContext:
        if not any(permission_checker.has_permission(context, p) for p in permissions):
            audit.record_denied("authz.permission_denied", ctx=context,
                                target="|".join(permissions),
                                detail={"required_any": list(permissions)},
                                request=request)
            raise HTTPException(403, f"权限不足：需要 {' 或 '.join(permissions)}")
        return context

    return _checker


__all__ = ["get_current_context", "require_any_permission", "require_permission"]
