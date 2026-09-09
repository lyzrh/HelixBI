"""分析运行器：把 app.graph.stream_analysis 包装为事件回调 + 落库。

关键约束：
- stream_analysis 是同步生成器（内部阻塞于 LLM 与 docker），调用方需在工作
  线程中运行本模块，并通过 on_event(event, data) 回调推送事件（线程安全性
  由调用方保证，路由层用 loop.call_soon_threadsafe 桥接）。
- worker 自行管理 SQLAlchemy Session（不依赖请求作用域的 db）。
"""

import time
import urllib.parse
from pathlib import Path

from app.graph import NODE_LABELS, stream_analysis
from app.sandbox import run_in_sandbox
from app.semantic import pack_for_file, render_semantic_prompt
from backend.config import RUNS_DIR
from backend.db import SessionLocal
from backend.models import DataSource, Message, Run, SceneAgent, jdump, jload
from backend.services.datasource import materialize, needs_materialize

EXTENDED_NODE_LABELS = {**NODE_LABELS, "materialize": "缓存数据库数据", "skill": "复用分析 Skill"}


def run_analysis_stream(
    run_pk: int,
    session_id: int,
    question: str,
    data_source_ids: list[int],
    spec: dict | None,
    skill_block: str = "",
    agent_id: int | None = None,
    on_event=None,
) -> dict:
    """在工作线程中执行完整分析流，返回最终落库结果摘要。"""
    t0 = time.time()
    emit = on_event or (lambda *_: None)
    try:
        with SessionLocal() as db:
            files, semantic_block = _prepare(db, data_source_ids, agent_id, emit)
            history = _history_from_session(db, session_id)
        final = _drive(run_pk, question, files, history, spec,
                       semantic_block, skill_block, emit, session_id=session_id)
        with SessionLocal() as db:
            summary = _persist_result(db, run_pk, session_id, question,
                                      data_source_ids, final, int((time.time() - t0) * 1000))
        emit("done", summary)
        return summary
    except Exception as exc:
        import traceback
        traceback.print_exc()
        with SessionLocal() as db:
            run = db.get(Run, run_pk)
            if run:
                run.status = "failed"
                run.stderr = str(exc)[-4000:]
                db.commit()
        emit("error", {"message": str(exc)})
        return {"run_id": run_pk, "ok": False, "message": str(exc)}


# ---- 准备阶段：数据源 → 文件映射 + 语义块 ----

def _prepare(db, data_source_ids: list[int], agent_id: int | None, emit) -> tuple[dict, str]:
    sources = [db.get(DataSource, i) for i in data_source_ids]
    sources = [s for s in sources if s]
    if not sources:
        raise ValueError("未选择任何有效的数据源，请先在对话中选择或上传数据")

    files: dict[str, str] = {}
    for ds in sources:
        if ds.type == "db":
            if needs_materialize(ds):
                emit("step", {"node": "materialize", "label": EXTENDED_NODE_LABELS["materialize"],
                              "status": "running"})
                materialize(ds, ds.materialized_table)
                db.commit()
                emit("step", {"node": "materialize", "label": EXTENDED_NODE_LABELS["materialize"],
                              "status": "done"})
            if not ds.materialized_path:
                raise ValueError(f"数据库源「{ds.name}」尚未物化，请先在数据源页选择表并缓存")
            files[f"{ds.materialized_table}.parquet"] = ds.materialized_path
        else:
            files[ds.name] = ds.file_path

    # 场景 Agent 强制使用其语义包（口径一致性），否则逐文件推断
    if agent_id:
        agent = db.get(SceneAgent, agent_id)
        if agent and agent.pack_id:
            return files, render_semantic_prompt(agent.pack_id)
    blocks = [render_semantic_prompt(pack_for_file(name)) for name in files]
    return files, "\n\n".join(b for b in blocks if b)


def _history_from_session(db, session_id: int, limit: int = 3) -> list[dict]:
    msgs = (db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.id.desc())
            .limit(limit * 2).all())
    history = []
    for m in reversed(msgs):
        if m.role == "user":
            history.append({"question": m.content, "answer": ""})
        elif history:
            history[-1]["answer"] = m.content
    return history


# ---- 驱动阶段：事件映射 ----

def _drive(run_pk, question, files, history, spec, semantic_block, skill_block, emit, session_id=None) -> dict:
    final: dict = {}
    emit("step", {"node": "parse_intent", "label": NODE_LABELS["parse_intent"],
                  "status": "running"})
    for node, delta, merged in stream_analysis(
        question, files, history=history, spec=spec,
        semantic_block=semantic_block, skill_block=skill_block,
        run_id=run_pk, session_id=session_id,
    ):
        final = merged
        _emit_node_done(emit, node, delta, merged)
        nxt = _predict_next(node, merged)
        if nxt:
            emit("step", {"node": nxt, "label": EXTENDED_NODE_LABELS.get(nxt, nxt),
                          "status": "running"})
    if not final:
        raise RuntimeError("分析流程未产生任何结果")
    return final


def _emit_node_done(emit, node, delta, merged):
    label = EXTENDED_NODE_LABELS.get(node, node)
    emit("step", {"node": node, "label": label, "status": "done"})
    if node == "parse_intent" and delta.get("spec"):
        emit("spec", {"spec": delta["spec"]})
    elif node == "generate_code":
        emit("code", {"plan": delta.get("plan", ""), "code": delta.get("code", ""),
                      "attempt": merged.get("attempts", 0)})
    elif node == "execute":
        execution = merged.get("execution", {})
        emit("execute", {
            "ok": execution.get("ok"),
            "stdout": (execution.get("stdout") or "")[-2000:],
            "stderr": (execution.get("stderr") or "")[-2000:],
            "charts": _chart_urls(execution),
            "tables": execution.get("tables") or {},
            "text": execution.get("text") or "",
        })
    elif node == "summarize":
        emit("answer", {"answer": delta.get("answer", "")})
    elif node == "suggest_followups":
        followups = delta.get("followups") or []
        emit("followups", {"followups": followups})


def _predict_next(node: str, merged: dict) -> str | None:
    if node == "parse_intent":
        return "generate_code"
    if node == "generate_code":
        return "execute"
    if node == "execute":
        execution = merged.get("execution", {})
        has_output = any([execution.get("text"), execution.get("tables"),
                          execution.get("charts")])
        if execution.get("ok") and has_output:
            return "summarize"
        from app.config import MAX_FIX_ATTEMPTS
        if merged.get("attempts", 0) <= MAX_FIX_ATTEMPTS:
            return "generate_code"
        return "summarize"
    if node == "summarize":
        return "suggest_followups"
    return None


# ---- 落库 ----

def _persist_result(db, run_pk, session_id, question, data_source_ids,
                    final, duration_ms) -> dict:
    run = db.get(Run, run_pk)
    if not run:
        raise RuntimeError(f"run {run_pk} 不存在")
    execution = final.get("execution", {})
    charts = _chart_urls(execution)
    ok = bool(execution.get("ok")) and bool(
        execution.get("text") or execution.get("tables") or execution.get("charts"))

    run.status = "done" if ok else "failed"
    run.spec = jdump(final.get("spec") or {})
    run.plan = final.get("plan", "")
    run.code = final.get("code", "")
    run.attempts = final.get("attempts", 0)
    run.ok = ok
    run.stdout = (execution.get("stdout") or "")[-4000:]
    run.stderr = (execution.get("stderr") or "")[-4000:]
    run.run_dir = execution.get("run_dir", "")
    run.charts = jdump(charts)
    run.tables = jdump(execution.get("tables") or {})
    run.answer = final.get("answer", "")
    run.followups = jdump(final.get("followups") or [])
    run.duration_ms = duration_ms

    meta = {
        "run_id": run_pk, "charts": charts, "tables": execution.get("tables") or {},
        "followups": final.get("followups") or [], "spec": final.get("spec") or {},
        "attempts": final.get("attempts", 0), "ok": ok, "code": final.get("code", ""),
        "plan": final.get("plan", ""),
    }
    msg = Message(session_id=session_id, role="assistant",
                  content=final.get("answer", "") or "（分析未产生结论，请查看执行日志）",
                  meta=jdump(meta))
    db.add(msg)
    db.flush()
    run.message_id = msg.id
    db.commit()
    return {"run_id": run_pk, "message_id": msg.id, "ok": ok, "duration_ms": duration_ms}


# ---- 工具 ----

def _chart_urls(execution: dict) -> list[str]:
    """runs 目录内图表 → 相对静态 URL（run_id 含中文必须 quote）。"""
    run_dir = execution.get("run_dir")
    charts = execution.get("charts") or []
    if not run_dir or not charts:
        return []
    out_dir = Path(run_dir)
    rid = urllib.parse.quote(out_dir.parent.name)
    return [f"/runs/{rid}/out/{urllib.parse.quote(c)}" for c in charts]


def chart_url_to_path(url: str) -> Path:
    """/runs/{rid}/out/{name} → 宿主绝对路径（仪表板导出用）。"""
    unquoted = urllib.parse.unquote(url)
    rel = unquoted[len("/runs/"):] if unquoted.startswith("/runs/") else unquoted
    return RUNS_DIR / rel.lstrip("/")
