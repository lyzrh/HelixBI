"""RBAC 闭环回归：静态文件鉴权 / 会话与运行记录隔离 / insights 隔离 / settings-agents 权限门。

覆盖本轮补齐的缺口，全部按「正例 + 越权拒绝」成对断言。
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
    db_dir = tmp_path_factory.mktemp("rbac_closure_db")
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
    """默认工作区（种子 admin）+ 第二工作区（独立 admin_b）+ 三角色用户。"""
    from backend.models import User, Workspace, WorkspaceMember
    from backend.auth.security import hash_password

    suffix = uuid.uuid4().hex[:6]
    ws_b = Workspace(name=f"RBAC-B-{suffix}", description="第二工作区")
    db_session.add(ws_b)
    db_session.flush()
    admin_b = User(username=f"admin2-{suffix}", display_name="B区管理员",
                   password_hash=hash_password("admin2-pass-123"))
    db_session.add(admin_b)
    db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=ws_b.id, user_id=admin_b.id,
                                   role_code="admin"))
    for name, role in ((f"ana-{suffix}", "analyst"), (f"vie-{suffix}", "viewer")):
        u = User(username=name, display_name=name,
                 password_hash=hash_password("pass-12345678"))
        db_session.add(u)
        db_session.flush()
        db_session.add(WorkspaceMember(workspace_id=ws_b.id, user_id=u.id,
                                       role_code=role))
    db_session.commit()
    return {"suffix": suffix, "ws_a": 1, "ws_b": ws_b.id, "admin_b": admin_b}


def _tok(api, username, password):
    r = api.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(token, ws=None):
    d = {"Authorization": f"Bearer {token}"}
    if ws:
        d["X-Workspace-Id"] = str(ws)
    return d


# ---- settings / agents 权限门 ----

def test_settings_llm_admin_only(api, env):
    s = env["suffix"]
    admin = _tok(api, "admin", "admin123")
    ana = _tok(api, f"ana-{s}", "pass-12345678")
    vie = _tok(api, f"vie-{s}", "pass-12345678")

    # 读配置保持登录门（前端需展示模型名）
    assert api.get("/api/settings/llm", headers=_h(ana)).status_code == 200
    # 写配置是系统级操作：analyst / viewer 403，admin 200
    body = {"model": "deepseek-chat"}
    assert api.put("/api/settings/llm", headers=_h(ana, env["ws_b"]), json=body).status_code == 403
    assert api.put("/api/settings/llm", headers=_h(vie, env["ws_b"]), json=body).status_code == 403
    r = api.put("/api/settings/llm", headers=_h(admin, 1), json=body)
    assert r.status_code == 200, r.text
    # 连通性测试同样仅 admin
    assert api.post("/api/settings/llm/test", headers=_h(ana), json={}).status_code == 403


def test_agents_write_admin_only(api, env):
    s = env["suffix"]
    ana = _tok(api, f"ana-{s}", "pass-12345678")
    vie = _tok(api, f"vie-{s}", "pass-12345678")
    admin = _tok(api, "admin", "admin123")

    # 场景 Agent 全员可读（登录门）
    assert api.get("/api/agents", headers=_h(vie)).status_code == 200
    # 写是管理操作：非 admin 403
    body = {"name": f"agent-{s}", "description": "", "pack_id": "retail_sales",
            "data_source_ids": [1], "intro": "", "recommended_questions": []}
    assert api.post("/api/agents", headers=_h(ana), json=body).status_code == 403
    assert api.post("/api/agents", headers=_h(vie), json=body).status_code == 403
    r = api.post("/api/agents", headers=_h(admin, 1), json=body)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert api.patch(f"/api/agents/{aid}", headers=_h(ana), json={"name": "x"}).status_code == 403
    assert api.delete(f"/api/agents/{aid}", headers=_h(ana)).status_code == 403
    assert api.delete(f"/api/agents/{aid}", headers=_h(admin, 1)).status_code == 200


# ---- 会话：改名 / 删除 的工作区隔离与角色门 ----

def test_session_patch_delete_isolated(api, env, db_session):
    from backend.models import Session as DbSession

    s = env["suffix"]
    # B 区会话（属 ws_b）
    sess = DbSession(title=f"b-sess-{s}", workspace_id=env["ws_b"])
    db_session.add(sess)
    db_session.flush()
    # A 区会话（默认工作区）
    sess_a = DbSession(title=f"a-sess-{s}", workspace_id=env["ws_a"])
    db_session.add(sess_a)
    db_session.commit()

    ana = _tok(api, f"ana-{s}", "pass-12345678")   # analyst，但只在 ws_b
    vie = _tok(api, f"vie-{s}", "pass-12345678")   # viewer，只在 ws_b

    # 跨工作区改/删 → 403（即使 analyst 有 analysis:create，也不是这个工作区的会话）
    assert api.patch(f"/api/sessions/{sess_a.id}", headers=_h(ana, env["ws_b"]),
                     json={"title": "hack"}).status_code == 403
    assert api.delete(f"/api/sessions/{sess_a.id}", headers=_h(ana, env["ws_b"])).status_code == 403

    # viewer 在自己工作区也没有 analysis:create → 403
    assert api.patch(f"/api/sessions/{sess.id}", headers=_h(vie, env["ws_b"]),
                     json={"title": "x"}).status_code == 403

    # B 区 analyst 改自己工作区会话 → 200
    assert api.patch(f"/api/sessions/{sess.id}", headers=_h(ana, env["ws_b"]),
                     json={"title": "renamed"}).status_code == 200
    # 清理
    assert api.delete(f"/api/sessions/{sess.id}", headers=_h(ana, env["ws_b"])).status_code == 200


# ---- 运行记录：读取 / 导出 / 用量明细按工作区隔离 ----

def test_runs_and_usage_isolated(api, env, db_session):
    from backend.models import Run, Session as DbSession

    s = env["suffix"]
    sess = DbSession(title=f"run-sess-{s}", workspace_id=env["ws_a"])
    db_session.add(sess)
    db_session.flush()
    run = Run(session_id=sess.id, question="q", status="done", ok=True)
    db_session.add(run)
    db_session.commit()

    ana = _tok(api, f"ana-{s}", "pass-12345678")  # ws_b analyst（对 ws_a 无身份）
    h_b = _h(ana, env["ws_b"])
    # 跨工作区运行记录 → 404（不暴露存在性）
    assert api.get(f"/api/runs/{run.id}", headers=h_b).status_code == 404
    assert api.get(f"/api/runs/{run.id}/export", headers=h_b).status_code == 404
    assert api.get(f"/api/usage/sessions/{run.id}/tokens", headers=h_b).status_code == 404
    # recent 只含本工作区
    admin = _tok(api, "admin", "admin123")
    ids = [r["id"] for r in api.get("/api/runs/recent", headers=_h(admin, 1)).json()]
    assert run.id in ids
    ids_b = [r["id"] for r in api.get("/api/runs/recent", headers=h_b).json()]
    assert run.id not in ids_b
    # 归属工作区可正常读取
    assert api.get(f"/api/runs/{run.id}", headers=_h(admin, 1)).status_code == 200


# ---- insights：按关联数据源工作区隔离 + 权限点 ----

def test_insights_isolation_and_permissions(api, env, db_session):
    from backend.models import DataSource, Insight

    s = env["suffix"]
    # A 区数据源 + 洞察
    ds_a = DataSource(name=f"ds-a-{s}", type="file", file_path="x.csv",
                      workspace_id=env["ws_a"])
    db_session.add(ds_a)
    db_session.flush()
    ins = Insight(data_source_id=ds_a.id, rule_id="spike", severity="info",
                  title=f"ins-{s}")
    db_session.add(ins)
    db_session.commit()

    admin = _tok(api, "admin", "admin123")
    ana = _tok(api, f"ana-{s}", "pass-12345678")   # ws_b analyst
    vie = _tok(api, f"vie-{s}", "pass-12345678")   # ws_b viewer

    # B 区用户看不到 A 区洞察（列表 / 详情 / 导出 / 改状态全隔离）
    assert ins.id not in [i["id"] for i in api.get("/api/insights", headers=_h(ana, env["ws_b"])).json()]
    assert api.get(f"/api/insights/{ins.id}", headers=_h(ana, env["ws_b"])).status_code == 404
    assert api.post(f"/api/insights/{ins.id}/report", headers=_h(ana, env["ws_b"])).status_code == 404
    assert api.patch(f"/api/insights/{ins.id}", headers=_h(ana, env["ws_b"]),
                     json={"status": "resolved"}).status_code == 404
    csv_b = api.get("/api/insights/export", headers=_h(ana, env["ws_b"])).text
    assert f"ins-{s}" not in csv_b

    # 权限点：viewer 不能生成（403 先于资源校验）；ws_b analyst 有权限但数据源在 A 区 → 404
    assert api.post("/api/insights/generate", headers=_h(vie, env["ws_b"]),
                    json={"data_source_ids": [ds_a.id]}).status_code == 403
    assert api.post("/api/insights/generate", headers=_h(ana, env["ws_b"]),
                    json={"data_source_ids": [ds_a.id]}).status_code == 404
    # 归属工作区 admin 改状态 → 200
    assert api.patch(f"/api/insights/{ins.id}", headers=_h(admin, 1),
                     json={"status": "read"}).status_code == 200

    # 调度配置：读是 datasource:read，写是 workspace:manage
    assert api.get("/api/insights/schedule", headers=_h(vie, env["ws_b"])).status_code == 200
    assert api.put("/api/insights/schedule", headers=_h(ana, env["ws_b"]),
                   json={"enabled": False}).status_code == 403
    assert api.post("/api/insights/schedule/run", headers=_h(ana, env["ws_b"])).status_code == 403


# ---- 鉴权文件下发（替代无鉴权静态挂载）----

def test_protected_file_serving(api, env, db_session):
    from backend.models import DataSource

    s = env["suffix"]
    admin = _tok(api, "admin", "admin123")
    ana = _tok(api, f"ana-{s}", "pass-12345678")  # 只在 ws_b

    # A 区自建上传文件（内置示例数据源 workspace_id=NULL 属有意全局共享，不作跨区样本）
    from backend.config import UPLOADS_DIR

    up_name = f"iso-{s}.csv"
    up_path = UPLOADS_DIR / up_name
    up_path.write_text("a,b\n1,2\n", encoding="utf-8")
    ds = DataSource(name=f"up-{s}", type="file", file_path=str(up_path),
                    file_name=up_name, file_type="csv", workspace_id=env["ws_a"])
    db_session.add(ds)
    db_session.commit()
    try:
        # 匿名访问全部拒绝（此前 /uploads/sample_sales.csv、/data/app.db 可匿名下载）
        assert api.get("/uploads/sample_sales.csv").status_code == 401
        assert api.get("/data/app.db").status_code in (401, 404)
        assert api.get("/runs/1/out/x.png").status_code == 401

        # 归属工作区可读
        r = api.get(f"/uploads/{up_name}", headers=_h(admin, 1))
        assert r.status_code == 200
        # 内置示例文件（历史全局 NULL）登录用户均可读
        assert api.get("/uploads/sample_sales.csv", headers=_h(admin, 1)).status_code == 200

        # 跨工作区 → 404（不暴露文件存在性）
        assert api.get(f"/uploads/{up_name}", headers=_h(ana, env["ws_b"])).status_code == 404
    finally:
        up_path.unlink(missing_ok=True)

    # 路径遍历防护
    assert api.get("/uploads/..%2F..%2Fbackend%2Fconfig.py", headers=_h(admin, 1)).status_code in (401, 404)
    # 不存在的运行产物 → 404
    assert api.get("/runs/999999/out/x.png", headers=_h(admin, 1)).status_code == 404
    # SQLite 元数据库不再可从 /data 下载（旧挂载曾 200）
    assert api.get("/data/app.db", headers=_h(admin, 1)).status_code == 404


# ---- datasources：单资源与写操作的工作区隔离 ----

def test_datasource_single_resource_isolated(api, env, db_session):
    from backend.models import DataSource

    s = env["suffix"]
    ds = DataSource(name=f"ds-iso-{s}", type="file", file_path="iso.csv",
                    workspace_id=env["ws_a"])
    db_session.add(ds)
    db_session.commit()

    ana = _tok(api, f"ana-{s}", "pass-12345678")  # ws_b analyst
    h_b = _h(ana, env["ws_b"])
    assert api.get(f"/api/datasources/{ds.id}", headers=h_b).status_code == 404
    assert api.get(f"/api/datasources/{ds.id}/preview", headers=h_b).status_code == 404
    assert api.patch(f"/api/datasources/{ds.id}", headers=h_b, json={"name": "x"}).status_code == 404
    assert api.delete(f"/api/datasources/{ds.id}", headers=h_b).status_code == 404
    # 归属工作区正常
    admin = _tok(api, "admin", "admin123")
    assert api.get(f"/api/datasources/{ds.id}", headers=_h(admin, 1)).status_code == 200
