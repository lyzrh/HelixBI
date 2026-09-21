"""Skill 引擎：沉淀 / 匹配 / few-shot 渲染 / 双模式运行（replay | 重新生成）。

FineBI NEXT Skill 思路的轻量实现：验证过的分析路径沉淀为可复用资产。
- 列集合匹配 → 直接重放代码（跳过 LLM，秒回）
- 不匹配 → few-shot 注入 generate_code 重新生成

匹配与准入分两层（feat/skill-retrieval-v2）：
- `match_skills()`：**召回**候选（供 few-shot 注入），返回 Skill 对象列表；
- `route_query()`：**召回 + 打分 + 准入**，返回可解释的 AdmissionDecision，
  由调用方决定 Replay 还是走完整 Agent。
具体打分/策略实现在 `backend.skills.retrieval`（确定性、零 token）。
"""

import re
import time
import uuid
from pathlib import Path

from backend.agent.sandbox import run_in_sandbox
from backend.db import SessionLocal
from backend.models import (
    DataSource, Message, Run, Session as DbSession, Skill,
    jdump, jload,
)
from backend.analysis.runtime import _chart_urls
from backend.skills import retrieval

PLACEHOLDER = "{name}"


# ---- 沉淀 ----

def capture_from_run(db, run_id: int, name: str, description: str = "",
                     tags: list[str] | None = None,
                     workspace_id: int | None = None,
                     user_id: int | None = None) -> dict:
    run = db.get(Run, run_id)
    if not run:
        raise ValueError("运行记录不存在")
    if not run.ok:
        raise ValueError("只能从成功的运行沉淀 Skill")
    columns: set[str] = set()
    pack_id = None
    signature = None
    for ds_id in jload(run.data_source_ids, []):
        ds = db.get(DataSource, ds_id)
        if ds:
            columns.update(jload(ds.columns_json, []))
            pack_id = pack_id or ds.pack_id
            # 数据源指纹：语义包 + 文件类型（数据库源按 parquet 计）
            ext = ("parquet" if ds.type == "db"
                   else Path(ds.file_name or ds.file_path or "").suffix)
            signature = retrieval.skill_signature(pack_id, f"x{ext}")
    skill = Skill(
        name=name, description=description or run.question[:100],
        pack_id=pack_id, question=run.question, spec=run.spec,
        code=run.code, columns_json=jdump(sorted(columns)),
        source_run_id=run_id, tags=jdump(tags or []),
        datasource_key=signature,
        scope="user" if (workspace_id is None and user_id is not None) else "workspace",
        workspace_id=workspace_id, user_id=user_id,
    )
    db.add(skill)
    db.flush()
    db.commit()
    return skill.to_dict()


# ---- 匹配 ----

def _tokenize(text: str) -> set[str]:
    """中文 2-gram + 英文/数字词（保留旧入口，实现统一到 retrieval.tokenize）。"""
    return set(retrieval.tokenize(text))


def match_skills(db, question: str, pack_id: str | None = None,
                 limit: int = 2, min_score: int = 2,
                 workspace_id: int | None = None,
                 user_id: int | None = None,
                 ctx=None, policy: str = "v2") -> list:
    """Skill **召回**（Retrieval 阶段，不含准入）：返回 Top-K 候选 Skill。

    为什么还保留这个入口：分析链路需要把候选 Skill 作为 few-shot 注入 prompt，
    这与「能不能重放」是两个问题。准入判定请用 `route_query()`。

    V2 打分在 `backend.skills.retrieval`：语义相似度 / 指标 / 维度 / 分析类型 /
    数据源兼容 / 历史成功率六路可解释信号，权重配置化（`config.SKILL_RETRIEVAL_WEIGHTS`）。
    作用域隔离（global + 本工作区 + 本人）不变。
    """
    if policy == "v1":
        # 基准对比与回归留档：用冻结的 V1 词面打分
        skills = retrieval.visible_skills(db, workspace_id, user_id)
        intent = retrieval.query_intent(question, pack_id)
        return _legacy_skills(skills, retrieval.legacy_rank(skills, question, pack_id, intent),
                              limit)
    skills = retrieval.visible_skills(db, workspace_id, user_id)
    candidates, _ = retrieval.retrieve(
        db, question, pack_id=pack_id, workspace_id=workspace_id, user_id=user_id,
        ctx=ctx, k=max(limit, 2), policy=policy, skills=skills)
    # few-shot 注入的相关性下限：与问题无关的 Skill 不进 prompt（召回阶段不做这个裁剪，
    # 否则会连带把 Recall 一起压低）
    from backend import config

    floor = config.SKILL_RETRIEVAL_MIN_SCORE
    picked = [c for c in candidates if c.final_score >= floor][:limit]
    by_id = {s.id: s for s in skills}
    return [by_id[c.skill_id] for c in picked if c.skill_id in by_id]


def _legacy_skills(skills: list, ranked: list, limit: int) -> list:
    by_id = {s.id: s for s in skills}
    return [by_id[c.skill_id] for c in ranked[:limit] if c.skill_id in by_id]


def route_query(db, question: str, pack_id: str | None = None,
                data_source_ids: list[int] | None = None,
                workspace_id: int | None = None, user_id: int | None = None,
                policy: str = "v2") -> retrieval.AdmissionDecision:
    """完整路由：Retrieval → Admission → Replay / Agent（对外主入口）。"""
    return retrieval.route(
        db, question, pack_id=pack_id, workspace_id=workspace_id, user_id=user_id,
        data_source_ids=data_source_ids, policy=policy)


def retrieval_trace(decision: retrieval.AdmissionDecision) -> dict:
    """准入结论 → 可观测记录（写进 Run.trace.skill，回答「为什么没重放」）。"""
    return {"retrieval": decision.to_dict()}


def render_skill_prompt(skills: list) -> str:
    if not skills:
        return ""
    lines = ["## 相似分析案例（few-shot 参考：仅借鉴分析思路、口径与代码风格，"
             "列名以本次数据概况为准，不要照抄案例中的文件名与列名）"]
    for s in skills:
        lines.append(f"### 案例：{s.name}")
        lines.append(f"问题：{s.question}")
        lines.append("```python")
        lines.append(s.code)
        lines.append("```")
    return "\n\n".join(lines) + "\n\n"


# ---- 运行（双模式）----

def run_skill(skill_pk: int, session_id: int | None, data_source_ids: list[int],
              on_event=None, workspace_id: int | None = None,
              question: str | None = None, decision=None) -> dict:
    """在工作线程中运行 Skill，返回落库摘要。

    两种模式（由 `_replay_gate` 决定）：
    - **replay**：结构 + 语义守卫全部通过 → 直接跑已验证代码，零 LLM 调用；
    - **few-shot 重新生成**：守卫不通过 → 把 Skill 当参考案例，走完整 Agent。

    安全兜底：**重放执行失败不再直接返回错误**，而是自动 fallback 到完整 Agent
    （`reason_code=replay_failed`），用户拿到的是结果而不是报错。

    `decision`：`retrieval.AdmissionDecision`（可选）。传入时严格遵循准入结论——
    decision=agent 或选中的不是本 Skill 时，一律不重放。
    """
    t0 = time.time()
    emit = on_event or (lambda *_: None)
    skipped_reason = ""
    try:
        with SessionLocal() as db:
            skill = db.get(Skill, skill_pk)
            if not skill or not skill.enabled:
                raise ValueError("Skill 不存在或已禁用")
            # Skill 作用域：本工作区 / global / 本人 可运行，其余拒绝
            if workspace_id is not None:
                visible = (skill.scope == "global"
                           or skill.workspace_id == workspace_id
                           or skill.workspace_id is None)  # 历史数据视为全局
                if not visible:
                    raise PermissionError("Skill 不属于当前工作区")
            if session_id is None:
                session = DbSession(title=f"Skill：{skill.name}",
                                    workspace_id=workspace_id)
                db.add(session)
                db.flush()
                session_id = session.id
                db.commit()

            if not data_source_ids:
                # 未指定则用 Skill 关联语义包下的第一个数据源（限本工作区）
                q = db.query(DataSource).filter(DataSource.pack_id == skill.pack_id)
                sources = (q.filter(DataSource.workspace_id == workspace_id).all()
                           if workspace_id is not None else q.all())
                if not sources:
                    q2 = db.query(DataSource)
                    sources = (q2.filter(DataSource.workspace_id == workspace_id).all()
                               if workspace_id is not None else q2.order_by(DataSource.id).all())
                if not sources:
                    raise ValueError("没有可用数据源，请先上传或连接数据")
                data_source_ids = [sources[0].id]

            files = _build_files(db, data_source_ids)
            columns_now: set[str] = set()
            for ds_id in data_source_ids:
                ds = db.get(DataSource, ds_id)
                if ds:
                    columns_now.update(jload(ds.columns_json, []))
            replay, skipped_reason = _replay_gate(
                db, skill, data_source_ids, files, columns_now, decision)
            run_question = question or skill.question

            emit("step", {"node": "skill", "label": f"运行 Skill「{skill.name}」",
                          "status": "running",
                          "detail": "重放已验证代码" if replay else "参考案例重新生成"})

            if replay:
                summary = _replay(db, skill, session_id, data_source_ids, files, emit, t0,
                                  question=run_question, retrieval=_decision_dict(decision))
                if summary.get("ok"):
                    emit("step", {"node": "skill", "label": f"运行 Skill「{skill.name}」",
                                  "status": "done"})
                    emit("done", summary)
                    return summary
                # 重放失败 → 安全回退
                skipped_reason = "replay_failed"
                emit("step", {"node": "skill", "label": f"运行 Skill「{skill.name}」",
                              "status": "done",
                              "detail": "重放执行失败，已回退到 Agent 重新生成"})

        # 不重放：列不匹配 / 准入拒绝 / 重放失败 → few-shot 重新生成
        #
        # 注意：所有需要的字段必须在 session 内取出。`_create_run()` 里有一次 commit，
        # 会把 ORM 实例的已加载属性置为 expired；出块后再访问就会抛
        # DetachedInstanceError（旧实现就在这里断了 few-shot 兜底链路）。
        from backend.analysis import runtime as analysis_runner
        with SessionLocal() as db:
            skill = db.get(Skill, skill_pk)
            skill_spec = jload(skill.spec)
            skill_id = skill.id
            skill_block = render_skill_prompt([skill])
            run_pk = _create_run(db, session_id, run_question, data_source_ids, skill_id)
        summary = analysis_runner.run_analysis_stream(
            run_pk, session_id, run_question, data_source_ids,
            skill_spec, skill_block, None, emit,
            skill_ids=[skill_id],
            retrieval=_fallback_record(decision, skipped_reason),
        )
        with SessionLocal() as db:
            s2 = db.get(Skill, skill_id)
            s2.use_count += 1
            if summary.get("ok"):
                s2.success_count += 1
            db.commit()
        return summary
    except Exception as exc:
        import traceback
        traceback.print_exc()
        emit("error", {"message": str(exc)})
        return {"ok": False, "message": str(exc)}


def _decision_dict(decision) -> dict | None:
    return decision.to_dict() if decision is not None else None


def _fallback_record(decision, reason: str) -> dict | None:
    """Agent 兜底路径的检索记录：说明「为什么最终 fallback 到 Agent」。"""
    if decision is None and not reason:
        return None
    record = _decision_dict(decision) or {}
    if reason:
        record["fallback_reason"] = reason
    return record


def _replay_gate(db, skill, data_source_ids: list[int], files: dict,
                 columns_now: set[str], decision=None) -> tuple[bool, str]:
    """重放守卫：结构兼容 + 准入结论 + 数据源指纹，三者都过才允许重放。

    返回 (是否可重放, 拒绝原因)。拒绝原因是可解释的字符串，直接进 trace。
    """
    if decision is not None:
        if decision.decision != "replay":
            return False, f"admission_declined:{decision.reason_code}"
        if decision.selected_skill_id not in (None, skill.id):
            return False, "admission_selected_other_skill"
    if not _columns_match(jload(skill.columns_json, []), columns_now):
        return False, "column_mismatch"
    if not _reader_compatible(skill.code, list(files)):
        return False, "reader_mismatch"
    # 数据源指纹：整体换了一批数据（语义包 / 文件类型变化）不得盲目重放
    ctx = retrieval.data_context(db, data_source_ids)
    stored = getattr(skill, "datasource_key", "") or ""
    if stored and stored != ctx.signature:
        return False, f"datasource_changed:{stored}->{ctx.signature}"
    return True, ""


def _create_run(db, session_id, question, data_source_ids, skill_id,
                prefix: str = "") -> int:
    user_msg = Message(session_id=session_id, role="user",
                       content=f"{prefix}{question}")
    db.add(user_msg)
    db.flush()
    run = Run(session_id=session_id, question=question,
              data_source_ids=jdump(data_source_ids), status="running",
              skill_id=skill_id)
    db.add(run)
    db.flush()
    db.commit()
    return run.id


def _replay(db, skill, session_id, data_source_ids, files, emit, t0,
            question: str | None = None, retrieval: dict | None = None) -> dict:
    """列匹配 → 直接重放代码（不经过 LLM）。"""
    code = _normalize_data_paths(skill.code, files)

    run_pk = _create_run(db, session_id, question or skill.question,
                         data_source_ids, skill.id)
    run_id = f"skill-{uuid.uuid4().hex[:8]}"
    result = run_in_sandbox(run_id, code, files)
    execution = {
        "ok": result.ok, "stdout": result.stdout, "stderr": result.stderr,
        "text": result.text, "tables": result.tables, "charts": result.charts,
        "run_dir": str(result.out_dir),
    }
    emit("execute", {
        "ok": execution["ok"],
        "stdout": execution["stdout"][-2000:], "stderr": execution["stderr"][-2000:],
        "charts": _chart_urls(execution), "tables": execution["tables"],
        "text": execution["text"],
    })
    ok = execution["ok"] and bool(execution["text"] or execution["tables"] or execution["charts"])
    answer = execution["text"] or "（重放完成，请查看图表与结果表）"
    emit("answer", {"answer": answer})
    emit("charts", {"charts": _chart_urls(execution)})
    emit("tables", {"tables": execution["tables"]})

    duration_ms = int((time.time() - t0) * 1000)
    with SessionLocal() as db2:
        run = db2.get(Run, run_pk)
        run.status = "done" if ok else "failed"
        run.ok = ok
        run.code = code
        run.attempts = 1
        run.stdout = execution["stdout"][-4000:]
        run.stderr = execution["stderr"][-4000:]
        run.run_dir = execution["run_dir"]
        run.charts = jdump(_chart_urls(execution))
        run.tables = jdump(execution["tables"])
        run.answer = answer
        run.duration_ms = duration_ms
        run.trace = jdump(_replay_trace(run_pk, skill, execution, answer, ok, duration_ms,
                                        question=question, retrieval=retrieval))
        meta = {"run_id": run_pk, "charts": _chart_urls(execution),
                "tables": execution["tables"], "followups": [],
                "attempts": 1, "ok": ok, "code": code, "skill_replay": True}
        msg = Message(session_id=session_id, role="assistant", content=answer, meta=jdump(meta))
        db2.add(msg)
        db2.flush()
        run.message_id = msg.id
        s2 = db2.get(Skill, skill.id)
        s2.use_count += 1
        if ok:
            s2.success_count += 1
        db2.commit()
    return {"run_id": run_pk, "message_id": msg.id, "ok": ok, "duration_ms": duration_ms}


def _replay_trace(run_pk: int, skill, execution: dict, answer: str,
                  ok: bool, duration_ms: int, question: str | None = None,
                  retrieval: dict | None = None) -> dict:
    """重放路径的可观测记录。

    关键字段是 `llm.calls == 0`——「Skill 重放不经过 LLM」从此是可验证的数据，
    而不是 README 里的一句自我声明。

    `skill.retrieval` 记录本次检索的候选与准入依据（为什么选中它、为什么允许重放）。
    """
    from backend import config
    from backend.analysis.validation import validate_final

    skill_block = {"matched_ids": [skill.id], "hit": True, "mode": "replay"}
    if retrieval is not None:
        skill_block["retrieval"] = retrieval
    return {
        "run_id": run_pk,
        "question": question or skill.question,
        "model": config.MODEL_NAME,
        "latency_ms": duration_ms,
        "stages": [{"node": "skill", "label": f"重放 Skill「{skill.name}」",
                    "duration_ms": duration_ms, "status": "done"}],
        "semantic": {"packs": [skill.pack_id] if skill.pack_id else [],
                     "source": "skill", "analysis_type": "replay",
                     "resolved_metrics": [], "resolved_dimensions": []},
        "skill": skill_block,
        "execution": {"ok": ok, "attempts": 1, "repair_count": 0, "sandboxed": True},
        "llm": {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "by_node": {}},
        "validation": validate_final({"execution": execution, "answer": answer}),
        "final_status": "done" if ok else "failed",
    }


def _build_files(db, data_source_ids) -> dict[str, str]:
    files = {}
    for ds_id in data_source_ids:
        ds = db.get(DataSource, ds_id)
        if not ds:
            continue
        if ds.type == "db":
            if not ds.materialized_path:
                from backend.datasource.service import materialize
                materialize(ds, ds.materialized_table)
                db.commit()
            files[f"{ds.materialized_table}.parquet"] = ds.materialized_path
        else:
            # 与 analysis_runner._prepare 一致：真实存储文件名（含扩展名）
            files[ds.file_name or Path(ds.file_path).name] = ds.file_path
    if not files:
        raise ValueError("未找到有效数据源")
    return files


def _columns_match(skill_columns: list[str], current_columns: set[str]) -> bool:
    """Skill 沉淀时数据列 ⊆ 当前数据列 → 可重放。"""
    if not skill_columns:
        return False
    return set(skill_columns).issubset(current_columns)


_READER_EXTS = {
    "read_csv": {".csv", ".tsv", ".txt"},
    "read_table": {".csv", ".tsv", ".txt"},
    "read_excel": {".xlsx", ".xls"},
    "read_json": {".json", ".jsonl"},
    "read_parquet": {".parquet"},
    "read_fwf": {".txt"},
}


def _reader_compatible(code: str, mount_names: list[str]) -> bool:
    """Skill 代码用的 pandas 读取函数与当前挂载文件的扩展名兼容才可重放。"""
    exts = {Path(n).suffix.lower() for n in mount_names}
    for reader in set(re.findall(r"pd\.(read_\w+)", code)):
        allowed = _READER_EXTS.get(reader)
        if allowed is not None and not exts & allowed:
            return False
    return True


def _normalize_data_paths(code: str, files: dict[str, str]) -> str:
    """把代码里的 /data/ 路径归一化到当前挂载文件名。

    先替换 {name} 占位符；再清理旧版 Skill 中残留的旧文件名（如历史
    display name），避免挂载名变更后重放找不到文件。
    """
    first = next(iter(files), "")
    code = code.replace(f"/data/{PLACEHOLDER}", f"/data/{first}")

    def _sub(match: re.Match) -> str:
        token = match.group(1)
        return match.group(0) if token in files else f"/data/{first}"

    return re.sub(r"/data/([^\"'\s\)]+)", _sub, code)
