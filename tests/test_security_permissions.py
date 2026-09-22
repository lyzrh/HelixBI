"""授权（Authorization）安全测试：细粒度权限门 / 工作区隔离 / 越权矩阵。

覆盖需求点名的授权用例：
viewer 越权、analyst 越权、admin 正常、workspace A 访问 workspace B、直接调 API 绕过 UI、
无 token、token 篡改；以及本轮新加的权限点（analysis:read / usage:read / settings:write）
与"权限判定不依赖 token 内容"。
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
    db_dir = tmp_path_factory.mktemp("sec_authz_db")
    os.environ["APP_DB_PATH"] = str(db_dir / "test_app.db")
    os.environ.setdefault("HELIX_SECRET_KEY", "authz-test-key")

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
    """A 区（admin_a / ana_a / vie_a）+ B 区（admin_b），外加一条 A 区的运行与数据源。"""
    from backend.auth.security import hash_password
    from backend.models import (
        DataSource, Run, Session as DbSession, User, Workspace, WorkspaceMember,
    )

    suffix = uuid.uuid4().hex[:6]
    ws_a = Workspace(name=f"Authz-A-{suffix}")
    ws_b = Workspace(name=f"Authz-B-{suffix}")
    db_session.add_all([ws_a, ws_b])
    db_session.flush()
    people = {}
    for ws, role, name in ((ws_a, "admin", "adma"), (ws_a, "analyst", "anaa"),
                           (ws_a, "viewer", "viea"), (ws_b, "admin", "admb")):
        u = User(username=f"{name}-{suffix}", display_name=name,
                 password_hash=hash_password("authz-pass-12345"))
        db_session.add(u)
        db_session.flush()
        db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role_code=role))
        people[name] = u

    sess_a = DbSession(title=f"sa-{suffix}", workspace_id=ws_a.id)
    sess_b = DbSession(title=f"sb-{suffix}", workspace_id=ws_b.id)
    db_session.add_all([sess_a, sess_b])
    db_session.flush()
    run_a = Run(session_id=sess_a.id, question="qa", status="done", ok=True,
                answer="A 区结论", run_dir="")
    run_b = Run(session_id=sess_b.id, question="qb", status="done", ok=True,
                answer="B 区结论", run_dir="")
    ds_a = DataSource(name=f"dsa-{suffix}", type="file", file_path="/tmp/none.csv",
                      workspace_id=ws_a.id)
    ds_b = DataSource(name=f"dsb-{suffix}", type="file", file_path="/tmp/none2.csv",
                      workspace_id=ws_b.id)
    db_session.add_all([run_a, run_b, ds_a, ds_b])
    db_session.commit()
    return {"suffix": suffix, "ws_a": ws_a.id, "ws_b": ws_b.id, **people,
            "run_a": run_a, "run_b": run_b, "ds_a": ds_a, "ds_b": ds_b, "sess_b": sess_b}


def _tok(api, username, password="authz-pass-12345"):
    r = api.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(token, ws=None):
    headers = {"Authorization": f"Bearer {token}"}
    if ws:
        headers["X-Workspace-Id"] = str(ws)
    return headers


# ---- 登录门 / 匿名 / 篡改 ----

@pytest.mark.parametrize("path", [
    "/api/datasources", "/api/sessions", "/api/skills", "/api/dashboards",
    "/api/agents", "/api/usage/summary", "/api/settings/llm", "/api/auth/sessions",
    "/api/auth/permissions", "/api/auth/roles", "/api/auth/audit",
])
def test_anonymous_is_401_everywhere(api, path):
    assert api.get(path).status_code == 401


def test_tampered_token_is_401(api, env):
    token = _tok(api, f"viea-{env['suffix']}")
    assert api.get("/api/datasources",
                   headers=_h(token[:-2] + "zz", env["ws_a"])).status_code == 401


# ---- 本轮新增权限门：正例 + 反例 ----

def test_role_catalog_is_admin_only(api, env):
    """/auth/permissions 与 /auth/roles 原先匿名可枚举，现在只有管理员能读。"""
    admin = _tok(api, f"adma-{env['suffix']}")
    ana = _tok(api, f"anaa-{env['suffix']}")
    assert api.get("/api/auth/permissions", headers=_h(admin, env["ws_a"])).status_code == 200
    assert api.get("/api/auth/roles", headers=_h(admin, env["ws_a"])).status_code == 200
    assert api.get("/api/auth/permissions", headers=_h(ana, env["ws_a"])).status_code == 403
    assert api.get("/api/auth/roles", headers=_h(ana, env["ws_a"])).status_code == 403


def test_llm_settings_read_is_graded_and_key_never_leaks(api, env, monkeypatch):
    """LLM 配置：登录即可看到模型名，但 base_url 只给有 settings:write 的人，密钥永远掩码。"""
    from backend import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-secret-abcdefghijklmn")
    ana = _tok(api, f"anaa-{env['suffix']}")
    admin = _tok(api, f"adma-{env['suffix']}")

    ana_view = api.get("/api/settings/llm", headers=_h(ana, env["ws_a"]))
    assert ana_view.status_code == 200
    assert "model" in ana_view.json() and ana_view.json()["can_edit"] is False
    assert "base_url" not in ana_view.json()
    assert "sk-secret-abcdefghijklmn" not in ana_view.text

    admin_view = api.get("/api/settings/llm", headers=_h(admin, env["ws_a"])).json()
    assert admin_view["can_edit"] is True and admin_view["base_url"]
    assert "abcdefghijklmn" not in admin_view["api_key_masked"]


def test_llm_settings_write_is_admin_only(api, env):
    body = {"model": "deepseek-chat"}
    ana = _tok(api, f"anaa-{env['suffix']}")
    vie = _tok(api, f"viea-{env['suffix']}")
    admin = _tok(api, f"adma-{env['suffix']}")
    assert api.put("/api/settings/llm", headers=_h(ana, env["ws_a"]), json=body).status_code == 403
    assert api.put("/api/settings/llm", headers=_h(vie, env["ws_a"]), json=body).status_code == 403
    assert api.post("/api/settings/llm/test", headers=_h(ana, env["ws_a"]),
                    json={}).status_code == 403
    assert api.put("/api/settings/llm", headers=_h(admin, env["ws_a"]),
                   json=body).status_code == 200


def test_usage_is_admin_only_and_workspace_scoped(api, env, db_session):
    """用量/成本是平台级信息：viewer/analyst 403；admin 只看本工作区。"""
    from backend.models import TokenUsage

    db_session.add_all([
        TokenUsage(run_id=env["run_a"].id, session_id=env["sess_b"].id, node="summarize",
                   input_tokens=100, output_tokens=10),
    ])
    db_session.commit()

    vie = _tok(api, f"viea-{env['suffix']}")
    ana = _tok(api, f"anaa-{env['suffix']}")
    adm_b = _tok(api, f"admb-{env['suffix']}")

    assert api.get("/api/usage/summary", headers=_h(vie, env["ws_a"])).status_code == 403
    assert api.get("/api/usage/history", headers=_h(ana, env["ws_a"])).status_code == 403

    body = api.get("/api/usage/summary", headers=_h(adm_b, env["ws_b"])).json()
    assert body["workspace_id"] == env["ws_b"]

    items = api.get("/api/usage/history", headers=_h(adm_b, env["ws_b"])).json()["items"]
    # 本次查询是 B 区的会话 → 可见；A 区的用量不会出现在 B 区视角
    assert all(item["session_id"] in (env["sess_b"].id, None) for item in items)


def test_viewer_can_read_analysis_but_not_execute(api, env):
    """新权限点 analysis:read 的意图：viewer 能看结论/产物，但不能跑分析。"""
    vie = _tok(api, f"viea-{env['suffix']}")
    assert api.get(f"/api/runs/{env['run_a'].id}",
                   headers=_h(vie, env["ws_a"])).status_code == 200
    assert api.get(f"/api/runs/{env['run_a'].id}/export",
                   headers=_h(vie, env["ws_a"])).status_code == 200
    assert api.post(f"/api/sessions/{env['run_a'].session_id}/analyze",
                    headers=_h(vie, env["ws_a"]),
                    json={"question": "q", "data_source_ids": [env["ds_a"].id]}
                    ).status_code == 403


def test_runs_and_sessions_need_analysis_read(api, env, db_session):
    """把 analysis:read 从 viewer 拿掉 → 立即 403（证明判定来自 DB，不是 token 缓存）。"""
    from backend.models import RolePermission

    row = (db_session.query(RolePermission)
           .filter(RolePermission.role_code == "viewer",
                   RolePermission.permission_code == "analysis:read").first())
    assert row is not None, "种子数据里 viewer 应带 analysis:read"
    db_session.delete(row)
    db_session.commit()
    try:
        vie = _tok(api, f"viea-{env['suffix']}")
        assert api.get("/api/sessions", headers=_h(vie, env["ws_a"])).status_code == 403
        assert api.get(f"/api/runs/{env['run_a'].id}",
                       headers=_h(vie, env["ws_a"])).status_code == 403
    finally:
        db_session.add(RolePermission(role_code="viewer", permission_code="analysis:read"))
        db_session.commit()


# ---- 工作区隔离：A 区用户拿 B 区 id ----

@pytest.mark.parametrize("path_tmpl", [
    "/api/runs/{run_b}", "/api/runs/{run_b}/export",
    "/api/usage/sessions/{run_b}/tokens",
])
def test_cross_workspace_run_access_is_denied(api, env, path_tmpl):
    token = _tok(api, f"adma-{env['suffix']}")   # A 区 admin（权限齐全，只输在工作区）
    path = path_tmpl.format(run_b=env["run_b"].id)
    assert api.get(path, headers=_h(token, env["ws_a"])).status_code == 404


def test_cross_workspace_session_access_is_denied(api, env):
    token = _tok(api, f"adma-{env['suffix']}")
    assert api.get(f"/api/sessions/{env['sess_b'].id}",
                   headers=_h(token, env["ws_a"])).status_code == 403


def test_cross_workspace_datasource_explore_is_denied(api, env):
    """自助分析读数据：跨工作区猜 id 必须被拒（本轮补齐的缺口）。"""
    token = _tok(api, f"adma-{env['suffix']}")
    for path in (f"/api/explore/schema?data_source_id={env['ds_b'].id}",
                 f"/api/explore/profile?data_source_id={env['ds_b'].id}"):
        assert api.get(path, headers=_h(token, env["ws_a"])).status_code == 404
    assert api.post("/api/explore/sql", headers=_h(token, env["ws_a"]),
                    json={"data_source_id": env["ds_b"].id, "sql": "SELECT 1"}
                    ).status_code == 404
    # 本工作区的数据源正常
    assert api.get(f"/api/datasources/{env['ds_a'].id}",
                   headers=_h(token, env["ws_a"])).status_code == 200


def test_cross_workspace_skill_read_is_denied(api, env, db_session):
    """Skill 按 id 读取原先没有作用域校验（猜到 id 就能读别人代码）。"""
    from backend.models import Skill

    skill_b = Skill(name=f"skb-{env['suffix']}", question="qb", code="print(1)",
                    scope="workspace", workspace_id=env["ws_b"], user_id=env["admb"].id)
    other_user_skill = Skill(name=f"sku-{env['suffix']}", question="qu", code="print(2)",
                             scope="user", workspace_id=env["ws_a"],
                             user_id=env["admb"].id)
    db_session.add_all([skill_b, other_user_skill])
    db_session.commit()

    token = _tok(api, f"adma-{env['suffix']}")
    assert api.get(f"/api/skills/{skill_b.id}",
                   headers=_h(token, env["ws_a"])).status_code == 404
    # user 作用域只看本人：同工作区但不是他的，也不可见
    assert api.get(f"/api/skills/{other_user_skill.id}",
                   headers=_h(token, env["ws_a"])).status_code == 404


def test_skill_capture_from_foreign_run_is_denied(api, env):
    token = _tok(api, f"adma-{env['suffix']}")
    resp = api.post("/api/skills/from-run", headers=_h(token, env["ws_a"]),
                    json={"run_id": env["run_b"].id, "name": "偷来的技能"})
    assert resp.status_code == 404


def test_forged_workspace_header_is_rejected(api, env):
    """伪造 X-Workspace-Id 指向自己不属于的工作区 → 401（UserContext 建不起来）。"""
    token = _tok(api, f"adma-{env['suffix']}")
    resp = api.get("/api/datasources", headers=_h(token, env["ws_b"]))
    assert resp.status_code == 401


def test_workspace_denial_is_audited(api, env):
    admin = _tok(api, f"adma-{env['suffix']}")
    api.get("/api/datasources", headers=_h(admin, env["ws_b"]))
    items = api.get("/api/auth/audit?event=authz&limit=50",
                    headers=_h(admin, env["ws_a"])).json()["items"]
    assert any(e["event"] == "authz.workspace_denied" for e in items), items[:3]


def test_permission_denial_is_audited(api, env):
    vie = _tok(api, f"viea-{env['suffix']}")
    api.post("/api/explore/sql", headers=_h(vie, env["ws_a"]),
             json={"data_source_id": env["ds_a"].id, "sql": "SELECT 1"})
    admin = _tok(api, f"adma-{env['suffix']}")
    items = api.get("/api/auth/audit?event=authz.permission_denied&limit=50",
                    headers=_h(admin, env["ws_a"])).json()["items"]
    assert any(e["event"] == "authz.permission_denied" for e in items)


# ---- 个人设置按用户隔离（原先全局 KV，会串到别人的分析 prompt）----

def test_preferences_are_per_user(api, env):
    ana = _tok(api, f"anaa-{env['suffix']}")
    vie = _tok(api, f"viea-{env['suffix']}")

    resp = api.put("/api/settings/preferences", headers=_h(ana, env["ws_a"]),
                   json={"answer_style": "concise", "custom_instructions": "只输出结论"})
    assert resp.status_code == 200

    # viewer 读到的仍是默认值（不会被 analyst 的设置污染）
    vie_prefs = api.get("/api/settings/preferences", headers=_h(vie, env["ws_a"])).json()
    assert vie_prefs["custom_instructions"] == ""
    assert vie_prefs["answer_style"] == "standard"

    # 各自的 profile 也互不可见
    api.put("/api/settings/profile", headers=_h(ana, env["ws_a"]),
            json={"nickname": "分析小助手"})
    assert api.get("/api/settings/profile",
                   headers=_h(vie, env["ws_a"])).json()["nickname"] != "分析小助手"


def test_temperature_is_platform_level_and_admin_only(api, env):
    """创意度影响所有人的分析，普通成员不能静默改（需要 settings:write）。"""
    from backend import config

    ana = _tok(api, f"anaa-{env['suffix']}")
    admin = _tok(api, f"adma-{env['suffix']}")
    before = config.LLM_TEMPERATURE

    resp = api.put("/api/settings/preferences", headers=_h(ana, env["ws_a"]),
                   json={"temperature": 0.9})
    assert resp.status_code == 403
    assert config.LLM_TEMPERATURE == before

    ok = api.put("/api/settings/preferences", headers=_h(admin, env["ws_a"]),
                 json={"temperature": 0.5})
    assert ok.status_code == 200 and config.LLM_TEMPERATURE == 0.5
    try:
        assert config.LLM_TEMPERATURE == 0.5
    finally:
        config.LLM_TEMPERATURE = before
        from backend.agent.graph import reset_llm

        reset_llm()


def test_preferences_write_does_not_leak_into_graph_cache(api, env):
    """偏好写入后清缓存 → 下一轮分析按新偏好（同时验证按用户缓存没有串号）。"""
    from backend.agent.graph import _get_prefs, preference_kv_key

    ana = _tok(api, f"anaa-{env['suffix']}")
    api.put("/api/settings/preferences", headers=_h(ana, env["ws_a"]),
            json={"answer_style": "detailed"})
    assert preference_kv_key(env["anaa"].id).endswith(str(env["anaa"].id))
    assert _get_prefs(env["anaa"].id)["answer_style"] == "detailed"
    # 另一个用户不受影响
    assert _get_prefs(env["viea"].id)["answer_style"] in ("standard", "concise")
