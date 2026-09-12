"""可观测性测试：Run.trace 的结构、数据库迁移与「重放零 LLM 调用」的证据。"""

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.db as db_module
from backend.analysis import runtime
from backend.analysis.validation import validate_final
from backend.models import Base, Run, TokenUsage
from backend.skills.engine import _replay_trace


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _make_execution(tmp_path, *, ok=True, text="结论", tables=None,
                    chart_files=(), chart_refs=None, stderr=""):
    out = Path(tmp_path) / "out"
    out.mkdir(parents=True, exist_ok=True)
    for name in chart_files:
        (out / name).write_bytes(b"png")
    return {
        "ok": ok, "stdout": "", "stderr": stderr, "text": text,
        "tables": {"t": [{"a": 1}]} if tables is None else tables,
        "charts": list(chart_files if chart_refs is None else chart_refs),
        "run_dir": str(tmp_path),
    }


# ---- 结果验收 ----

def test_validate_final_ok(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, chart_files=("c.png",)),
        "answer": "有结论",
    })
    assert result["status"] == "ok"
    assert result["failed"] == []


def test_validate_final_fails_when_execution_failed(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, ok=False, stderr="boom"),
        "answer": "",
    })
    assert result["status"] == "fail"
    assert "execution_ok" in result["failed"]


def test_validate_final_fails_without_any_artifact(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, text="", tables={}),
        "answer": "有结论",
    })
    assert result["status"] == "fail"
    assert "has_artifact" in result["failed"]


def test_validate_final_warns_on_missing_chart_file(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, chart_refs=("ghost.png",)),
        "answer": "有结论",
    })
    assert result["status"] == "warn"
    assert "charts_on_disk" in result["failed"]


def test_validate_final_warns_on_empty_answer(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, chart_files=("c.png",)),
        "answer": "",
    })
    assert result["status"] == "warn"
    assert "answer_present" in result["failed"]


def test_validate_final_warns_on_stderr_with_success(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, chart_files=("c.png",),
                                     stderr="UserWarning: ..."),
        "answer": "有结论",
    })
    assert result["status"] == "warn"
    assert "clean_stderr" in result["failed"]


def test_validate_final_detects_malformed_table(tmp_path):
    result = validate_final({
        "execution": _make_execution(tmp_path, chart_files=("c.png",),
                                     tables={"bad": ["not-a-dict"]}),
        "answer": "有结论",
    })
    assert "tables_wellformed" in result["failed"]


# ---- 重放路径的可观测记录 ----

def test_replay_trace_proves_zero_llm_calls(tmp_path):
    """这是「Skill 重放绕过 LLM」的可验证证据。"""
    skill = SimpleNamespace(id=42, name="品类销售额", question="各品类销售额",
                            pack_id="retail_sales")
    execution = _make_execution(tmp_path, chart_files=("c.png",))

    trace = _replay_trace(7, skill, execution, "结论", True, 420)

    assert trace["skill"]["mode"] == "replay"
    assert trace["llm"]["calls"] == 0
    assert trace["llm"]["input_tokens"] == 0 and trace["llm"]["output_tokens"] == 0
    assert trace["validation"]["status"] == "ok"
    assert trace["final_status"] == "done"
    assert trace["execution"]["sandboxed"] is True


def test_trace_is_json_serializable(tmp_path):
    skill = SimpleNamespace(id=1, name="x", question="q", pack_id="retail_sales")
    trace = _replay_trace(1, skill, _make_execution(tmp_path, chart_files=("c.png",)),
                          "结论", True, 100)
    assert json.loads(json.dumps(trace, ensure_ascii=False))["run_id"] == 1


# ---- 落库与迁移 ----

def test_run_to_dict_exposes_trace():
    engine = _engine()
    with sessionmaker(bind=engine)() as session:
        run = Run(session_id=1, question="q", trace=json.dumps({"run_id": 1, "llm": {"calls": 0}}))
        session.add(run)
        session.commit()
        payload = run.to_dict()
        assert payload["trace"]["llm"]["calls"] == 0


def test_run_to_dict_tolerates_empty_trace():
    engine = _engine()
    with sessionmaker(bind=engine)() as session:
        run = Run(session_id=1, question="q")
        session.add(run)
        session.commit()
        assert run.to_dict()["trace"] == {}


def test_runs_table_declares_trace_column():
    assert "trace" in Run.__table__.columns


def test_migration_covers_trace_column():
    """迁移必须存在：create_all 不会给已存在的表补列。"""
    source = Path(db_module.__file__).read_text(encoding="utf-8")
    assert "runs ADD COLUMN trace" in source


# ---- LLM 用量汇总 ----

def test_llm_stats_aggregates_by_run(monkeypatch):
    engine = _engine()
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(runtime, "SessionLocal", Session)
    with Session() as session:
        session.add(TokenUsage(run_id=7, node="parse_intent", input_tokens=100, output_tokens=20))
        session.add(TokenUsage(run_id=7, node="generate_code", input_tokens=200, output_tokens=50))
        session.add(TokenUsage(run_id=8, node="summarize", input_tokens=10, output_tokens=5))
        session.commit()

    stats = runtime._llm_stats(7)
    assert stats["calls"] == 2
    assert stats["input_tokens"] == 300
    assert stats["output_tokens"] == 70
    assert stats["by_node"] == {"parse_intent": 1, "generate_code": 1}


def test_llm_stats_returns_zero_without_records(monkeypatch):
    monkeypatch.setattr(runtime, "SessionLocal", sessionmaker(bind=_engine()))
    assert runtime._llm_stats(999)["calls"] == 0


def test_llm_stats_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(runtime, "SessionLocal", _boom)
    stats = runtime._llm_stats(1)
    assert stats["calls"] == 0 and stats["cost_usd"] == 0.0
