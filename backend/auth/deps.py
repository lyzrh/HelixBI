"""FastAPI 认证依赖：从请求解析 JWT → 解析 UserContext → 权限门。

- `get_current_context`：无 token / token 无效 → 401；用户或工作区失效 → 401。
- `require_permission(code)`：依赖工厂，注到需要该权限的路由上；
  Viewer 绕过前端直调 API 时同样在后端被拒。
- 工作区来源：`X-Workspace-Id` 头（前端切换工作区后重取 UserContext）；
  缺省时用用户默认工作区（第一个成员关系）。
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.context import UserContext, resolve_user_context
from backend.auth.security import decode_token
from backend.db import get_db


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def get_current_context(request: Request, db: Session = Depends(get_db)) -> UserContext:
    payload = decode_token(_extract_token(request) or "")
    if not payload:
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
        raise HTTPException(401, str(exc))


def require_permission(permission: str):
    """权限门依赖工厂：require_permission("datasource:write")。"""

    def _checker(context: UserContext = Depends(get_current_context)) -> UserContext:
        from backend.auth.context import permission_checker

        if not permission_checker.has_permission(context, permission):
            raise HTTPException(403, f"权限不足：需要 {permission}")
        return context

    return _checker
