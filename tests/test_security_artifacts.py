"""Artifact 访问安全测试：鉴权 / 工作区归属 / 路径遍历 / 不暴露存在性 / 导出不外泄。

覆盖需求点名的产物用例：合法工作区下载、跨工作区下载、不存在资源、path traversal、
未认证访问；另外覆盖仪表板导出里客户端可控 chart_url 的越权与遍历。
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
    db_dir = tmp_path_factory.mktemp("sec_artifact_db")
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
def dirs(tmp_path_factory):
    """把三个静态目录指向临时目录，避免测试往仓库 runs/ uploads/ data/ 里写。"""
    import backend.routers.files as files_mod

    base = tmp_path_factory.mktemp("artifact_dirs")
    runs, uploads = base / "runs", base / "uploads"
    materialized = base / "data" / "materialized"
    for d in (runs, uploads, materialized):
        d.mkdir(parents=True, exist_ok=True)
    old = (files_mod.RUNS_DIR, files_mod.UPLOADS_DIR, files_mod.MATERIALIZED_DIR)
    files_mod.RUNS_DIR, files_mod.UPLOADS_DIR, files_mod.MATERIALIZED_DIR = (
        runs, uploads, materialized)
    # 导出链路（report/exporter → analysis/runtime.chart_url_to_path）用的是 runtime 里
    # 导入的 RUNS_DIR 快照，测试同样要指过去，否则导出读的是仓库真实 runs/
    import backend.analysis.runtime as runtime_mod

    old_runtime_runs = runtime_mod.RUNS_DIR
    runtime_mod.RUNS_DIR = runs
    yield {"runs": runs, "uploads": uploads, "materialized": materialized}
    files_mod.RUNS_DIR, files_mod.UPLOADS_DIR, files_mod.MATERIALIZED_DIR = old
    runtime_mod.RUNS_DIR = old_runtime_runs


@pytest.fixture(scope="module")
def env(api, db_session):
    from backend.auth.security import hash_password
    from backend.models import User, Workspace, WorkspaceMember

    suffix = uuid.uuid4().hex[:6]
    ws_a = Workspace(name=f"Art-A-{suffix}", description="A 区")
    ws_b = Workspace(name=f"Art-B-{suffix}", description="B 区")
    db_session.add_all([ws_a, ws_b])
    db_session.flush()
    users = {}
    for ws, role, name in ((ws_a, "admin", "aa"), (ws_b, "admin", "bb"),
                           (ws_b, "viewer", "bv")):
        u = User(username=f"{name}-{suffix}", display_name=name,
                 password_hash=hash_password("art-pass-12345"))
        db_session.add(u)
        db_session.flush()
        db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role_code=role))
        users[name] = u
    db_session.commit()
    return {"suffix": suffix, "ws_a": ws_a.id, "ws_b": ws_b.id, **users}


def _tok(api, username, password="art-pass-12345"):
    r = api.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(token, ws=None):
    headers = {"Authorization": f"Bearer {token}"}
    if ws:
        headers["X-Workspace-Id"] = str(ws)
    return headers


@pytest.fixture(scope="module")
def assets(env, db_session, dirs):
    """造两条运行（各带一个图表文件）+ 一条孤儿运行 + 上传文件 + 物化文件。"""
    from backend.models import DataSource, Run, Session as DbSession

    def _make_run(tag: str, workspace_id: int | None, orphan: bool = False):
        run_dir = dirs["runs"] / tag / "out"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "chart.png").write_bytes(b"\x89PNG-fake")
        if orphan:
            session_id = 999999  # 指向不存在的会话
        else:
            session = DbSession(title=f"sess-{tag}", workspace_id=workspace_id)
            db_session.add(session)
            db_session.flush()
            session_id = session.id
        run = Run(session_id=session_id, question=f"q-{tag}", status="done", ok=True,
                  run_dir=str(run_dir), charts=f'["/runs/{tag}/out/chart.png"]')
        db_session.add(run)
        db_session.flush()
        return run

    run_a = _make_run(f"run-a-{env['suffix']}", env["ws_a"])
    run_b = _make_run(f"run-b-{env['suffix']}", env["ws_b"])
    run_orphan = _make_run(f"run-orphan-{env['suffix']}", None, orphan=True)

    upload_a = dirs["uploads"] / "sales.csv"
    upload_a.write_text("a,b\n1,2\n", encoding="utf-8")
    ds_a = DataSource(name=f"file-a-{env['suffix']}", type="file",
                      file_path=str(upload_a), file_name="sales.csv",
                      workspace_id=env["ws_a"])
    mat = dirs["materialized"] / "tbl.parquet"
    mat.write_bytes(b"PAR1-fake")
    ds_mat = DataSource(name=f"db-a-{env['suffix']}", type="db", db_type="sqlite",
                        materialized_path=str(mat), materialized_table="tbl",
                        workspace_id=env["ws_a"])
    db_session.add_all([ds_a, ds_mat])
    db_session.commit()
    return {"run_a": run_a, "run_b": run_b, "run_orphan": run_orphan,
            "dir_a": f"run-a-{env['suffix']}", "dir_b": f"run-b-{env['suffix']}",
            "dir_orphan": f"run-orphan-{env['suffix']}",
            "ds_a": ds_a, "ds_mat": ds_mat,
            "upload_name": "sales.csv", "mat_rel": "tbl.parquet",
            "files": {"a": "chart.png"}}


# ---- /runs 产物 ----

def test_owner_workspace_can_read_run_artifact(api, env, assets):
    """产物 URL 用**运行目录名**（分析链路真实生成的形状），归属按 Run.run_dir 反查。"""
    token = _tok(api, f"aa-{env['suffix']}")
    resp = api.get(f"/runs/{assets['dir_a']}/out/chart.png",
                   headers=_h(token, env["ws_a"]))
    assert resp.status_code == 200, resp.text
    assert resp.content


def test_run_artifact_also_resolvable_by_run_id(api, env, assets):
    """兼容按 Run.id 访问（同一份归属校验）。"""
    token = _tok(api, f"aa-{env['suffix']}")
    assert api.get(f"/runs/{assets['run_a'].id}/out/chart.png",
                   headers=_h(token, env["ws_a"])).status_code == 200


def test_other_workspace_gets_404(api, env, assets):
    """猜到别人工作区的运行目录名也拿不到文件（归属由 DB 反查决定）。"""
    token = _tok(api, f"bb-{env['suffix']}")
    for key in (assets["dir_a"], str(assets["run_a"].id)):
        assert api.get(f"/runs/{key}/out/chart.png",
                       headers=_h(token, env["ws_b"])).status_code == 404


def test_orphan_run_is_not_readable_by_anyone(api, env, assets):
    """孤儿运行（会话已删）不再被当作"全局可读"。"""
    token = _tok(api, f"aa-{env['suffix']}")
    assert api.get(f"/runs/{assets['dir_orphan']}/out/chart.png",
                   headers=_h(token, env["ws_a"])).status_code == 404


def test_anonymous_artifact_access_is_401(api, env, assets):
    assert api.get(f"/runs/{assets['dir_a']}/out/chart.png").status_code == 401


def test_path_traversal_is_blocked(api, env, assets):
    """`../`（含 URL 编码变体）不能越过 runs 基目录读到任意文件。"""
    token = _tok(api, f"aa-{env['suffix']}")
    key = assets["dir_a"]
    encoded = [f"/runs/{key}/%2e%2e%2f%2e%2e%2fdata%2fapp.db",
               f"/runs/{key}/out/%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fhosts",
               f"/runs/{key}/%2Fetc%2Fhosts"]
    for path in encoded:
        resp = api.get(path, headers=_h(token, env["ws_a"]))
        assert resp.status_code in (400, 404), (path, resp.status_code)

    # 未编码的 `..` 会被 HTTP 客户端/网关规范化，这里断言"无论如何都读不到目标文件"
    for path in (f"/runs/{key}/../../data/app.db",
                 f"/runs/{key}/out/../../../../../etc/hosts"):
        resp = api.get(path, headers=_h(token, env["ws_a"]))
        assert b"SQLite format" not in resp.content
        assert b"localhost" not in resp.content


def test_missing_run_artifact_is_404(api, env, assets):
    token = _tok(api, f"aa-{env['suffix']}")
    assert api.get("/runs/99999999/out/chart.png",
                   headers=_h(token, env["ws_a"])).status_code == 404


def test_artifact_denial_is_audited(api, env, assets):
    token = _tok(api, f"bb-{env['suffix']}")
    api.get(f"/runs/{assets['run_a'].id}/out/chart.png", headers=_h(token, env["ws_b"]))
    admin = _tok(api, f"aa-{env['suffix']}")
    items = api.get("/api/auth/audit?event=artifact&limit=50",
                    headers=_h(admin, env["ws_a"])).json()["items"]
    assert any(e["event"] == "artifact.access_denied" for e in items), items[:3]


# ---- /uploads 源文件 ----

def test_upload_owner_can_read(api, env, assets):
    token = _tok(api, f"aa-{env['suffix']}")
    resp = api.get(f"/uploads/{assets['upload_name']}", headers=_h(token, env["ws_a"]))
    assert resp.status_code == 200 and resp.content


def test_upload_cross_workspace_404(api, env, assets):
    token = _tok(api, f"bb-{env['suffix']}")
    assert api.get(f"/uploads/{assets['upload_name']}",
                   headers=_h(token, env["ws_b"])).status_code == 404


def test_unregistered_upload_file_is_404(api, env, assets, dirs):
    """只有服务登记过的文件才可下发：临时塞进 uploads 的文件读不到。"""
    (dirs["uploads"] / "secret.csv").write_text("x\n1\n", encoding="utf-8")
    token = _tok(api, f"aa-{env['suffix']}")
    assert api.get("/uploads/secret.csv",
                   headers=_h(token, env["ws_a"])).status_code == 404


def test_upload_anonymous_401(api, env, assets):
    assert api.get(f"/uploads/{assets['upload_name']}").status_code == 401


# ---- /data/materialized ----

def test_materialized_owner_can_read(api, env, assets):
    token = _tok(api, f"aa-{env['suffix']}")
    resp = api.get(f"/data/materialized/{assets['mat_rel']}", headers=_h(token, env["ws_a"]))
    assert resp.status_code == 200 and resp.content


def test_materialized_cross_workspace_404(api, env, assets):
    token = _tok(api, f"bb-{env['suffix']}")
    assert api.get(f"/data/materialized/{assets['mat_rel']}",
                   headers=_h(token, env["ws_b"])).status_code == 404


def test_whole_data_dir_is_not_exposed(api, env, assets):
    """整个 data/ 目录不再静态可访问（原先连 SQLite 元数据库都能被下载）。"""
    token = _tok(api, f"aa-{env['suffix']}")
    for path in ("/data/app.db", "/data/materialized/../app.db"):
        assert api.get(path, headers=_h(token, env["ws_a"])).status_code == 404


# ---- 导出链路：客户端可控的 chart_url / source_run_id ----

def test_dashboard_export_skips_foreign_and_traversing_charts(api, env, assets, dirs,
                                                              db_session):
    """导出只嵌本工作区可见产物；`../` 与别的工作区图表一律跳过。"""
    from backend.models import Dashboard, DashboardItem

    dash = Dashboard(name=f"dash-{env['suffix']}", workspace_id=env["ws_a"])
    db_session.add(dash)
    db_session.flush()
    db_session.add(DashboardItem(
        dashboard_id=dash.id, type="chart", title="外区图表",
        payload='{"chart_url": "/runs/%s/out/chart.png"}' % f"run-b-{env['suffix']}"))
    db_session.add(DashboardItem(
        dashboard_id=dash.id, type="chart", title="遍历",
        payload='{"chart_url": "/runs/../uploads/sales.csv"}'))
    db_session.add(DashboardItem(
        dashboard_id=dash.id, type="chart", title="本区图表",
        payload='{"chart_url": "/runs/%s/out/chart.png"}' % f"run-a-{env['suffix']}"))
    db_session.commit()

    token = _tok(api, f"aa-{env['suffix']}")
    resp = api.get(f"/api/dashboards/{dash.id}/export", headers=_h(token, env["ws_a"]))
    assert resp.status_code == 200
    html = resp.text
    assert "本区图表" in html
    assert "外区图表" not in html and "遍历" not in html


def test_dashboard_export_denied_cross_workspace(api, env, assets, db_session):
    from backend.models import Dashboard

    dash = Dashboard(name=f"dashb-{env['suffix']}", workspace_id=env["ws_b"])
    db_session.add(dash)
    db_session.commit()
    token = _tok(api, f"aa-{env['suffix']}")
    assert api.get(f"/api/dashboards/{dash.id}/export",
                   headers=_h(token, env["ws_a"])).status_code == 404


def test_pinning_foreign_run_is_rejected(api, env, assets, db_session):
    """往仪表板里固定别的工作区的运行 → 404（不暴露存在性）。"""
    from backend.models import Dashboard

    dash = Dashboard(name=f"dashc-{env['suffix']}", workspace_id=env["ws_a"])
    db_session.add(dash)
    db_session.commit()
    token = _tok(api, f"aa-{env['suffix']}")
    resp = api.post(f"/api/dashboards/{dash.id}/items",
                    json={"type": "text", "title": "x", "payload": {"text": "hi"},
                          "source_run_id": assets["run_b"].id},
                    headers=_h(token, env["ws_a"]))
    assert resp.status_code == 404
