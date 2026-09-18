"""UserContext：可信后端解析出的"当前用户在某工作区的权限快照"。

User → WorkspaceMember → Role → Permission 链路唯一在此落地；
`permission_checker.has_permission` 是全项目唯一的权限判断入口，
业务代码不得写 `if user.role == "admin"` 这类硬编码。
"""

from dataclasses import dataclass, field, asdict
from typing import Any

from backend.models import RolePermission, WorkspaceMember


@dataclass
class UserContext:
    user_id: int
    username: str
    workspace_id: int
    role: str
    permissions: set[str] = field(default_factory=set)
    # data_scope：预留的数据可见范围（all=本工作区全部；未来可扩展行级）
    data_scope: str = "all"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["permissions"] = sorted(self.permissions)
        return d


def resolve_user_context(db, user_id: int, workspace_id: int | None = None) -> UserContext:
    """从数据库解析 UserContext（可信来源）。

    - workspace_id 为空时取该用户的第一个成员关系（默认工作区）。
    - 用户不存在 / 停用 / 不属于该工作区 → 抛 PermissionError（由 API 层转 401/403）。
    - 角色未知 → permissions 为空集（最小权限）。
    """
    from backend.models import User, Workspace

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise PermissionError("用户不存在或已停用")

    if workspace_id is None:
        member = (db.query(WorkspaceMember)
                  .filter(WorkspaceMember.user_id == user_id)
                  .order_by(WorkspaceMember.id).first())
    else:
        member = (db.query(WorkspaceMember)
                  .filter(WorkspaceMember.user_id == user_id,
                          WorkspaceMember.workspace_id == workspace_id)
                  .first())
    if not member:
        raise PermissionError("用户不属于该工作区")

    ws = db.get(Workspace, member.workspace_id)
    if not ws:
        raise PermissionError("工作区不存在")

    perms = {
        rp.permission_code
        for rp in db.query(RolePermission)
        .filter(RolePermission.role_code == member.role_code).all()
    }
    return UserContext(
        user_id=user.id,
        username=user.username,
        workspace_id=member.workspace_id,
        role=member.role_code,
        permissions=perms,
    )


class PermissionChecker:
    """权限判断单一入口：has_permission(context, "datasource:write")。

    context 支持 UserContext 实例或其 to_dict() 字典（跨线程/图谱 State
    传递的序列化形态），避免调用方各自反序列化。
    """

    @staticmethod
    def has_permission(context: UserContext | dict | None, permission: str) -> bool:
        if context is None:
            return False
        perms = context.permissions if isinstance(context, UserContext) \
            else context.get("permissions") or []
        return permission in perms

    @staticmethod
    def require(context: UserContext | dict | None, permission: str) -> None:
        """无权限直接抛 PermissionError（API 层统一转 403）。"""
        if not PermissionChecker.has_permission(context, permission):
            raise PermissionError(f"缺少权限 {permission}")


permission_checker = PermissionChecker()
