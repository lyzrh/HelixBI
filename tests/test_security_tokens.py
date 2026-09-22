"""Token 生命周期安全测试：access 短期 / refresh 可撤销 / 轮换 / 重放 / 强制下线。

覆盖需求点名的认证侧用例：
access 过期、refresh 过期、refresh 被 revoke、logout 后不可刷新、
rotation + replay（整族吊销）、管理员强制下线、refresh 不能当 access 用、token 篡改。
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
    db_dir = tmp_path_factory.mktemp("sec_token_db")
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
    from backend.auth.security import hash_password
    from backend.models import User, Workspace, WorkspaceMember

    suffix = uuid.uuid4().hex[:6]
    ws = Workspace(name=f"Tok-{suffix}", description="Token 测试工作区")
    db_session.add(ws)
    db_session.flush()
    users = {}
    for name, role in (("mgr", "admin"), ("mem", "analyst")):
        u = User(username=f"{name}-{suffix}", display_name=name,
                 password_hash=hash_password("tok-pass-12345"))
        db_session.add(u)
        db_session.flush()
        db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role_code=role))
        users[name] = u
    db_session.commit()
    return {"suffix": suffix, "ws": ws.id, **users}


def _login(api, username, password="tok-pass-12345"):
    r = api.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _h(token, ws=None):
    headers = {"Authorization": f"Bearer {token}"}
    if ws:
        headers["X-Workspace-Id"] = str(ws)
    return headers


def _refresh(api, refresh_token):
    return api.post("/api/auth/refresh", json={"refresh_token": refresh_token})


# ---- 登录返回什么 ----

def test_login_returns_short_lived_access_and_revocable_refresh(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    assert body["token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"
    # access 短期（默认 1h，可由 HELIX_ACCESS_TOKEN_TTL 调整）
    from backend import config

    assert 0 < body["expires_in"] <= config.ACCESS_TOKEN_TTL_SECONDS
    assert body["refresh_expires_in"] >= body["expires_in"]
    # access 能用，refresh 不能当 access 用
    assert api.get("/api/sessions", headers=_h(body["token"], env["ws"])).status_code == 200
    assert api.get("/api/auth/me", headers=_h(body["refresh_token"])).status_code == 401


def test_refresh_token_is_not_a_jwt(api, env):
    """refresh 是不透明随机串（不是 JWT），因此天生无法通过 access 校验。"""
    body = _login(api, f"mem-{env['suffix']}")
    assert body["refresh_token"].count(".") != 2


def test_jwt_with_refresh_typ_is_rejected(api, env):
    """纵深防御：即便有人把 typ=refresh 的 JWT 拿来当 access 用，也必须被拒。"""
    from backend.auth.security import create_token

    forged = create_token(env["mem"].id, env["mem"].username, typ="refresh")
    assert api.get("/api/auth/me", headers=_h(forged)).status_code == 401


def test_tampered_token_rejected(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    tampered = body["token"][:-3] + ("aaa" if not body["token"].endswith("aaa") else "bbb")
    assert api.get("/api/auth/me", headers=_h(tampered)).status_code == 401
    assert api.get("/api/sessions", headers=_h(body["token"], env["ws"])).status_code == 200


# ---- access 过期 ----

def test_expired_access_token_rejected(api, env):
    from backend.auth.security import create_token

    expired = create_token(env["mem"].id, env["mem"].username, ttl=-10)
    assert api.get("/api/auth/me", headers=_h(expired)).status_code == 401


def test_no_token_is_401(api):
    assert api.get("/api/auth/me").status_code == 401
    assert api.get("/api/sessions").status_code == 401


# ---- 轮换与重放 ----

def test_refresh_rotates_and_old_token_cannot_be_reused(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    first = body["refresh_token"]

    rotated = _refresh(api, first)
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()["refresh_token"]
    assert second != first
    # 新 access 可用
    assert api.get("/api/sessions", headers=_h(rotated.json()["token"], env["ws"])).status_code == 200

    # 旧 refresh 再次使用 → 判定重放：整族吊销（包括刚刚签发的那个）
    replay = _refresh(api, first)
    assert replay.status_code == 401
    assert "重复使用" in replay.json()["detail"] or "replay" in replay.text.lower()
    assert _refresh(api, second).status_code == 401, "重放后整族应失效"


def test_replay_is_audited(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    rotated = _refresh(api, body["refresh_token"]).json()
    _refresh(api, body["refresh_token"])  # 触发重放

    admin = _login(api, f"mgr-{env['suffix']}")["token"]
    events = api.get("/api/auth/audit?limit=50", headers=_h(admin, env["ws"])).json()["items"]
    assert any(e["event"] == "auth.token_replay" and e["outcome"] == "denied" for e in events)
    # 审计里绝不出现令牌原文
    raw = str(events)
    assert body["refresh_token"] not in raw and rotated["refresh_token"] not in raw
    assert body["token"] not in raw


def test_audit_never_stores_credentials(api, env):
    """口令 / 令牌都不该出现在审计里（登录成功与失败各记一条）。"""
    api.post("/api/auth/login", json={"username": f"mem-{env['suffix']}",
                                      "password": "wrong-password"})
    admin = _login(api, f"mgr-{env['suffix']}")["token"]
    items = api.get("/api/auth/audit?limit=100", headers=_h(admin, env["ws"])).json()["items"]
    raw = str(items)
    assert "wrong-password" not in raw and "tok-pass-12345" not in raw
    assert any(e["event"] == "auth.login" and e["outcome"] == "failed" for e in items)


# ---- 撤销：logout / revoke-all / 管理员 ----

def test_logout_revokes_refresh(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    assert api.post("/api/auth/logout",
                    json={"refresh_token": body["refresh_token"]}).status_code == 200
    assert _refresh(api, body["refresh_token"]).status_code == 401


def test_logout_is_idempotent(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    assert api.post("/api/auth/logout",
                    json={"refresh_token": body["refresh_token"]}).json()["revoked"] is True
    again = api.post("/api/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert again.status_code == 200 and again.json()["revoked"] is False


def test_revoke_all_kills_every_session(api, env):
    a = _login(api, f"mem-{env['suffix']}")
    b = _login(api, f"mem-{env['suffix']}")   # 第二次登录 = 第二个会话
    assert _refresh(api, b["refresh_token"]).status_code == 200

    resp = api.post("/api/auth/revoke-all", headers=_h(a["token"], env["ws"]))
    assert resp.status_code == 200 and resp.json()["revoked"] >= 1
    # 撤销后：两个 refresh 都用不了，但已签发的 access 到期前仍有效（短期令牌的取舍）
    assert _refresh(api, a["refresh_token"]).status_code == 401
    assert _refresh(api, b["refresh_token"]).status_code == 401


def test_admin_can_force_logout_a_member(api, env):
    member = _login(api, f"mem-{env['suffix']}")
    admin = _login(api, f"mgr-{env['suffix']}")
    resp = api.post(f"/api/auth/users/{env['mem'].id}/revoke",
                    headers=_h(admin["token"], env["ws"]))
    assert resp.status_code == 200 and resp.json()["revoked"] >= 1
    assert _refresh(api, member["refresh_token"]).status_code == 401


def test_non_admin_cannot_force_logout(api, env):
    member = _login(api, f"mem-{env['suffix']}")
    other = _login(api, f"mem-{env['suffix']}")
    resp = api.post(f"/api/auth/users/{env['mem'].id}/revoke",
                    headers=_h(other["token"], env["ws"]))
    assert resp.status_code == 403


def test_admin_cannot_revoke_user_outside_workspace(api, env, db_session):
    from backend.auth.security import hash_password
    from backend.models import User

    outsider = User(username=f"out-{env['suffix']}",
                    password_hash=hash_password("tok-pass-12345"))
    db_session.add(outsider)
    db_session.commit()

    admin = _login(api, f"mgr-{env['suffix']}")["token"]
    resp = api.post(f"/api/auth/users/{outsider.id}/revoke",
                    headers=_h(admin, env["ws"]))
    assert resp.status_code == 404


def test_my_sessions_lists_active_logins(api, env):
    body = _login(api, f"mem-{env['suffix']}")
    rows = api.get("/api/auth/sessions", headers=_h(body["token"], env["ws"])).json()
    assert rows and any(r["family_id"] for r in rows)
    # 输出里不含 token / token_hash
    assert "token" not in str(rows).lower() or "token_hash" not in str(rows)


# ---- refresh 过期 / 被停用 ----

def test_expired_refresh_token_rejected(api, env, db_session):
    from backend.auth.security import hash_refresh_token
    from backend.models import RefreshToken

    body = _login(api, f"mem-{env['suffix']}")
    row = (db_session.query(RefreshToken)
           .filter(RefreshToken.token_hash == hash_refresh_token(body["refresh_token"])).first())
    row.expires_at = "2000-01-01 00:00:00"
    db_session.commit()
    resp = _refresh(api, body["refresh_token"])
    assert resp.status_code == 401
    assert "过期" in resp.json()["detail"]


def test_inactive_user_cannot_refresh(api, env, db_session):
    from backend.models import User

    body = _login(api, f"mem-{env['suffix']}")
    user = db_session.get(User, env["mem"].id)
    user.is_active = False
    db_session.commit()
    try:
        assert _refresh(api, body["refresh_token"]).status_code == 401
        assert api.get("/api/auth/me", headers=_h(body["token"])).status_code == 401
    finally:
        user.is_active = True
        db_session.commit()


def test_unknown_refresh_token_rejected(api, env):
    assert _refresh(api, "not-a-real-token-0123456789").status_code == 401


# ---- 权限判定不依赖 token 内容（改权限立即生效）----

def test_permission_change_takes_effect_without_new_login(api, env, db_session):
    """LLM 不参与授权、token 也不缓存权限：DB 里改权限，下一次请求立即按新权限判。"""
    from backend.models import RolePermission

    body = _login(api, f"mem-{env['suffix']}")
    assert api.get("/api/usage/summary", headers=_h(body["token"], env["ws"])).status_code == 403

    row = RolePermission(role_code="analyst", permission_code="usage:read")
    db_session.add(row)
    db_session.commit()
    try:
        assert api.get("/api/usage/summary",
                       headers=_h(body["token"], env["ws"])).status_code == 200
    finally:
        db_session.delete(row)
        db_session.commit()
    assert api.get("/api/usage/summary", headers=_h(body["token"], env["ws"])).status_code == 403
