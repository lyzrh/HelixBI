"""分析运行器：把 backend.agent.graph.stream_analysis 包装为事件回调 + 落库。

关键约束：
- stream_analysis 是同步生成器（内部阻塞于 LLM 与 docker），调用方需在工作
  线程中运行本模块，并通过 on_event(event, data) 回调推送事件（线程安全性
  由调用方保证，路由层用 loop.call_soon_threadsafe 桥接）。
- worker 自行管理 SQLAlchemy Session（不依赖请求作用域的 db）。
"""

import os
import time
import urllib.parse
from pathlib import Path

from backend import config
from backend.agent.acceptance import acceptance_gate
from backend.agent.graph import NODE_LABELS, stream_analysis
from backend.agent.sandbox import run_in_sandbox
from backend.analysis.validation import validate_final
from backend.config import RUNS_DIR
from backend.datasource.service import materialize, needs_materialize
from backend.db import SessionLocal
from backend.models import (
    DataSource, Message, Run, SceneAgent, TokenUsage, jdump, jload,
)
from backend.models import Session as RunSession
from backend.semantic import pack_for_file, render_semantic_prompt, resolve

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
    skill_ids: list[int] | None = None,
    user_context: dict | None = None,
    retrieval: dict | None = None,
) -> dict:
    """在工作线程中执行完整分析流，返回最终落库结果摘要。

    全过程记入 `Run.trace`（分阶段耗时 / LLM 调用与 token / 口径解析 / 校验结论）。
    **失败同样留痕**——排查问题时，失败的那一轮往往才是最需要看的。
    user_context：可信后端解析的 UserContext 字典（可为空 = 匿名旧链路）。
    retrieval：Skill 检索 + 重放准入结论（写进 trace.skill.retrieval，
    用于回答「为什么这次没重放、为什么落到 Agent」）。
    """
    t0 = time.time()
    emit = on_event or (lambda *_: None)
    stages: list[dict] = []
    try:
        # Tool 级权限检查（纵深防御的第二道门）：API 层已拦一道，
        # 这里再拦一道，Viewer 绕过前端直调也无法推进分析链路。
        if user_context:
            from backend.auth.context import permission_checker

            permission_checker.require(user_context, "analysis:execute")
        with SessionLocal() as db:
            files, semantic_block, semantic_meta = _prepare(db, data_source_ids, agent_id,
                                                            emit, user_context)
            history = _history_from_session(db, session_id)
        final, stages = _drive(run_pk, question, files, history, spec,
                               semantic_block, skill_block, emit, session_id=session_id,
                               user_context=user_context)
        trace = _build_trace(run_pk, question, t0, stages, final, semantic_meta,
                             skill_block, skill_ids, user_context, retrieval)
        with SessionLocal() as db:
            summary = _persist_result(db, run_pk, session_id, question, data_source_ids,
                                      final, int((time.time() - t0) * 1000), trace)
        emit("done", summary)
        return summary
    except Exception as exc:
        import traceback
        traceback.print_exc()
        # 越权请求（绕过 API 层直驱链路时）同样要留痕：这是权限的第三道门
        if isinstance(exc, PermissionError):
            from backend.auth import audit

            audit.record("authz.permission_denied", "denied",
                         user_id=(user_context or {}).get("user_id"),
                         username=(user_context or {}).get("username", ""),
                         workspace_id=(user_context or {}).get("workspace_id"),
                         target="analysis:execute",
                         detail={"reason": "runtime_tool_gate", "error": str(exc)[:120]})
        with SessionLocal() as db:
            run = db.get(Run, run_pk)
            if run:
                run.status = "failed"
                run.stderr = str(exc)[-4000:]
                run.trace = jdump({
                    "run_id": run_pk, "question": question, "model": config.MODEL_NAME,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "stages": stages, "final_status": "failed",
                    "validation": {"status": "fail", "checks": [],
                                   "failed": ["runtime_error"]},
                    # 链路整体异常（未进入沙箱）：不是"修不修"的问题，如实标注原因
                    "self_repair": {"policy": config.REPAIR_POLICY, "outcome": "runtime_error",
                                    "repair_status": "not_applicable", "repair_attempts": 0,
                                    "executions": 0, "first_pass_success": False,
                                    "error_category": "runtime", "error_signature": "",
                                    "repair_strategy": "", "repair_reason": str(exc)[:300],
                                    "attempts": [], "repair_latency_ms": _latency_stats([]),
                                    "llm": _llm_stats(run_pk)},
                    "error": str(exc)[:500],
                })
                db.commit()
        emit("error", {"message": str(exc)})
        return {"run_id": run_pk, "ok": False, "message": str(exc)}


# ---- 准备阶段：数据源 → 文件映射 + 语义块 ----

def _prepare(db, data_source_ids: list[int], agent_id: int | None,
             emit, user_context: dict | None = None) -> tuple[dict, str, dict]:
    sources = [db.get(DataSource, i) for i in data_source_ids]
    sources = [s for s in sources if s]
    if not sources:
        raise ValueError("未选择任何有效的数据源，请先在对话中选择或上传数据")

    # 数据作用域（data_scope）：数据源属于工作区；本工作区或历史全局数据可见，
    # 其他工作区的数据源即使拿到 id 也不能进入沙箱。
    if user_context:
        ws_id = user_context.get("workspace_id")
        sources = [s for s in sources
                   if s.workspace_id in (ws_id, None)]
    if not sources:
        raise PermissionError("所选数据源不属于当前工作区，已拒绝访问")

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
            # 挂载用真实存储文件名（含扩展名），生成代码才能选对读取函数
            files[ds.file_name or Path(ds.file_path).name] = ds.file_path

    # 场景 Agent 强制使用其语义包（口径一致性），否则逐数据源取包（去重）
    if agent_id:
        agent = db.get(SceneAgent, agent_id)
        if agent and agent.pack_id:
            return (files, render_semantic_prompt(agent.pack_id),
                    {"packs": [agent.pack_id], "source": "agent"})
    blocks, seen_packs = [], set()
    for ds in sources:
        pack_id = ds.pack_id or pack_for_file(ds.name, jload(ds.columns_json, []))
        if pack_id in seen_packs:
            continue
        seen_packs.add(pack_id)
        blocks.append(render_semantic_prompt(pack_id))
    return (files, "\n\n".join(b for b in blocks if b),
            {"packs": sorted(seen_packs), "source": "sources"})


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

def _drive(run_pk, question, files, history, spec, semantic_block, skill_block, emit,
           session_id=None, user_context: dict | None = None) -> tuple[dict, list[dict]]:
    """驱动图谱并把每个节点的耗时记进 stages（供 trace 使用）。

    `stream_analysis` 每完成一个节点才 yield，因此"上一次 yield 到这一次 yield
    的间隔"就是该节点的耗时。
    """
    final: dict = {}
    stages: list[dict] = []
    emit("step", {"node": "parse_intent", "label": NODE_LABELS["parse_intent"],
                  "status": "running"})
    last = time.time()
    for node, delta, merged in stream_analysis(
        question, files, history=history, spec=spec,
        semantic_block=semantic_block, skill_block=skill_block,
        run_id=run_pk, session_id=session_id, user_context=user_context,
    ):
        now = time.time()
        stage = {
            "node": node,
            "label": EXTENDED_NODE_LABELS.get(node, node),
            "duration_ms": int((now - last) * 1000),
            "status": "done",
        }
        if node == "execute":
            stage["ok"] = bool((merged.get("execution") or {}).get("ok"))
            stage["attempt"] = merged.get("attempts", 0)
        elif node == "generate_code":
            stage["attempt"] = merged.get("attempts", 0)
        elif node == "classify":
            # 自修复决策进 stages：时间线里就能看出"这一轮是修还是不修、按什么类别修"
            stage["error_category"] = merged.get("error_category", "")
            stage["repair_status"] = merged.get("repair_status", "")
            stage["repair_strategy"] = merged.get("repair_strategy", "")
        stages.append(stage)
        last = now

        final = merged
        _emit_node_done(emit, node, delta, merged)
        nxt = _predict_next(node, merged)
        if nxt:
            emit("step", {"node": nxt, "label": EXTENDED_NODE_LABELS.get(nxt, nxt),
                          "status": "running"})
    if not final:
        raise RuntimeError("分析流程未产生任何结果")
    return final, stages


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
    elif node == "classify":
        # 分类结论直接作为可视化步骤的说明文字（前端 step.detail 已支持）
        from backend.agent import repair as repair_mod

        category = merged.get("error_category", "")
        status = merged.get("repair_status", "")
        if status == repair_mod.STATUS_REPAIRING:
            label = repair_mod.CATEGORY_LABELS.get(category, category)
            strategy = merged.get("repair_strategy", "")
            emit("step", {"node": "classify",
                          "label": f"识别为「{label}」→ 定向修复（{strategy}）",
                          "status": "done"})
        else:
            emit("step", {"node": "classify",
                          "label": "自修复决策",
                          "status": "done",
                          "detail": merged.get("repair_reason", "") or
                                    f"状态：{status}"})
    elif node == "summarize":
        emit("answer", {"answer": delta.get("answer", "")})
    elif node == "suggest_followups":
        followups = delta.get("followups") or []
        emit("followups", {"followups": followups})


def _predict_next(node: str, merged: dict) -> str | None:
    """下一步提示（只为 UI 提前显示"正在做什么"，路由真值在 LangGraph 的边里）。"""
    if node == "parse_intent":
        return "generate_code"
    if node == "generate_code":
        return "execute"
    if node == "execute":
        return "classify"
    if node == "classify":
        from backend.agent import repair as repair_mod

        if merged.get("repair_status") == repair_mod.STATUS_REPAIRING:
            return "generate_code"
        return "summarize"
    if node == "summarize":
        return "suggest_followups"
    return None


# ---- 落库 ----

def _persist_result(db, run_pk, session_id, question, data_source_ids,
                    final, duration_ms, trace: dict | None = None) -> dict:
    run = db.get(Run, run_pk)
    if not run:
        raise RuntimeError(f"run {run_pk} 不存在")
    execution = final.get("execution", {})
    charts = _chart_urls(execution)
    # 验收门与 Self-Repair 同源（backend.agent.acceptance）：空结果表不算产物
    ok = acceptance_gate(execution)["passed"]

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
    run.trace = jdump(trace or {})

    meta = {
        "run_id": run_pk, "charts": charts, "tables": execution.get("tables") or {},
        "followups": final.get("followups") or [], "spec": final.get("spec") or {},
        "attempts": final.get("attempts", 0), "ok": ok, "code": final.get("code", ""),
        "plan": final.get("plan", ""),
        "self_repair": {
            "outcome": (trace or {}).get("self_repair", {}).get("outcome", ""),
            "repair_attempts": (trace or {}).get("self_repair", {}).get("repair_attempts", 0),
            "error_category": (trace or {}).get("self_repair", {}).get("error_category", ""),
        },
    }
    msg = Message(session_id=session_id, role="assistant",
                  content=final.get("answer", "") or "（分析未产生结论，请查看执行日志）",
                  meta=jdump(meta))
    db.add(msg)
    db.flush()
    run.message_id = msg.id
    db.commit()
    return {"run_id": run_pk, "message_id": msg.id, "ok": ok, "duration_ms": duration_ms}


# ---- 可观测性 ----

def _build_trace(run_pk, question, t0, stages, final, semantic_meta,
                 skill_block, skill_ids, user_context=None, retrieval=None) -> dict:
    """组装单次运行的可观测记录（写进 Run.trace，前端时间线与评估共用同一份）。"""
    execution = final.get("execution") or {}
    attempts = final.get("attempts", 0) or 0
    packs = list((semantic_meta or {}).get("packs") or [])
    resolved = resolve(question, packs[0]) if packs else {}
    validation = validate_final(final)

    skill_trace = {
        "matched_ids": list(skill_ids or []),
        "hit": bool(skill_block),
        "mode": "few_shot" if skill_block else "fresh",
    }
    if retrieval is not None:
        skill_trace["retrieval"] = retrieval
    if user_context:
        # 触发者身份（谁发起的分析）——审计与排障用，与权限判定分离
        skill_trace["actor"] = {
            "user_id": user_context.get("user_id"),
            "workspace_id": user_context.get("workspace_id"),
            "role": user_context.get("role"),
        }

    llm_stats = _llm_stats(run_pk)
    return {
        "run_id": run_pk,
        "question": question,
        "model": config.MODEL_NAME,
        "latency_ms": int((time.time() - t0) * 1000),
        "stages_ms": sum(s.get("duration_ms", 0) for s in stages),
        "stages": stages,
        "intent": final.get("spec") or {},
        "semantic": {
            "packs": packs,
            "source": (semantic_meta or {}).get("source", ""),
            "resolved_metrics": [m["name"] for m in resolved.get("metrics", [])],
            "resolved_dimensions": [d["name"] for d in resolved.get("dimensions", [])],
            "analysis_type": resolved.get("analysis_type", "unknown"),
            "resolver_confidence": resolved.get("confidence", 0.0),
        },
        "skill": skill_trace,
        "execution": {
            "ok": bool(execution.get("ok")),
            "attempts": attempts,
            "repair_count": max(attempts - 1, 0),
            "sandboxed": True,
        },
        # Self-Repair V2 的观测段：能回答"为什么这个 Agent 修了 2 次才成功"
        "self_repair": build_self_repair_trace(final, llm_stats),
        "llm": llm_stats,
        "validation": validation,
        "final_status": "done" if validation["status"] != "fail" else "failed",
    }


def build_self_repair_trace(final: dict, llm: dict | None = None) -> dict:
    """把 Self-Repair 的决策链写进 trace（确定性、只读 state 字段，零额外开销）。

    回答四类问题：
    1. **成功了几次就成**——`first_pass_success`（首轮即通过验收）；
    2. **为什么修**——`error_category / error_signature / repair_strategy` 与每轮的错误序列；
    3. **修了多久**——`attempts[].duration_ms` 与 `repair_latency_ms` 的 p50 / p95
       （口径：一次 repair 决策 → 下一轮执行结论，含 LLM 重新生成 + 沙箱执行）；
    4. **最后怎么收场**——`outcome`（success / exhausted / fallback）+ `repair_status`
       + `stop_reason`，以及整轮的 LLM calls / tokens / cost。
    """
    from backend.agent import repair as repair_mod

    execution = (final or {}).get("execution") or {}
    passed = acceptance_gate(execution)["passed"]
    attempts = final.get("attempts", 0) or 0
    history = [dict(h) for h in (final.get("repair_history") or [])]
    errors = list(final.get("previous_errors") or [])
    status = final.get("repair_status") or repair_mod.STATUS_NOT_NEEDED
    last_error = errors[-1] if errors else {}
    durations = [h.get("duration_ms", 0) for h in history
                 if h.get("duration_ms") is not None]

    if passed:
        outcome = "success"
    elif status in (repair_mod.STATUS_EXHAUSTED, repair_mod.STATUS_REPEATED):
        outcome = "exhausted"
    else:
        outcome = "fallback"   # 环境不可用等不可修复情形 → 结构化失败收尾

    policy_name = final.get("repair_policy") or getattr(config, "REPAIR_POLICY", "v2")
    return {
        "policy": policy_name,
        "first_pass_success": bool(passed and attempts <= 1),
        "outcome": outcome,
        "repair_status": status,
        "repair_attempts": len(history),
        "max_fix_attempts": config.MAX_FIX_ATTEMPTS,
        "executions": attempts,
        "error_category": last_error.get("category", ""),
        "error_label": last_error.get("label", ""),
        "error_signature": last_error.get("signature", ""),
        "repair_strategy": history[-1].get("strategy", "") if history else "",
        "repair_reason": final.get("repair_reason", ""),
        "repeated_error": bool(final.get("repair_repeat_kind")),
        "repeat_kind": final.get("repair_repeat_kind", ""),
        "error_chain": [e.get("label") or e.get("category", "") for e in errors],
        "errors": repair_mod.summarize_errors(errors),
        "attempts": [
            {"attempt": h.get("attempt"), "trigger_category": h.get("trigger_category"),
             "trigger_signature": h.get("trigger_signature"),
             "strategy": h.get("strategy"), "focus": h.get("focus"),
             "duration_ms": h.get("duration_ms"), "ok": h.get("ok"),
             "result_category": h.get("result_category")}
            for h in history
        ],
        "repair_latency_ms": _latency_stats(durations),
        "llm": {
            "calls": (llm or {}).get("calls", 0),
            "input_tokens": (llm or {}).get("input_tokens", 0),
            "output_tokens": (llm or {}).get("output_tokens", 0),
            "cost_usd": (llm or {}).get("cost_usd", 0.0),
        },
    }


def _latency_stats(samples: list[int]) -> dict:
    """修复耗时统计（毫秒）：p50 / p95 / 总和，用于回答"自修复的延迟代价"。"""
    if not samples:
        return {"count": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0, "total_ms": 0}

    ordered = sorted(samples)

    def q(ratio: float) -> int:
        idx = min(int(ratio * (len(ordered) - 1) + 0.5), len(ordered) - 1)
        return ordered[idx]

    return {"count": len(ordered), "p50_ms": q(0.50), "p95_ms": q(0.95),
            "max_ms": ordered[-1], "total_ms": sum(ordered)}



def _llm_stats(run_pk: int) -> dict:
    """本轮 LLM 用量：调用次数 = token_usages 的记录条数（链路已有埋点，直接汇总）。

    这也是「Skill 重放不调 LLM」的**可验证证据**：重放轮的 calls 必须是 0。
    """
    empty = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0, "by_node": {}}
    try:
        with SessionLocal() as db:
            rows = db.query(TokenUsage).filter(TokenUsage.run_id == run_pk).all()
    except Exception:  # noqa: BLE001 — 统计失败不得影响主流程
        return empty
    if not rows:
        return empty
    by_node: dict[str, int] = {}
    for row in rows:
        by_node[row.node] = by_node.get(row.node, 0) + 1
    return {
        "calls": len(rows),
        "input_tokens": sum(r.input_tokens or 0 for r in rows),
        "output_tokens": sum(r.output_tokens or 0 for r in rows),
        "cost_usd": round(sum(r.cost_usd or 0.0 for r in rows), 6),
        "by_node": by_node,
    }


# ---- 工具 ----

def run_artifact_key(run_dir: str) -> str:
    """产物 URL 里的那段 key（`/runs/<key>/out/<chart>`）。

    `Run.run_dir` 指向运行的 **out** 目录（`runs/<key>/out`），所以 URL 的 key 是它的父目录名；
    生成（`_chart_urls`）与校验（`visible_run_dirs` / 文件下发路由）共用这个函数，
    避免两处各写一套导致图表 URL 与鉴权路由对不上。
    """
    path = Path(str(run_dir or ""))
    return path.parent.name if path.name == "out" else path.name


def _chart_urls(execution: dict) -> list[str]:
    """runs 目录内图表 → 带鉴权的相对 URL（key 可能含中文，必须 quote）。"""
    run_dir = execution.get("run_dir")
    charts = execution.get("charts") or []
    if not run_dir or not charts:
        return []
    rid = urllib.parse.quote(run_artifact_key(run_dir))
    return [f"/runs/{rid}/out/{urllib.parse.quote(c)}" for c in charts]


def chart_url_to_path(url: str) -> Path | None:
    """/runs/{run_dir}/out/{name} → 宿主绝对路径（仪表板 / 单轮导出用）。

    安全（Security Hardening V1）：`url` 可能来自客户端提交的仪表板 payload，
    因此必须**拒绝一切越过 RUNS_DIR 的路径**（`../`、绝对路径、URL 编码变体），
    否则导出会变成任意文件读取（连 SQLite 元数据库都能被读出来）。
    越界一律返回 None，由调用方按"没有这张图"处理。
    """
    unquoted = urllib.parse.unquote(str(url or ""))
    rel = unquoted[len("/runs/"):] if unquoted.startswith("/runs/") else unquoted
    rel = rel.lstrip("/")
    if not rel or rel.startswith("~"):
        return None
    base = RUNS_DIR.resolve()
    try:
        target = (base / rel).resolve()
    except (OSError, RuntimeError):
        return None
    if os.path.commonpath([str(base), str(target)]) != str(base):
        return None
    return target


def visible_run_dirs(db, workspace_id: int | None) -> set[str]:
    """本工作区可见的 Run 产物目录名集合（导出时用来判定"这张图是不是我们的"）。

    产物 URL 里的第一段是**运行目录名**（`run_id` 字符串，不是数据库主键），
    因此归属校验必须经 `Run → Session.workspace_id → run_dir` 反查，而不是只看路径形状。
    """
    names: set[str] = set()
    try:
        rows = (db.query(Run.run_dir)
                .join(RunSession, Run.session_id == RunSession.id)
                .filter((RunSession.workspace_id == workspace_id)
                        | (RunSession.workspace_id.is_(None))).all())
    except Exception:  # noqa: BLE001 — 查不到就当作"没有可见产物"，宁严不松
        return names
    for (run_dir,) in rows:
        if run_dir:
            names.add(run_artifact_key(run_dir))
    return names

