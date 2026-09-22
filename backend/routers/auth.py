"""认证路由：/login（只负责认证）+ Token 生命周期 + UserContext / 工作区 / 成员管理。

权限解析全部在后端：用户不能自选角色；角色由 WorkspaceMember 决定。
切换工作区 = 重新解析 UserContext（前端随后刷新上下文）。

Token 生命周期（Security Hardening V1）：
- `/login` 返回**短期 access token + 可撤销 refresh token**；
- `/refresh` 轮换（旧 refresh 立即失效，重放会牵连整族）；
- `/logout` 吊销当前会话，`/revoke-all` 吊销本人全部，管理员可强制下线某用户；
- token 里只有身份（`sub`/`username`/`typ`），**权限永远实时解析**。
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import audit, tokens
from backend.auth.context import resolve_user_context
from backend.auth.deps import _extract_token, get_current_context, require_permission
from backend.auth.security import (
    create_token, decode_token, hash_password, verify_password,
)
from backend.db import get_db
from backend.models import (
    Permission, Role, User, Workspace, WorkspaceMember, jdump,
)

router = APIRouter(prefix="/auth")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,64}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginBody(BaseModel):
    username: str = Field(min_length=1)  # 支持用户名或邮箱
    password: str = Field(min_length=1)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=8)


class LogoutBody(BaseModel):
    refresh_token: str | None = None


class SwitchWorkspaceBody(BaseModel):
    workspace_id: int


class RegisterBody(BaseModel):
    """自助注册：只创建认证身份，不授予任何工作区与角色。

    明确不提供 role / workspace 字段——即使客户端传入也会被忽略，
    角色只能由工作区管理员通过成员管理接口分配。
    """
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str
    display_name: str = ""


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


@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    """自助注册（公开端点）：仅创建 User，不创建任何 WorkspaceMember。

    注册成功 ≠ 有权限：用户必须由 admin 加入工作区后（Membership + Role）
    才能登录使用，登录链路对此已有 403 防线。
    """
    if not _USERNAME_RE.match(body.username):
        raise HTTPException(400, "用户名只能包含字母、数字、点、下划线、连字符，长度 2-64")
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(400, "邮箱格式不正确")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "用户名已存在")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "邮箱已被注册")
    user = User(
        username=body.username,
        email=body.email,
        display_name=body.display_name.strip()[:64] or body.username,
        # 口令只存 pbkdf2_sha256 哈希，绝不存明文（auth/security.py）
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    return user.to_dict()


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    """用户名 / 邮箱 + 口令 → access token + refresh token。只认证，不授权。"""
    user = (db.query(User)
            .filter((User.username == body.username) | (User.email == body.username))
            .first())
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        # 失败一律同一个 401 文案 + 审计（用户名照记，口令绝不落库）
        audit.record("auth.login", "failed", user_id=user.id if user else None,
                     username=body.username, detail={"reason": "bad_credentials"},
                     request=request)
        raise HTTPException(401, "用户名或密码错误")
    memberships = _memberships(db, user.id)
    if not memberships:
        audit.record("auth.login", "denied", user_id=user.id, username=user.username,
                     detail={"reason": "no_workspace"}, request=request)
        raise HTTPException(403, "用户尚未加入任何工作区，请联系管理员")

    token = create_token(user.id, user.username)
    refresh_token, _row = tokens.issue_refresh(
        db, user.id, workspace_id=memberships[0]["workspace_id"],
        user_agent=request.headers.get("User-Agent", ""))
    audit.record("auth.login", "ok", user_id=user.id, username=user.username,
                 workspace_id=memberships[0]["workspace_id"], request=request)
    return {
        "token": token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": _access_ttl(),
        "refresh_expires_in": tokens.refresh_ttl_seconds(),
        "user": user.to_dict(),
        "workspaces": memberships,
    }


@router.post("/refresh")
def refresh_token(body: RefreshBody, request: Request, db: Session = Depends(get_db)):
    """刷新访问令牌（轮换 refresh token）。

    旧 refresh token 一旦使用即失效；若再次出现，视为泄露重放 →
    吊销该登录会话的全部令牌（详见 `backend/auth/tokens.py`）。
    """
    try:
        access, new_refresh, _row = tokens.rotate(
            db, body.refresh_token, user_agent=request.headers.get("User-Agent", ""),
            request=request)
    except tokens.TokenError as exc:
        # 可预期的失败（无效/过期/已撤销/重放）统一 401，细节已在审计里留痕
        raise HTTPException(401, exc.message)
    return {"token": access, "refresh_token": new_refresh, "token_type": "bearer",
            "expires_in": _access_ttl(),
            "refresh_expires_in": tokens.refresh_ttl_seconds()}


@router.post("/logout")
def logout(body: LogoutBody, request: Request, db: Session = Depends(get_db)):
    """登出：吊销当前 refresh token（幂等，永远 200，客户端总能清本地态）。"""
    revoked = False
    if body.refresh_token:
        revoked = tokens.revoke(db, body.refresh_token, tokens.REASON_LOGOUT)
    audit.record("auth.logout", "ok" if revoked else "noop", request=request,
                 detail={"revoked": revoked})
    return {"ok": True, "revoked": revoked}


@router.post("/revoke-all")
def revoke_all(request: Request, db: Session = Depends(get_db),
               ctx=Depends(get_current_context)):
    """使本人全部令牌失效（改密 / 怀疑泄露时的自助操作）。"""
    count = tokens.revoke_all_for_user(db, ctx.user_id, tokens.REASON_REVOKE_ALL)
    audit.record("auth.token_revoked", "ok", user_id=ctx.user_id,
                 username=ctx.username, workspace_id=ctx.workspace_id,
                 target=f"user:{ctx.user_id}", detail={"revoked": count,
                                                       "scope": "self"},
                 request=request)
    return {"ok": True, "revoked": count}


@router.get("/sessions")
def my_sessions(db: Session = Depends(get_db), ctx=Depends(get_current_context)):
    """当前有效登录会话（refresh token 族）——便于用户自查异常登录。"""
    return tokens.active_sessions(db, ctx.user_id)


@router.post("/users/{uid}/revoke")
def revoke_user_tokens(uid: int, request: Request, db: Session = Depends(get_db),
                       ctx=Depends(require_permission("member:manage"))):
    """管理员强制下线指定用户（吊销其全部 refresh token）。"""
    target = (db.query(WorkspaceMember)
              .filter(WorkspaceMember.workspace_id == ctx.workspace_id,
                      WorkspaceMember.user_id == uid).first())
    if not target:
        raise HTTPException(404, "用户不是当前工作区成员")
    count = tokens.revoke_all_for_user(db, uid, tokens.REASON_ADMIN)
    audit.record("auth.token_revoked", "ok", user_id=ctx.user_id, username=ctx.username,
                 workspace_id=ctx.workspace_id, target=f"user:{uid}",
                 detail={"revoked": count, "scope": "admin"}, request=request)
    return {"ok": True, "revoked": count}


@router.get("/audit")
def security_audit(limit: int = 50, event: str = "", db: Session = Depends(get_db),
                   ctx=Depends(require_permission("member:manage"))):
    """安全事件审计（管理员）：登录 / 刷新 / 吊销 / 越权被拒 / 产物访问被拒 / 凭据变更。

    只读本人工作区相关事件之外的全局记录属管理员权限；返回内容经过脱敏，
    永远不含口令与令牌原文。
    """
    return {"items": audit.recent(db, limit=limit, event=event),
            "viewer": {"user_id": ctx.user_id, "workspace_id": ctx.workspace_id}}


def _access_ttl() -> int:
    from backend.auth.security import access_ttl

    return access_ttl()



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
def list_permissions(db: Session = Depends(get_db),
                     _ctx=Depends(require_permission("member:manage"))):
    """权限点清单：面向成员管理界面，管理员可见（原先匿名可枚举，已收口）。"""
    return [p.to_dict() for p in db.query(Permission).order_by(Permission.code).all()]


@router.get("/roles")
def list_roles(db: Session = Depends(get_db),
               _ctx=Depends(require_permission("member:manage"))):
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
                 ctx=Depends(require_permission("member:manage"))):
    _ensure_same_workspace(ctx, wsid)
    rows = (db.query(WorkspaceMember, User)
            .join(User, WorkspaceMember.user_id == User.id)
            .filter(WorkspaceMember.workspace_id == wsid).all())
    return [{**m.to_dict(), "username": u.username, "display_name": u.display_name,
             "email": u.email}
            for m, u in rows]


@router.post("/workspaces/{wsid}/members")
def add_member(wsid: int, body: MemberAddBody, db: Session = Depends(get_db),
               ctx=Depends(require_permission("member:manage"))):
    _ensure_same_workspace(ctx, wsid)
    ws = db.get(Workspace, wsid)
    if not ws:
        raise HTTPException(404, "工作区不存在")
    if not db.query(Role).get(body.role_code):
        raise HTTPException(400, "角色不存在")
    user = db.query(User).filter(User.username == body.username).first()
    if not user:
        raise HTTPException(404, "用户不存在（请先让该用户完成注册）")
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
                 ctx=Depends(require_permission("member:manage"))):
    _ensure_same_workspace(ctx, wsid)
    member = (db.query(WorkspaceMember)
              .filter(WorkspaceMember.workspace_id == wsid,
                      WorkspaceMember.user_id == uid).first())
    if not member:
        raise HTTPException(404, "成员关系不存在")
    if not db.get(Role, body.role_code):
        raise HTTPException(400, "角色不存在")
    # 末位管理员保护：降级最后一个 admin 会导致工作区失去管理能力（锁死）
    if member.role_code == "admin" and body.role_code != "admin":
        _ensure_not_last_admin(db, wsid, uid)
    member.role_code = body.role_code
    db.commit()
    return member.to_dict()


@router.delete("/workspaces/{wsid}/members/{uid}")
def remove_member(wsid: int, uid: int, db: Session = Depends(get_db),
                  ctx=Depends(require_permission("member:manage"))):
    """移除成员：移除后该用户立即失去本工作区的全部权限（角色随成员关系删除）。"""
    _ensure_same_workspace(ctx, wsid)
    member = (db.query(WorkspaceMember)
              .filter(WorkspaceMember.workspace_id == wsid,
                      WorkspaceMember.user_id == uid).first())
    if not member:
        raise HTTPException(404, "成员关系不存在")
    if member.role_code == "admin":
        _ensure_not_last_admin(db, wsid, uid)
    db.delete(member)
    db.commit()
    return {"ok": True}


# ---- 内部工具 ----

def _ensure_same_workspace(ctx, wsid: int) -> None:
    """成员管理只能作用于自己所在（且有权管理的）工作区，防止改 id 越权。"""
    if ctx.workspace_id != wsid:
        raise HTTPException(403, "不能管理其他工作区的成员")


def _ensure_not_last_admin(db: Session, wsid: int, uid: int) -> None:
    admins = (db.query(WorkspaceMember)
              .filter(WorkspaceMember.workspace_id == wsid,
                      WorkspaceMember.role_code == "admin").all())
    if len(admins) <= 1 and any(m.user_id == uid for m in admins):
        raise HTTPException(400, "不能移除或降级工作区最后一名管理员")

def _memberships(db: Session, user_id: int) -> list[dict]:
    rows = (db.query(WorkspaceMember, Workspace)
            .join(Workspace, WorkspaceMember.workspace_id == Workspace.id)
            .filter(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.id).all())
    return [{"workspace_id": ws.id, "name": ws.name, "role_code": m.role_code,
             "description": ws.description} for m, ws in rows]
