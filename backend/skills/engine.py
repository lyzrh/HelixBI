"""Skill 引擎：沉淀 / 匹配 / few-shot 渲染 / 双模式运行（replay | 重新生成）。

FineBI NEXT Skill 思路的轻量实现：验证过的分析路径沉淀为可复用资产。
- 列集合匹配 → 直接重放代码（跳过 LLM，秒回）
- 不匹配 → few-shot 注入 generate_code 重新生成
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
    for ds_id in jload(run.data_source_ids, []):
        ds = db.get(DataSource, ds_id)
        if ds:
            columns.update(jload(ds.columns_json, []))
            pack_id = pack_id or ds.pack_id
    skill = Skill(
        name=name, description=description or run.question[:100],
        pack_id=pack_id, question=run.question, spec=run.spec,
        code=run.code, columns_json=jdump(sorted(columns)),
        source_run_id=run_id, tags=jdump(tags or []),
        scope="user" if (workspace_id is None and user_id is not None) else "workspace",
        workspace_id=workspace_id, user_id=user_id,
    )
    db.add(skill)
    db.flush()
    db.commit()
    return skill.to_dict()


# ---- 匹配 ----

def _tokenize(text: str) -> set[str]:
    """中文 2-gram + 英文/数字词。"""
    words = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) == 1:
            words.add(seg)
        else:
            words.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return words


def match_skills(db, question: str, pack_id: str | None = None,
                 limit: int = 2, min_score: int = 2,
                 workspace_id: int | None = None,
                 user_id: int | None = None) -> list:
    """Skill 路由：词面 2-gram + 语义包加权 + **分析类型一致性**。

    为什么需要第三个信号：仅靠词面，「按从高到低排序」与「环比对比」这类
    **同指标、不同口径**的问题无法区分，Top1 会误路由到错误的分析路径。
    分析类型由确定性解析器（`backend.semantic.resolver`）给出，零 token。

    作用域隔离：workspace_id 传入时只检索 global + 本工作区 + 本人 scoped 的
    Skill，避免 Sales 工作区的 Skill 被 Marketing 工作区错误检索。
    """
    from backend.semantic import resolve

    q_tokens = _tokenize(question)
    query_type_cache: dict[str, str] = {}
    skill_type_cache: dict[int, str] = {}

    def _query_type(pid: str | None) -> str:
        if not pid:
            return "unknown"
        if pid not in query_type_cache:
            query_type_cache[pid] = resolve(question, pid).get("analysis_type", "unknown")
        return query_type_cache[pid]

    def _skill_type(skill) -> str:
        if skill.id not in skill_type_cache:
            pid = skill.pack_id or pack_id
            skill_type_cache[skill.id] = (
                resolve(skill.question, pid).get("analysis_type", "unknown") if pid else "unknown")
        return skill_type_cache[skill.id]

    query = db.query(Skill).filter(Skill.enabled == True)  # noqa: E712
    if workspace_id is not None:
        query = query.filter(
            (Skill.scope == "global")
            | (Skill.workspace_id == workspace_id)
            | ((Skill.scope == "user") & (Skill.user_id == user_id))
        )
    scored = []
    for skill in query.all():
        corpus = skill.question + " " + skill.name + " " + " ".join(jload(skill.tags, []))
        score = len(q_tokens & _tokenize(corpus))
        if pack_id and skill.pack_id == pack_id:
            score += 3
        q_type = _query_type(skill.pack_id or pack_id)
        if q_type != "unknown" and q_type == _skill_type(skill):
            score += 2
        if score >= min_score:
            scored.append((score, skill))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


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
              on_event=None, workspace_id: int | None = None) -> dict:
    """在工作线程中运行 Skill，返回落库摘要。"""
    t0 = time.time()
    emit = on_event or (lambda *_: None)
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
                session = DbSession(title=f"Skill：{skill.name}")
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
            replay = (_columns_match(jload(skill.columns_json, []), columns_now)
                      and _reader_compatible(skill.code, list(files)))

            emit("step", {"node": "skill", "label": f"运行 Skill「{skill.name}」",
                          "status": "running", "detail": "重放已验证代码" if replay else "参考案例重新生成"})

            if replay:
                summary = _replay(db, skill, session_id, data_source_ids, files, emit, t0)
                emit("step", {"node": "skill", "label": f"运行 Skill「{skill.name}」",
                              "status": "done"})
                emit("done", summary)
                return summary

        # 列不匹配 → few-shot 重新生成（analysis_runner 内部已发 done）
        from backend.analysis import runtime as analysis_runner
        with SessionLocal() as db:
            skill = db.get(Skill, skill_pk)
            run_pk = _create_run(db, session_id, skill.question, data_source_ids, skill.id)
        summary = analysis_runner.run_analysis_stream(
            run_pk, session_id, skill.question, data_source_ids,
            jload(skill.spec), render_skill_prompt([skill]), None, emit,
            skill_ids=[skill.id],
        )
        with SessionLocal() as db:
            s2 = db.get(Skill, skill_pk)
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


def _create_run(db, session_id, question, data_source_ids, skill_id) -> int:
    user_msg = Message(session_id=session_id, role="user",
                       content=f"[Skill] {question}")
    db.add(user_msg)
    db.flush()
    run = Run(session_id=session_id, question=question,
              data_source_ids=jdump(data_source_ids), status="running",
              skill_id=skill_id)
    db.add(run)
    db.flush()
    db.commit()
    return run.id


def _replay(db, skill, session_id, data_source_ids, files, emit, t0) -> dict:
    """列匹配 → 直接重放代码（不经过 LLM）。"""
    code = _normalize_data_paths(skill.code, files)

    run_pk = _create_run(db, session_id, skill.question, data_source_ids, skill.id)
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
        run.trace = jdump(_replay_trace(run_pk, skill, execution, answer, ok, duration_ms))
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
                  ok: bool, duration_ms: int) -> dict:
    """重放路径的可观测记录。

    关键字段是 `llm.calls == 0`——「Skill 重放不经过 LLM」从此是可验证的数据，
    而不是 README 里的一句自我声明。
    """
    from backend import config
    from backend.analysis.validation import validate_final

    return {
        "run_id": run_pk,
        "question": skill.question,
        "model": config.MODEL_NAME,
        "latency_ms": duration_ms,
        "stages": [{"node": "skill", "label": f"重放 Skill「{skill.name}」",
                    "duration_ms": duration_ms, "status": "done"}],
        "semantic": {"packs": [skill.pack_id] if skill.pack_id else [],
                     "source": "skill", "analysis_type": "replay",
                     "resolved_metrics": [], "resolved_dimensions": []},
        "skill": {"matched_ids": [skill.id], "hit": True, "mode": "replay"},
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
