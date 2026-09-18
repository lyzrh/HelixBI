"""认证路由：/login（只负责认证）+ UserContext / 工作区 / 成员管理。

权限解析全部在后端：用户不能自选角色；角色由 WorkspaceMember 决定。
切换工作区 = 重新解析 UserContext（前端随后刷新上下文）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.context import resolve_user_context
from backend.auth.deps import get_current_context, require_permission
from backend.auth.security import create_token, hash_password, verify_password
from backend.db import get_db
from backend.models import (
    Permission, Role, User, Workspace, WorkspaceMember, jdump,
)

router = APIRouter(prefix="/auth")


class LoginBody(BaseModel):
    username: str = Field(min_length=1)  # 支持用户名或邮箱
    password: str = Field(min_length=1)


class SwitchWorkspaceBody(BaseModel):
    workspace_id: int


class UserCreateBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6)
    email: str = ""
    display_name: str = ""


class MemberAddBody(BaseModel):
    username: str
    role_code: str = Field(pattern="^(admin|analyst|viewer)$")


class MemberPatchBody(BaseModel):
    role_code: str = Field(pattern="^(admin|analyst|viewer)$")


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    """用户名 / 邮箱 + 口令 → JWT。只认证，不授权。"""
    user = (db.query(User)
            .filter((User.username == body.username) | (User.email == body.username))
            .first())
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = create_token(user.id, user.username)
    memberships = _memberships(db, user.id)
    if not memberships:
        raise HTTPException(403, "用户尚未加入任何工作区，请联系管理员")
    return {
        "token": token,
        "user": user.to_dict(),
        "workspaces": memberships,
    }


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    """当前 UserContext（含工作区与权限，全部来自后端解析）。"""
    from backend.auth.deps import _extract_token

    payload = decode_token(_extract_token(request) or "")
    if not payload:
        raise HTTPException(401, "未登录或登录已过期")
    ws_header = request.headers.get("X-Workspace-Id")
    try:
        ctx = resolve_user_context(db, int(payload["sub"]),
                                   int(ws_header) if ws_header else None)
    except (PermissionError, KeyError, ValueError) as exc:
        raise HTTPException(401, str(exc))
    return {
        "context": ctx.to_dict(),
        "workspaces": _memberships(db, ctx.user_id),
        "current_workspace_id": ctx.workspace_id,
    }


@router.post("/switch-workspace")
def switch_workspace(body: SwitchWorkspaceBody, request: Request,
                     db: Session = Depends(get_db)):
    """切换当前工作区：重新解析并返回 UserContext（前端替换本地上下文）。"""
    from backend.auth.deps import _extract_token

    payload = decode_token(_extract_token(request) or "")
    if not payload:
        raise HTTPException(401, "未登录或登录已过期")
    try:
        ctx = resolve_user_context(db, int(payload["sub"]), body.workspace_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return {"context": ctx.to_dict(), "current_workspace_id": ctx.workspace_id}


# ---- 成员 / 用户管理（workspace:manage / member:manage）----

@router.get("/permissions")
def list_permissions(db: Session = Depends(get_db)):
    return [p.to_dict() for p in db.query(Permission).order_by(Permission.code).all()]


@router.get("/roles")
def list_roles(db: Session = Depends(get_db)):
    from backend.models import RolePermission

    out = []
    for role in db.query(Role).order_by(Role.code).all():
        d = role.to_dict()
        d["permissions"] = sorted(
            rp.permission_code for rp in
            db.query(RolePermission).filter(RolePermission.role_code == role.code).all())
        out.append(d)
    return out


@router.get("/users")
def list_users(db: Session = Depends(get_db),
               _ctx: None = Depends(require_permission("workspace:manage"))):
    return [u.to_dict() for u in db.query(User).order_by(User.id).all()]


@router.post("/users")
def create_user(body: UserCreateBody, db: Session = Depends(get_db),
                _ctx: None = Depends(require_permission("workspace:manage"))):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "用户名已存在")
    user = User(username=body.username, email=body.email or None,
                display_name=body.display_name or body.username,
                password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    db.commit()
    return user.to_dict()


@router.get("/workspaces/{wsid}/members")
def list_members(wsid: int, db: Session = Depends(get_db),
                 ctx: object = Depends(require_permission("member:manage"))):
    rows = (db.query(WorkspaceMember, User)
            .join(User, WorkspaceMember.user_id == User.id)
            .filter(WorkspaceMember.workspace_id == wsid).all())
    return [{**m.to_dict(), "username": u.username, "display_name": u.display_name}
            for m, u in rows]


@router.post("/workspaces/{wsid}/members")
def add_member(wsid: int, body: MemberAddBody, db: Session = Depends(get_db),
               _ctx: None = Depends(require_permission("member:manage"))):
    ws = db.get(Workspace, wsid)
    if not ws:
        raise HTTPException(404, "工作区不存在")
    if not db.query(Role).get(body.role_code):
        raise HTTPException(400, "角色不存在")
    user = db.query(User).filter(User.username == body.username).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    exists = (db.query(WorkspaceMember)
              .filter(WorkspaceMember.workspace_id == wsid,
                      WorkspaceMember.user_id == user.id).first())
    if exists:
        raise HTTPException(400, "该用户已是工作区成员")
    member = WorkspaceMember(workspace_id=wsid, user_id=user.id, role_code=body.role_code)
    db.add(member)
    db.commit()
    return member.to_dict()


@router.patch("/workspaces/{wsid}/members/{uid}")
def patch_member(wsid: int, uid: int, body: MemberPatchBody,
                 db: Session = Depends(get_db),
                 _ctx: None = Depends(require_permission("member:manage"))):
    member = (db.query(WorkspaceMember)
              .filter(WorkspaceMember.workspace_id == wsid,
                      WorkspaceMember.user_id == uid).first())
    if not member:
        raise HTTPException(404, "成员关系不存在")
    if not db.get(Role, body.role_code):
        raise HTTPException(400, "角色不存在")
    member.role_code = body.role_code
    db.commit()
    return member.to_dict()


# ---- 内部工具 ----

def _memberships(db: Session, user_id: int) -> list[dict]:
    rows = (db.query(WorkspaceMember, Workspace)
            .join(Workspace, WorkspaceMember.workspace_id == Workspace.id)
            .filter(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.id).all())
    return [{"workspace_id": ws.id, "name": ws.name, "role_code": m.role_code,
             "description": ws.description} for m, ws in rows]
