"""用户注册 + Workspace 成员管理 + 越权防护测试（Case 8-14）。

复用 test_auth_rbac.py 的隔离策略：模块级独立临时 SQLite + TestClient。
覆盖：注册（哈希存储/不授予角色）、成员增删改（403 门 + 末位管理员保护）、
跨工作区参数篡改、仪表板工作区隔离。
"""

import importlib
import os
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("register_members_db")
    os.environ["APP_DB_PATH"] = str(db_dir / "test_app.db")

    import backend.config as config
    importlib.reload(config)
    import backend.db as db_mod
    importlib.reload(db_mod)

    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as c:
        yield c

    os.environ.pop("APP_DB_PATH", None)


@pytest.fixture(scope="module")
def db_session(api):
    from backend.db import SessionLocal

    with SessionLocal() as db:
        yield db


@pytest.fixture(scope="module")
def env(api, db_session):
    """默认工作区（种子 admin）+ 第二工作区（另一名 admin）+ 后缀。"""
    from backend.models import User, Workspace, WorkspaceMember
    from backend.auth.security import hash_password

    suffix = uuid.uuid4().hex[:6]
    ws_b = Workspace(name=f"Beta-{suffix}", description="第二工作区")
    db_session.add(ws_b)
    db_session.flush()
    admin_b = User(username=f"admin-b-{suffix}", display_name="Beta管理员",
                   password_hash=hash_password("admin-b-pass-123"))
    db_session.add(admin_b)
    db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=ws_b.id, user_id=admin_b.id,
                                   role_code="admin"))
    db_session.commit()
    return {"suffix": suffix, "ws_a": 1, "ws_b": ws_b.id, "admin_b": admin_b}


def _register(api, suffix: str, name: str, email: str | None = None,
              password: str = "reg-pass-12345", **extra):
    body = {"username": f"{name}-{suffix}", "email": email or f"{name}-{suffix}@test.io",
            "password": password, "display_name": name}
    body.update(extra)
    return api.post("/api/auth/register", json=body)


def _login(api, username: str, password: str):
    return api.post("/api/auth/login", json={"username": username, "password": password})


def _token(api, username: str, password: str) -> str:
    resp = _login(api, username, password)
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _headers(token: str, workspace_id: int | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if workspace_id:
        h["X-Workspace-Id"] = str(workspace_id)
    return h


# ---- Case 8：注册 ----

def test_case8_register_creates_user_without_any_permission(api, env, db_session):
    suffix = env["suffix"]
    resp = _register(api, suffix, "carol")
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == f"carol-{suffix}"

    from backend.models import User, WorkspaceMember
    user = db_session.query(User).filter(User.username == f"carol-{suffix}").first()
    # 密码安全哈希：库里没有明文，且 pbkdf2 校验通过
    assert user.password_hash != "reg-pass-12345"
    assert user.password_hash.startswith("pbkdf2_sha256$")
    from backend.auth.security import verify_password
    assert verify_password("reg-pass-12345", user.password_hash)
    # 注册不授予任何工作区 → 无 Membership → 无角色无权限
    assert not (db_session.query(WorkspaceMember)
                .filter(WorkspaceMember.user_id == user.id).first())

    # 未加入工作区前不能登录（403 防线），更拿不到任何角色
    resp = _login(api, f"carol-{suffix}", "reg-pass-12345")
    assert resp.status_code == 403
    assert "尚未加入任何工作区" in resp.json()["detail"]


def test_case8_register_duplicate_and_validation(api, env):
    suffix = env["suffix"]
    assert _register(api, suffix, "carol").status_code == 400  # 用户名重复
    assert _register(api, suffix, "dave", email=f"carol-{suffix}@test.io").status_code == 400  # 邮箱重复
    assert _register(api, suffix, "bad email!", email="not-an-email").status_code == 400
    assert api.post("/api/auth/register", json={
        "username": f"shortpw-{suffix}", "email": f"shortpw-{suffix}@test.io",
        "password": "1234567"}).status_code == 422  # 密码过短


def test_case8_register_role_field_is_ignored(api, env, db_session):
    """即使请求体塞入 role=admin / workspace_id 也被忽略，绝不授予管理员。"""
    suffix = env["suffix"]
    resp = _register(api, suffix, "mallory", role="admin", workspace_id=env["ws_a"])
    assert resp.status_code == 200

    from backend.models import User, WorkspaceMember
    user = db_session.query(User).filter(User.username == f"mallory-{suffix}").first()
    assert user is not None
    assert not (db_session.query(WorkspaceMember)
                .filter(WorkspaceMember.user_id == user.id).first())
    assert _login(api, f"mallory-{suffix}", "reg-pass-12345").status_code == 403


# ---- Case 9：Admin 管理成员（增 / 改 / 删）----

def _add_member(api, token, wsid, username, role):
    return api.post(f"/api/auth/workspaces/{wsid}/members", headers=_headers(token, wsid),
                    json={"username": username, "role_code": role})


def test_case9_admin_manages_members_full_flow(api, env, db_session):
    suffix = env["suffix"]
    _register(api, suffix, "erin")   # 未来 analyst
    _register(api, suffix, "frank")  # 未来 viewer

    admin_token = _token(api, "admin", "admin123")
    ws_a = env["ws_a"]

    # 添加 erin 为 analyst / frank 为 viewer
    resp = _add_member(api, admin_token, ws_a, f"erin-{suffix}", "analyst")
    assert resp.status_code == 200, resp.text
    erin_uid = resp.json()["user_id"]
    resp = _add_member(api, admin_token, ws_a, f"frank-{suffix}", "viewer")
    assert resp.status_code == 200
    frank_uid = resp.json()["user_id"]

    # 加入后即可登录，角色由 Membership 决定
    erin_token = _token(api, f"erin-{suffix}", "reg-pass-12345")
    ctx = api.get("/api/auth/me", headers=_headers(erin_token, ws_a)).json()["context"]
    assert ctx["role"] == "analyst"
    assert "analysis:execute" in ctx["permissions"] and "member:manage" not in ctx["permissions"]

    frank_token = _token(api, f"frank-{suffix}", "reg-pass-12345")
    ctx = api.get("/api/auth/me", headers=_headers(frank_token, ws_a)).json()["context"]
    assert ctx["role"] == "viewer"
    assert ctx["permissions"] == sorted(["datasource:read", "dashboard:read", "skill:read"])

    # 修改角色 viewer → analyst
    resp = api.patch(f"/api/auth/workspaces/{ws_a}/members/{frank_uid}",
                     headers=_headers(admin_token, ws_a), json={"role_code": "analyst"})
    assert resp.status_code == 200, resp.text
    ctx = api.get("/api/auth/me", headers=_headers(frank_token, ws_a)).json()["context"]
    assert ctx["role"] == "analyst"

    # 成员列表含邮箱与角色
    rows = api.get(f"/api/auth/workspaces/{ws_a}/members", headers=_headers(admin_token, ws_a)).json()
    by_name = {r["username"]: r for r in rows}
    assert by_name[f"erin-{suffix}"]["role_code"] == "analyst"
    assert by_name[f"erin-{suffix}"]["email"] == f"erin-{suffix}@test.io"

    # 移除 erin → 立即失去本工作区权限
    resp = api.delete(f"/api/auth/workspaces/{ws_a}/members/{erin_uid}",
                      headers=_headers(admin_token, ws_a))
    assert resp.status_code == 200
    resp = api.get("/api/auth/me", headers=_headers(erin_token, ws_a))
    assert resp.status_code == 401
    assert _login(api, f"erin-{suffix}", "reg-pass-12345").status_code == 403


def test_case9_add_member_requires_registered_user(api, env):
    admin_token = _token(api, "admin", "admin123")
    resp = _add_member(api, admin_token, env["ws_a"], f"nobody-{env['suffix']}", "viewer")
    assert resp.status_code == 404


# ---- Case 10：末位管理员保护 ----

def test_case10_last_admin_protected(api, env):
    suffix = env["suffix"]
    admin_token = _token(api, "admin", "admin123")
    ws_a = env["ws_a"]
    admin_uid = 1  # 种子 admin

    # 唯一 admin 不能被降级 / 移除
    resp = api.patch(f"/api/auth/workspaces/{ws_a}/members/{admin_uid}",
                     headers=_headers(admin_token, ws_a), json={"role_code": "viewer"})
    assert resp.status_code == 400
    assert "最后一名管理员" in resp.json()["detail"]
    resp = api.delete(f"/api/auth/workspaces/{ws_a}/members/{admin_uid}",
                      headers=_headers(admin_token, ws_a))
    assert resp.status_code == 400

    # 第二个 admin 存在时可以正常操作（对另一工作区的 admin_b 不受影响）
    _register(api, suffix, "gina")
    resp = _add_member(api, admin_token, ws_a, f"gina-{suffix}", "admin")
    assert resp.status_code == 200
    gina_uid = resp.json()["user_id"]
    resp = api.patch(f"/api/auth/workspaces/{ws_a}/members/1",
                     headers=_headers(admin_token, ws_a), json={"role_code": "viewer"})
    assert resp.status_code == 200
    # 注意：admin 自降为 viewer 后本请求上下文立即失去 member:manage（权限实时解析），
    # 恢复操作必须由另一名 admin（gina）执行
    gina_token = _token(api, f"gina-{suffix}", "reg-pass-12345")
    resp = api.patch(f"/api/auth/workspaces/{ws_a}/members/1",
                     headers=_headers(gina_token, ws_a), json={"role_code": "admin"})
    assert resp.status_code == 200, resp.text
    resp = api.patch(f"/api/auth/workspaces/{ws_a}/members/{gina_uid}",
                     headers=_headers(admin_token, ws_a), json={"role_code": "viewer"})
    assert resp.status_code == 200, resp.text


# ---- Case 11：analyst / viewer 不能调用管理 API（403）----

def test_case11_non_admin_member_management_denied(api, env):
    suffix = env["suffix"]
    _register(api, suffix, "henry")
    admin_token = _token(api, "admin", "admin123")
    ws_a = env["ws_a"]
    _add_member(api, admin_token, ws_a, f"henry-{suffix}", "analyst")
    henry_token = _token(api, f"henry-{suffix}", "reg-pass-12345")
    h = _headers(henry_token, ws_a)

    assert api.get(f"/api/auth/workspaces/{ws_a}/members", headers=h).status_code == 403
    assert _add_member(api, henry_token, ws_a, f"frank-{suffix}", "admin").status_code == 403
    assert api.patch(f"/api/auth/workspaces/{ws_a}/members/1", headers=h,
                     json={"role_code": "viewer"}).status_code == 403
    assert api.delete(f"/api/auth/workspaces/{ws_a}/members/1", headers=h).status_code == 403
    # 列出平台用户（workspace:manage）同样拒绝
    assert api.get("/api/auth/users", headers=h).status_code == 403


# ---- Case 12：跨工作区参数篡改 ----

def test_case12_admin_cannot_manage_other_workspace(api, env, db_session):
    """跨工作区成员管理防护：
    - 非成员伪造 X-Workspace-Id → UserContext 解析直接 401；
    - 多工作区用户不带工作区头（ctx=默认工作区）改 URL 里的 ws id → 403。"""
    suffix = env["suffix"]
    admin_token = _token(api, "admin", "admin123")
    ws_b = env["ws_b"]

    # admin（仅 ws_a 成员）伪造 X-Workspace-Id: ws_b → 401（非成员，UserContext 解析拒绝）
    assert api.get(f"/api/auth/workspaces/{ws_b}/members",
                   headers=_headers(admin_token, ws_b)).status_code == 401
    assert _add_member(api, admin_token, ws_b, f"mallory-{suffix}", "admin").status_code == 401

    # leo：ws_a 是 admin，ws_b 只是 viewer。不带工作区头 → ctx=ws_a(admin)，
    # 但 URL 指向 ws_b → 同工作区校验拦截（防止拿 A 工区权限管 B 工区）
    from backend.models import User, WorkspaceMember
    _register(api, suffix, "leo")
    leo = db_session.query(User).filter(User.username == f"leo-{suffix}").first()
    db_session.add(WorkspaceMember(workspace_id=ws_b, user_id=leo.id, role_code="viewer"))
    db_session.add(WorkspaceMember(workspace_id=env["ws_a"], user_id=leo.id, role_code="admin"))
    db_session.commit()
    leo_token = _token(api, f"leo-{suffix}", "reg-pass-12345")
    h = {"Authorization": f"Bearer {leo_token}"}  # 故意不带 X-Workspace-Id
    assert api.get(f"/api/auth/workspaces/{ws_b}/members", headers=h).status_code == 403
    assert _add_member(api, leo_token, ws_b, f"mallory-{suffix}", "admin").status_code == 403

    # 非成员拿 X-Workspace-Id 指向未加入的工作区 → UserContext 解析失败
    henry_token = _token(api, f"henry-{suffix}", "reg-pass-12345")
    assert api.get("/api/auth/me",
                   headers=_headers(henry_token, ws_b)).status_code == 401


def test_case12_registered_user_isolated_from_other_workspaces(api, env):
    """注册用户不因注册获得任何工作区数据：未加入前业务 API 全部拒绝。"""
    suffix = env["suffix"]
    _register(api, suffix, "ivan")
    # 未加入工作区 → 登录被拒 → 更不可能访问任何工作区资源
    assert _login(api, f"ivan-{suffix}", "reg-pass-12345").status_code == 403


# ---- Case 13：仪表板工作区隔离 ----

def test_case13_dashboard_workspace_isolation(api, env):
    suffix = env["suffix"]
    admin_token = _token(api, "admin", "admin123")
    admin_b_token = _token(api, f"admin-b-{suffix}", "admin-b-pass-123")
    ws_a, ws_b = env["ws_a"], env["ws_b"]

    # ws_a 新建的仪表板归属 ws_a
    resp = api.post("/api/dashboards", headers=_headers(admin_token, ws_a),
                    json={"name": f"销售看板-{suffix}", "description": "ws_a 专属"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["workspace_id"] == ws_a
    did = resp.json()["id"]

    # 列表按工作区过滤：ws_b 看不到 ws_a 的新仪表板
    ids_b = [d["id"] for d in api.get("/api/dashboards",
                                      headers=_headers(admin_b_token, ws_b)).json()]
    assert did not in ids_b
    ids_a = [d["id"] for d in api.get("/api/dashboards",
                                      headers=_headers(admin_token, ws_a)).json()]
    assert did in ids_a

    # 跨工作区按 id 直取 / 改 / 删 → 404（不暴露存在性）
    assert api.get(f"/api/dashboards/{did}", headers=_headers(admin_b_token, ws_b)).status_code == 404
    assert api.patch(f"/api/dashboards/{did}", headers=_headers(admin_b_token, ws_b),
                     json={"name": "hacked"}).status_code == 404
    assert api.delete(f"/api/dashboards/{did}", headers=_headers(admin_b_token, ws_b)).status_code == 404
    assert api.post(f"/api/dashboards/{did}/items", headers=_headers(admin_b_token, ws_b),
                    json={"type": "text", "title": "x", "payload": {}}).status_code == 404

    # viewer 有 dashboard:read 可读，但没有写权限（专用 viewer 用户）
    _register(api, suffix, "kate")
    _add_member(api, admin_token, ws_a, f"kate-{suffix}", "viewer")
    kate_token = _token(api, f"kate-{suffix}", "reg-pass-12345")
    assert api.get(f"/api/dashboards/{did}", headers=_headers(kate_token, ws_a)).status_code == 200
    assert api.patch(f"/api/dashboards/{did}", headers=_headers(kate_token, ws_a),
                     json={"name": "nope"}).status_code == 403


# ---- Case 14：Viewer 只读 / Analyst 可分析（回归三角色语义）----

def test_case14_role_semantics_after_member_flow(api, env):
    suffix = env["suffix"]
    # frank 在 Case 9 中被升为 analyst：可以创建会话
    frank_token = _token(api, f"frank-{suffix}", "reg-pass-12345")
    h = _headers(frank_token, env["ws_a"])
    resp = api.post("/api/sessions", headers=h, json={"title": "角色语义"})
    assert resp.status_code == 200

    # kate 在 Case 13 中已加入为 viewer：只读
    kate_token = _token(api, f"kate-{suffix}", "reg-pass-12345")
    kh = _headers(kate_token, env["ws_a"])
    assert api.post("/api/sessions", headers=kh, json={"title": "x"}).status_code == 403
    assert api.get("/api/datasources", headers=kh).status_code == 200
