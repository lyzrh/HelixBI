"""重放安全与兜底：RBAC / Workspace / 数据源守卫 / 重放失败自动 fallback。

这一组是「端到端语义」的测试——不测单个函数，而是测**行为契约**：

1. 跨工作区的 Skill 不许运行（RBAC 不被 Replay 绕过）；
2. 数据源整体变化后不得盲目重放，而是退回 Agent 重新生成；
3. 重放代码执行失败时自动 fallback 到 Agent，用户拿到结果而不是报错；
4. 准入拒绝（admission_declined）时走 few-shot，而不是硬重放；
5. 每次运行都留下「为什么重放 / 为什么没重放」的可观测记录。

沙箱与 LLM 都被替换成确定性的替身——测的是路由与防守逻辑，不是 Docker。
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, DataSource, Run, Session as DbSession, Skill
from backend.skills import engine as skill_engine
from backend.skills import retrieval

CSV_CODE = "import pandas as pd\n\ndf = pd.read_csv('/data/sales.csv')\n"
COLUMNS = ["订单日期", "品类", "销售额", "数量"]


@pytest.fixture
def db(monkeypatch, tmp_path):
    """独立内存库 + 把引擎的 SessionLocal 指过去。

    用与生产一致的 sessionmaker 参数（`expire_on_commit=False`）——否则测试会因为
    commit 后属性过期而报 DetachedInstanceError，那不是被测代码的问题。
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(skill_engine, "SessionLocal", Session)
    import backend.analysis.runtime as runtime

    monkeypatch.setattr(runtime, "SessionLocal", Session)
    with Session() as session:
        yield session, Session


def _seed(db, tmp_path, *, workspace_id=1, datasource_key="retail_sales::csv",
          pack_id="retail_sales", columns=None):
    csv = tmp_path / "sales.csv"
    csv.write_text("订单日期,品类,销售额,数量\n2026-01-01,饮料,100,2\n", encoding="utf-8")
    ds = DataSource(name="销售数据", type="file", file_path=str(csv), file_name="sales.csv",
                    pack_id=pack_id, workspace_id=workspace_id,
                    columns_json=json.dumps(columns or COLUMNS, ensure_ascii=False))
    session = DbSession(title="t", workspace_id=workspace_id)
    db.add_all([ds, session])
    db.flush()
    skill = Skill(name="各品类销售额", question="各品类的销售额是多少？", pack_id=pack_id,
                  code=CSV_CODE, columns_json=json.dumps(COLUMNS, ensure_ascii=False),
                  datasource_key=datasource_key, scope="workspace", workspace_id=workspace_id)
    db.add(skill)
    db.commit()
    return {"ds": ds, "session": session, "skill": skill}


def _fake_sandbox(ok: bool, text: str = "结论"):
    """替换 sandbox 执行：真实 SandboxResult 从 out_dir/result.json 读契约数据。"""
    from pathlib import Path

    from backend.agent.sandbox import SandboxResult

    def _run(run_id, code, files):
        out = Path(list(files.values())[0]).parent / f"runs/{run_id}/out"
        out.mkdir(parents=True, exist_ok=True)
        payload = {"text": text, "tables": {"t": [{"a": 1}]}, "charts": []} if ok else {}
        (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False),
                                         encoding="utf-8")
        return SandboxResult(ok=ok, stdout="", stderr="" if ok else "boom", out_dir=out)

    return _run


def _agent_stub(monkeypatch, called: list):
    """替换 Agent 链路，记录被调用的原因。"""
    import backend.analysis.runtime as runtime

    def _run(run_pk, session_id, question, data_source_ids, spec, skill_block="",
             agent_id=None, on_event=None, skill_ids=None, user_context=None,
             retrieval=None, skill_candidates=None, context_policy=None,
             budget_overrides=None):
        called.append({"run_pk": run_pk, "skill_block": skill_block,
                       "retrieval": retrieval, "skill_ids": skill_ids})
        if on_event:
            on_event("done", {"run_id": run_pk, "ok": True})
        return {"run_id": run_pk, "ok": True, "duration_ms": 1}

    monkeypatch.setattr(runtime, "run_analysis_stream", _run)


# ---- 1. 正常重放：0 次 LLM 调用 ----

def test_replay_path_hits_zero_llm_calls(db, tmp_path, monkeypatch):
    session, Session = db
    seeded = _seed(session, tmp_path)
    monkeypatch.setattr(skill_engine, "run_in_sandbox", _fake_sandbox(True))
    called: list = []
    _agent_stub(monkeypatch, called)

    summary = skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                                    [seeded["ds"].id], workspace_id=1,
                                    question="各品类的销售额是多少？")
    assert summary["ok"] is True
    assert called == [], "命中重放时不应调用 Agent"
    with Session() as s:
        run = s.query(Run).order_by(Run.id.desc()).first()
        trace = json.loads(run.trace)
        assert trace["skill"]["mode"] == "replay"
        assert trace["llm"]["calls"] == 0
        assert run.attempts == 1


# ---- 2. 重放失败 → 自动 fallback 到 Agent ----

def test_replay_failure_falls_back_to_agent(db, tmp_path, monkeypatch):
    session, Session = db
    seeded = _seed(session, tmp_path)
    monkeypatch.setattr(skill_engine, "run_in_sandbox", _fake_sandbox(False))
    called: list = []
    _agent_stub(monkeypatch, called)

    summary = skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                                    [seeded["ds"].id], workspace_id=1,
                                    question="各品类的销售额是多少？")
    assert summary["ok"] is True, "重放失败必须安全 fallback，而不是把错误抛给用户"
    assert len(called) == 1
    assert called[0]["retrieval"]["fallback_reason"] == "replay_failed"
    assert called[0]["skill_block"], "fallback 时应把 Skill 作为 few-shot 注入"


# ---- 3. 数据源整体变化 → 不重放，走 Agent ----

def test_datasource_change_skips_replay(db, tmp_path, monkeypatch):
    session, Session = db
    # Skill 沉淀自 xlsx，当前数据是 csv → 指纹不一致
    seeded = _seed(session, tmp_path, datasource_key="retail_sales::xlsx")
    monkeypatch.setattr(skill_engine, "run_in_sandbox", _fake_sandbox(True))
    called: list = []
    _agent_stub(monkeypatch, called)

    skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                           [seeded["ds"].id], workspace_id=1)
    assert len(called) == 1, "数据源换了不能盲目重放"


def test_column_missing_skips_replay(db, tmp_path, monkeypatch):
    session, Session = db
    seeded = _seed(session, tmp_path)
    seeded["skill"].columns_json = json.dumps([*COLUMNS, "折扣"], ensure_ascii=False)
    session.commit()
    monkeypatch.setattr(skill_engine, "run_in_sandbox", _fake_sandbox(True))
    called: list = []
    _agent_stub(monkeypatch, called)

    skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                           [seeded["ds"].id], workspace_id=1)
    assert len(called) == 1


# ---- 4. 准入拒绝 → few-shot，而不是硬重放 ----

def test_admission_declined_forces_agent(db, tmp_path, monkeypatch):
    session, Session = db
    seeded = _seed(session, tmp_path)
    monkeypatch.setattr(skill_engine, "run_in_sandbox", _fake_sandbox(True))
    called: list = []
    _agent_stub(monkeypatch, called)

    decision = retrieval.route(session, "各品类的订单量是多少？", "retail_sales",
                               workspace_id=1, user_id=1,
                               data_source_ids=[seeded["ds"].id])
    assert decision.decision == "agent"
    assert decision.reason_code == "blocked:metric_mismatch"

    skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                           [seeded["ds"].id], workspace_id=1,
                           question="各品类的订单量是多少？", decision=decision)
    assert len(called) == 1
    assert called[0]["retrieval"]["fallback_reason"].startswith("admission_declined")

    # 反向：准入放行时应当重放
    ok = retrieval.route(session, "各品类的销售额是多少？", "retail_sales",
                         workspace_id=1, user_id=1, data_source_ids=[seeded["ds"].id])
    assert ok.decision == "replay"
    called.clear()
    skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                           [seeded["ds"].id], workspace_id=1,
                           question="各品类的销售额是多少？", decision=ok)
    assert called == []


# ---- 5. RBAC / Workspace 不被 Replay 绕过 ----

def test_cross_workspace_skill_cannot_run(db, tmp_path, monkeypatch):
    session, Session = db
    seeded = _seed(session, tmp_path, workspace_id=2)
    monkeypatch.setattr(skill_engine, "run_in_sandbox", _fake_sandbox(True))

    summary = skill_engine.run_skill(seeded["skill"].id, seeded["session"].id,
                                    [seeded["ds"].id], workspace_id=1)
    assert summary["ok"] is False
    assert "工作区" in summary["message"]


def test_rerun_and_analyze_permission_checks_unchanged():
    """准入判定不参与授权：它只在权限通过之后决定「怎么算」，不决定「能不能算」。"""
    from backend.auth.context import permission_checker

    viewer = {"permissions": ["skill:read"], "workspace_id": 1, "user_id": 2,
              "username": "v", "role": "viewer"}
    assert permission_checker.has_permission(viewer, "skill:read") is True
    assert permission_checker.has_permission(viewer, "analysis:execute") is False


def test_skill_replay_never_crosses_workspace_in_retrieval(db, tmp_path):
    session, Session = db
    other = _seed(session, tmp_path, workspace_id=9)
    decision = retrieval.route(session, "各品类的销售额是多少？", "retail_sales",
                               workspace_id=1, user_id=1)
    assert decision.decision == "agent"
    assert decision.reason_code == "no_candidate"
    assert other["skill"].id not in decision.candidate_ids
