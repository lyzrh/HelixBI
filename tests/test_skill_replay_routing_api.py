"""API 层：/sessions/{id}/analyze 的 Skill Replay 分流（真实 HTTP 栈 + 真实 RBAC）。

覆盖「与 Agent 路由结合」这一条：高置信度命中 → 直接重放（0 次 LLM 生成）；
低置信度 → 完整 Agent；权限不足 → 403。沙箱被执行替身替换，测的是路由与授权。
"""

import importlib
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CSV_CODE = "import pandas as pd\n\ndf = pd.read_csv('/data/sales.csv')\n"
COLUMNS = ["订单日期", "品类", "销售额", "数量"]


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    """独立临时库 + 重新绑定持有 SessionLocal 引用的模块。

    注意：`engine` / `runtime` 在模块导入时 `from backend.db import SessionLocal`，
    只 reload `backend.db` 不会更新它们手里的引用（会继续写上一个测试模块的库）。
    因此这里显式 reload。这也是本仓库既有的测试隔离方式（见 tests/test_auth_rbac.py）。
    """
    db_dir = tmp_path_factory.mktemp("replay_routing_db")
    os.environ["APP_DB_PATH"] = str(db_dir / "test_app.db")

    import backend.config as config
    importlib.reload(config)
    import backend.db as db_mod
    importlib.reload(db_mod)
    import backend.analysis.runtime as runtime
    importlib.reload(runtime)
    import backend.skills.engine as skill_engine
    importlib.reload(skill_engine)

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        yield client
    os.environ.pop("APP_DB_PATH", None)


@pytest.fixture(scope="module")
def setup(app_env, tmp_path_factory):
    from backend.auth.security import hash_password
    from backend.db import SessionLocal
    from backend.models import DataSource, Skill, User, Workspace, WorkspaceMember

    csv = tmp_path_factory.mktemp("replay_csv") / "sales.csv"
    csv.write_text("订单日期,品类,销售额,数量\n2026-01-01,饮料,100,2\n", encoding="utf-8")

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        ws = Workspace(name=f"Replay-{suffix}")
        db.add(ws)
        db.flush()
        analyst = User(username=f"analyst-{suffix}", display_name="Analyst",
                       password_hash=hash_password("analyst-pass-123"))
        viewer = User(username=f"viewer-{suffix}", display_name="Viewer",
                      password_hash=hash_password("viewer-pass-123"))
        db.add_all([analyst, viewer])
        db.flush()
        db.add_all([
            WorkspaceMember(workspace_id=ws.id, user_id=analyst.id, role_code="analyst"),
            WorkspaceMember(workspace_id=ws.id, user_id=viewer.id, role_code="viewer"),
        ])
        ds = DataSource(name="销售数据", type="file", file_path=str(csv),
                        file_name="sales.csv", pack_id="retail_sales",
                        workspace_id=ws.id,
                        columns_json=json.dumps(COLUMNS, ensure_ascii=False))
        db.add(ds)
        db.flush()
        skill = Skill(name="各品类销售额", question="各品类的销售额是多少？",
                      pack_id="retail_sales", code=CSV_CODE,
                      columns_json=json.dumps(COLUMNS, ensure_ascii=False),
                      datasource_key="retail_sales::csv",
                      scope="workspace", workspace_id=ws.id)
        db.add(skill)
        db.commit()
        payload = {"ws": ws.id, "ds": ds.id, "skill": skill.id,
                   "analyst": analyst.username, "viewer": viewer.username,
                   "suffix": suffix}
    return payload


def _token(client, username: str, password: str = "analyst-pass-123") -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _headers(token: str, ws: int) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(ws)}


def _new_session(client, token, ws) -> int:
    resp = client.post("/api/sessions", json={"title": "重放测试"},
                       headers=_headers(token, ws))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.fixture
def fake_sandbox(monkeypatch):
    """替换沙箱执行：真实契约（result.json）+ 可切换成功/失败。"""
    from pathlib import Path as _Path

    from backend.agent.sandbox import SandboxResult
    from backend.skills import engine as skill_engine

    calls: list = []

    def _run(run_id, code, files):
        calls.append({"run_id": run_id, "code": code})
        out = _Path(list(files.values())[0]).parent / f"runs/{run_id}/out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(
            json.dumps({"text": "各品类销售额：食品 100", "tables": {"t": [{"a": 1}]},
                        "charts": []}, ensure_ascii=False), encoding="utf-8")
        return SandboxResult(ok=True, stdout="", stderr="", out_dir=out)

    monkeypatch.setattr(skill_engine, "run_in_sandbox", _run)
    return calls


def test_high_confidence_query_is_replayed_without_agent(app_env, setup, fake_sandbox,
                                                         monkeypatch):
    """命中重放时：沙箱被调用、Agent 链路完全不被调用。"""
    import backend.analysis.runtime as runtime

    agent_calls: list = []
    monkeypatch.setattr(runtime, "run_analysis_stream",
                        lambda *a, **kw: agent_calls.append(kw) or
                        {"run_id": 0, "ok": True})

    token = _token(app_env, setup["analyst"])
    sid = _new_session(app_env, token, setup["ws"])
    resp = app_env.post(f"/api/sessions/{sid}/analyze",
                        json={"question": "各品类的销售额是多少？",
                              "data_source_ids": [setup["ds"]]},
                        headers=_headers(token, setup["ws"]))
    assert resp.status_code == 200, resp.text
    assert "event: step" in resp.text
    assert fake_sandbox, "应当走重放路径（沙箱被执行）"
    assert agent_calls == [], "重放不应触发 Agent 链路"

    from backend.db import SessionLocal
    from backend.models import Run

    with SessionLocal() as db:
        run = db.query(Run).order_by(Run.id.desc()).first()
        trace = json.loads(run.trace)
        assert trace["skill"]["mode"] == "replay"
        assert trace["llm"]["calls"] == 0
        assert trace["skill"]["retrieval"]["decision"] == "replay"
        assert trace["skill"]["retrieval"]["candidates"], "必须留下候选与打分依据"


def test_low_confidence_query_goes_to_agent(app_env, setup, fake_sandbox, monkeypatch):
    """口径不一致（订单量 vs 销售额）时不得重放，必须走完整 Agent。"""
    import backend.analysis.runtime as runtime

    agent_calls: list = []

    def _agent(run_pk, session_id, question, data_source_ids, spec, skill_block="",
               agent_id=None, on_event=None, skill_ids=None, user_context=None,
               retrieval=None, skill_candidates=None, context_policy=None,
               budget_overrides=None):
        agent_calls.append({"retrieval": retrieval, "skill_block": skill_block})
        if on_event:
            on_event("done", {"ok": True})
        return {"run_id": run_pk, "ok": True}

    monkeypatch.setattr(runtime, "run_analysis_stream", _agent)

    token = _token(app_env, setup["analyst"])
    sid = _new_session(app_env, token, setup["ws"])
    resp = app_env.post(f"/api/sessions/{sid}/analyze",
                        json={"question": "各品类的订单量是多少？",
                              "data_source_ids": [setup["ds"]]},
                        headers=_headers(token, setup["ws"]))
    assert resp.status_code == 200, resp.text
    assert fake_sandbox == [], "口径不一致时不得执行重放"
    assert len(agent_calls) == 1
    assert agent_calls[0]["retrieval"]["decision"] == "agent"
    assert agent_calls[0]["retrieval"]["reason_code"] == "blocked:metric_mismatch"


def test_viewer_is_denied_analysis(app_env, setup):
    """RBAC 不被 Replay 绕过：Viewer 连请求都进不来。"""
    analyst_token = _token(app_env, setup["analyst"])
    sid = _new_session(app_env, analyst_token, setup["ws"])

    viewer_token = _token(app_env, setup["viewer"], "viewer-pass-123")
    resp = app_env.post(f"/api/sessions/{sid}/analyze",
                        json={"question": "各品类的销售额是多少？",
                              "data_source_ids": [setup["ds"]]},
                        headers=_headers(viewer_token, setup["ws"]))
    assert resp.status_code == 403


def test_skill_run_denied_for_other_workspace(app_env, setup):
    """手动运行 Skill 同样受工作区门禁保护（纵深防御）。

    这里让用户在有权限的**另一个**工作区里（analyst 角色）去运行本工作区的 Skill，
    权限点齐全，唯一拦住它的是工作区归属校验。
    """
    from backend.db import SessionLocal
    from backend.models import User, Workspace, WorkspaceMember

    with SessionLocal() as db:
        other = Workspace(name=f"Other-{uuid.uuid4().hex[:6]}")
        db.add(other)
        db.flush()
        user = (db.query(User).filter(User.username == setup["analyst"]).first())
        db.add(WorkspaceMember(workspace_id=other.id, user_id=user.id, role_code="analyst"))
        db.commit()
        other_id = other.id

    token = _token(app_env, setup["analyst"])
    resp = app_env.post(f"/api/skills/{setup['skill']}/run",
                        json={"session_id": None, "data_source_ids": [setup["ds"]]},
                        headers=_headers(token, other_id))
    assert resp.status_code == 403


def test_run_detail_exposes_retrieval_reason(app_env, setup, fake_sandbox):
    """可观测性：运行详情里能回答「为什么重放 / 为什么没重放」。"""
    token = _token(app_env, setup["analyst"])
    sid = _new_session(app_env, token, setup["ws"])
    app_env.post(f"/api/sessions/{sid}/analyze",
                 json={"question": "各品类的销售额是多少？",
                       "data_source_ids": [setup["ds"]]},
                 headers=_headers(token, setup["ws"]))
    # 重放路径也要把会话标题从「新会话」更新掉（两条路径行为一致）
    sessions = app_env.get("/api/sessions", headers=_headers(token, setup["ws"])).json()
    assert any(s["id"] == sid and s["title"] != "新会话" for s in sessions)

    recent = app_env.get("/api/runs/recent", headers=_headers(token, setup["ws"])).json()
    assert recent, "应当有运行记录"
    detail = app_env.get(f"/api/runs/{recent[0]['id']}",
                         headers=_headers(token, setup["ws"])).json()
    skill = detail["trace"]["skill"]
    assert skill["mode"] == "replay"
    assert skill["retrieval"]["selected_skill_id"] == setup["skill"]
    assert skill["retrieval"]["candidates"][0]["scores"]["metric"] == 1.0
