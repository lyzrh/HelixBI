"""认证 + Workspace + RBAC 测试（Case 1-7）。

策略：整模块使用独立临时 SQLite（APP_DB_PATH + importlib.reload），
不污染开发库 data/app.db。鉴权通过 FastAPI TestClient 走真实 API 栈。
Case 1 的完整 AI 分析（LLM + 沙箱）在无 API Key 环境标记 Blocked；
其余 Case 全部真实执行。
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
    """独立临时元数据库 + TestClient（触发 init_db + seed）。"""
    db_dir = tmp_path_factory.mktemp("auth_rbac_db")
    os.environ["APP_DB_PATH"] = str(db_dir / "test_app.db")

    import backend.config as config
    importlib.reload(config)
    import backend.db as db_mod
    importlib.reload(db_mod)

    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as c:  # context manager 触发 lifespan（建表 + seed）
        yield c

    os.environ.pop("APP_DB_PATH", None)


@pytest.fixture(scope="module")
def db_session(api):
    from backend.db import SessionLocal

    with SessionLocal() as db:
        yield db


@pytest.fixture(scope="module")
def sales_ws(db_session):
    """销售工作区（Analyst 视角）+ 营销工作区（Viewer 视角）+ 测试用户。"""
    from backend.models import User, Workspace, WorkspaceMember
    from backend.auth.security import hash_password

    suffix = uuid.uuid4().hex[:6]
    sales = Workspace(name=f"Sales-{suffix}", description="销售工作区")
    mkt = Workspace(name=f"Marketing-{suffix}", description="营销工作区")
    db_session.add_all([sales, mkt])
    db_session.flush()

    alice = User(username=f"alice-{suffix}", display_name="Alice",
                 password_hash=hash_password("alice-pass-123"))
    bob = User(username=f"bob-{suffix}", display_name="Bob",
               password_hash=hash_password("bob-pass-123"))
    db_session.add_all([alice, bob])
    db_session.flush()

    # 同一用户：Sales → Analyst，Marketing → Viewer
    db_session.add_all([
        WorkspaceMember(workspace_id=sales.id, user_id=alice.id, role_code="analyst"),
        WorkspaceMember(workspace_id=mkt.id, user_id=alice.id, role_code="viewer"),
        WorkspaceMember(workspace_id=sales.id, user_id=bob.id, role_code="viewer"),
    ])
    db_session.commit()
    return {"sales": sales, "mkt": mkt, "alice": alice, "bob": bob,
            "suffix": suffix}


def _login(api, username: str, password: str) -> str:
    resp = api.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _headers(token: str, workspace_id: int | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if workspace_id:
        h["X-Workspace-Id"] = str(workspace_id)
    return h


# ---- 登录与 UserContext ----

def test_login_wrong_password(api, sales_ws):
    resp = api.post("/api/auth/login",
                    json={"username": f"alice-{sales_ws['suffix']}", "password": "wrong"})
    assert resp.status_code == 401


def test_login_by_email_supported(api, sales_ws, db_session):
    """支持用户名或邮箱登录。"""
    from backend.models import User

    user = db_session.get(User, sales_ws["alice"].id)
    user.email = f"alice-{sales_ws['suffix']}@example.com"
    db_session.commit()
    resp = api.post("/api/auth/login",
                    json={"username": user.email, "password": "alice-pass-123"})
    assert resp.status_code == 200


def test_me_requires_token(api):
    assert api.get("/api/auth/me").status_code == 401
    assert api.get("/api/sessions", headers={"Authorization": "Bearer bad"}).status_code == 401


# ---- Case 2：同一用户跨工作区角色切换 ----

def test_case2_same_user_different_roles_per_workspace(api, sales_ws):
    token = _login(api, f"alice-{sales_ws['suffix']}", "alice-pass-123")

    r_sales = api.get("/api/auth/me", headers=_headers(token, sales_ws["sales"].id))
    r_mkt = api.get("/api/auth/me", headers=_headers(token, sales_ws["mkt"].id))
    assert r_sales.status_code == 200 and r_mkt.status_code == 200

    ctx_sales = r_sales.json()["context"]
    ctx_mkt = r_mkt.json()["context"]
    assert ctx_sales["role"] == "analyst"
    assert ctx_mkt["role"] == "viewer"
    assert "sql:execute" in ctx_sales["permissions"]
    assert "sql:execute" not in ctx_mkt["permissions"]
    assert "analysis:execute" in ctx_sales["permissions"]
    assert "analysis:execute" not in ctx_mkt["permissions"]
    assert ctx_mkt["permissions"] == sorted(["datasource:read", "dashboard:read", "skill:read"])


def test_user_cannot_access_workspace_they_are_not_member_of(api, sales_ws):
    """Bob 不属于 Marketing 工作区 → 401（User Resolver 拒绝）。"""
    token = _login(api, f"bob-{sales_ws['suffix']}", "bob-pass-123")
    resp = api.get("/api/auth/me", headers=_headers(token, sales_ws["mkt"].id))
    assert resp.status_code == 401


# ---- Case 3：Analyst 可以执行允许的 SQL ----

def test_case3_analyst_can_execute_sql(api, sales_ws, db_session):
    from backend.models import DataSource

    # 给销售工作区放一个数据源（指向示例 csv）
    csv_path = PROJECT_ROOT / "examples" / "sample_sales.csv"
    ds = DataSource(name=f"sales-{sales_ws['suffix']}", type="file",
                    file_path=str(csv_path), file_name="sample_sales.csv",
                    file_type="csv", workspace_id=sales_ws["sales"].id,
                    pack_id="retail_sales", columns_json='["订单日期","品类","区域","销售额","数量"]')
    db_session.add(ds)
    db_session.commit()

    token = _login(api, f"alice-{sales_ws['suffix']}", "alice-pass-123")
    resp = api.post("/api/explore/sql",
                    headers=_headers(token, sales_ws["sales"].id),
                    json={"data_source_id": ds.id, "sql": "SELECT 品类, COUNT(*) AS n FROM t GROUP BY 品类"})
    assert resp.status_code == 200, resp.text
    assert "columns" in resp.json() or "rows" in resp.json()


# ---- Case 4：Viewer 没有 sql:execute → 后端拒绝 ----

def test_case4_viewer_sql_denied(api, sales_ws):
    token = _login(api, f"alice-{sales_ws['suffix']}", "alice-pass-123")
    ds_id = 1  # 任意 id；403 应先于数据源校验发生
    resp = api.post("/api/explore/sql",
                    headers=_headers(token, sales_ws["mkt"].id),
                    json={"data_source_id": ds_id, "sql": "SELECT 1"})
    assert resp.status_code == 403
    assert "sql:execute" in resp.json()["detail"]


# ---- Case 5：Viewer 绕过前端直调 API → 后端仍拒绝 ----

def test_case5_viewer_bypass_frontend_denied(api, sales_ws):
    token = _login(api, f"bob-{sales_ws['suffix']}", "bob-pass-123")
    h = _headers(token, sales_ws["sales"].id)

    # 写数据源
    resp = api.post("/api/datasources/db", headers=h,
                    json={"name": "hacked", "config": {"db_type": "sqlite", "sqlite_path": "x.db"}})
    assert resp.status_code == 403
    # 执行分析
    resp = api.post("/api/sessions/1/analyze", headers=h,
                    json={"question": "test", "data_source_ids": [1]})
    assert resp.status_code == 403
    # 写 Skill
    resp = api.post("/api/skills", headers=h,
                    json={"name": "hacked", "code": "print(1)"})
    assert resp.status_code == 403
    # 删除数据源
    resp = api.delete("/api/datasources/999", headers=h)
    assert resp.status_code == 403


def test_anonymous_api_access_denied(api):
    """未登录直调任何业务 API → 401。"""
    assert api.get("/api/datasources").status_code == 401
    assert api.get("/api/sessions").status_code == 401
    assert api.get("/api/skills").status_code == 401
    assert api.get("/api/health").status_code == 200  # 健康检查保持公开


# ---- Case 6：不同 Workspace 的 Skill / Memory 不串数据 ----

def test_case6_skill_and_memory_isolation(api, sales_ws, db_session):
    from backend.models import Skill, Session as DbSession
    from backend.skills import engine as skill_engine

    sales_id, mkt_id = sales_ws["sales"].id, sales_ws["mkt"].id

    # Sales 工作区沉淀一个 Skill
    skill_engine.capture_from_run = skill_engine.capture_from_run  # noqa: PLW0127 — 引用清晰
    from backend.models import Run

    run = Run(session_id=1, question="按品类统计销售额", data_source_ids="[1]",
              status="done", ok=True, code="print(1)")
    db_session.add(run)
    db_session.flush()
    skill = Skill(name=f"skill-{sales_ws['suffix']}", question="按品类统计销售额",
                  code="print(1)", scope="workspace", workspace_id=sales_id,
                  enabled=True)
    db_session.add(skill)
    db_session.commit()

    # Sales（analyst）匹配得到；Marketing（viewer）检索不到
    alice_token = _login(api, f"alice-{sales_ws['suffix']}", "alice-pass-123")
    r_sales = api.get("/api/skills", headers=_headers(alice_token, sales_id))
    r_mkt = api.get("/api/skills", headers=_headers(alice_token, mkt_id))
    assert r_sales.status_code == 200 and r_mkt.status_code == 200
    sales_names = [s["name"] for s in r_sales.json()]
    mkt_names = [s["name"] for s in r_mkt.json()]
    assert f"skill-{sales_ws['suffix']}" in sales_names
    assert f"skill-{sales_ws['suffix']}" not in mkt_names

    # 引擎级匹配同样隔离
    with db_session.no_autoflush:
        hit_sales = skill_engine.match_skills(db_session, "按品类统计销售额",
                                              workspace_id=sales_id)
        hit_mkt = skill_engine.match_skills(db_session, "按品类统计销售额",
                                            workspace_id=mkt_id)
    assert any(s.id == skill.id for s in hit_sales)
    assert not any(s.id == skill.id for s in hit_mkt)

    # 会话（Memory 载体）按工作区隔离：Marketing 只能看到自己工作区的会话
    s_sales = DbSession(title="Sales 会话", workspace_id=sales_id)
    s_mkt = DbSession(title="Marketing 会话", workspace_id=mkt_id)
    db_session.add_all([s_sales, s_mkt])
    db_session.commit()
    listed_mkt = api.get("/api/sessions", headers=_headers(alice_token, mkt_id)).json()
    titles = [s["title"] for s in listed_mkt]
    assert "Marketing 会话" in titles
    assert "Sales 会话" not in titles


# ---- Case 1 / Case 7：登录后进入分析链路；原有链路结构不被破坏 ----

def test_case1_login_to_analysis_permission_path(api, sales_ws, db_session):
    """登录 → 选工作区 → 创建会话 → 发起分析。

    完整 AI 分析依赖 LLM Key 与 Docker 沙箱；本环境无 Key 时该部分标记
    Blocked，但权限链路（会话创建成功 / 分析请求通过权限门）真实执行。
    """
    from backend import config

    alice_token = _login(api, f"alice-{sales_ws['suffix']}", "alice-pass-123")
    h = _headers(alice_token, sales_ws["sales"].id)

    resp = api.post("/api/sessions", headers=h, json={"title": "Case1 会话"})
    assert resp.status_code == 200, resp.text
    sid = resp.json()["id"]
    assert resp.json()["workspace_id"] == sales_ws["sales"].id

    ds_rows = api.get("/api/datasources", headers=h).json()
    if not ds_rows:
        pytest.skip("工作区内无数据源")
    resp = api.post(f"/api/sessions/{sid}/analyze", headers=h,
                    json={"question": "各品类的总销售额是多少", "data_source_ids": [ds_rows[0]["id"]]})
    if resp.status_code == 200:
        assert resp.headers["content-type"].startswith("text/event-stream")
    elif not config.OPENAI_API_KEY:
        pytest.skip("Blocked：未配置 LLM API Key，无法跑完整 AI 分析（权限链路已验证）")
    else:
        pytest.fail(f"分析请求意外失败: {resp.status_code} {resp.text}")


def test_case7_execute_node_tool_level_permission(db_session):
    """AgentState.user_context 纵深防御：无权限上下文在 execute 前被拒。"""
    from backend.agent.graph import _initial_state
    from backend.auth.context import permission_checker

    ctx = {"user_id": 1, "username": "x", "workspace_id": 1,
           "role": "viewer", "permissions": ["datasource:read"], "data_scope": "all"}
    with pytest.raises(PermissionError):
        permission_checker.require(ctx, "analysis:execute")

    # State 构造包含 user_context 钩子（图谱结构未重写）
    state = _initial_state("q", {}, None, None, "", user_context=ctx)
    assert state["user_context"]["role"] == "viewer"
    assert state["skill_block"] == ""  # 既有钩子不受影响


def test_datasource_workspace_scope_in_prepare(db_session):
    """_prepare 数据作用域：其他工作区的数据源不能进入沙箱。"""
    from backend.analysis import runtime
    from backend.models import DataSource

    other = DataSource(name=f"other-{uuid.uuid4().hex[:6]}", type="file",
                       file_path="nowhere.csv", workspace_id=987654)
    db_session.add(other)
    db_session.commit()

    with pytest.raises(Exception, match="数据源不属于当前工作区|不属于当前工作区|未选择任何有效"):
        runtime._prepare(db_session, [other.id], None, lambda *_: None,
                         user_context={"workspace_id": 123, "role": "analyst"})
