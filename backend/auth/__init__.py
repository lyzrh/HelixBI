"""认证与 RBAC 领域：认证（security）、UserContext 解析（context）、API 依赖（deps）。

设计约束：
- Login 只负责认证；角色/权限由后端根据 User → WorkspaceMember → Role →
  Permission 解析，用户不可自选，LLM 不可决定或修改权限。
- UserContext 只来自可信后端数据，作为唯一权限判断依据。
- JWT 使用 stdlib HS256 实现（零新增依赖），密钥从环境变量读取。
"""

from backend.auth.context import UserContext, permission_checker, resolve_user_context
from backend.auth.deps import get_current_context, require_permission

__all__ = [
    "UserContext", "permission_checker", "resolve_user_context",
    "get_current_context", "require_permission",
]
