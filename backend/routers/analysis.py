"""分析路由：SSE 流式分析 + 意图解析 + 运行详情/重跑。"""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import Message, Run, Session as DbSession, jdump, jload
from backend.schemas import AnalyzeBody, ParseBody
from backend.analysis import runtime as analysis_runner

router = APIRouter(prefix="", dependencies=[Depends(get_current_context)])

# 并发护栏：Docker 沙箱资源有限（2 CPU / 2G / 容器）
_semaphore = asyncio.Semaphore(2)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(work) -> StreamingResponse:
    """把「在线程池里跑的阻塞任务」桥接成 SSE 响应。

    `work` 是一个接受 `on_event(event, data)` 的可调用对象。
    调用方必须先持有 `_semaphore`（本函数在流结束时负责释放）。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event: str, data: dict):
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def worker():
        try:
            await loop.run_in_executor(None, lambda: work(on_event))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, (None, None))

    async def gen():
        task = asyncio.create_task(worker())
        try:
            while True:
                event, data = await queue.get()
                if event is None:
                    break
                yield _sse_frame(event, data)
        finally:
            _semaphore.release()
            if not task.done():
                await task

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/sessions/{sid}/analyze")
async def analyze(sid: int, body: AnalyzeBody, db: Session = Depends(get_db),
                  ctx: UserContext = Depends(require_permission("analysis:execute"))):
    session = db.get(DbSession, sid)
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.workspace_id not in (ctx.workspace_id, None):
        raise HTTPException(403, "会话不属于当前工作区")
    if not body.data_source_ids:
        raise HTTPException(400, "请至少选择一个数据源")

    if _semaphore.locked():
        raise HTTPException(429, "已有分析任务在执行中，请稍候再试")
    await _semaphore.acquire()

    # 请求作用域内完成 Skill 路由：Retrieval（召回）→ Admission（准入）→ 重放 or Agent
    from backend.skills import engine as skill_engine

    decision = skill_engine.route_query(
        db, body.question, data_source_ids=body.data_source_ids,
        workspace_id=ctx.workspace_id, user_id=ctx.user_id)
    skills = skill_engine.match_skills(db, body.question,
                                       workspace_id=ctx.workspace_id,
                                       user_id=ctx.user_id)
    skill_block = skill_engine.render_skill_prompt(skills)
    # 会话标题在两条路径上都要更新（重放路径不经过下面的 Run 创建逻辑）
    if session.title == "新会话" or not session.title:
        session.title = body.question[:30]

    if decision.replay:
        # 高置信度 → 直接重放已验证代码（0 次 LLM 生成）；重放失败会在引擎内
        # 自动 fallback 到完整 Agent，而不是把错误抛给用户。
        # 标题在这里显式落库：重放路径不再经过下面的 Run 创建 + commit。
        db.commit()
        skill_pk = decision.selected_skill_id

        def work(on_event):
            return skill_engine.run_skill(
                skill_pk, sid, list(body.data_source_ids), on_event,
                workspace_id=ctx.workspace_id, question=body.question,
                decision=decision)

        return await sse_stream(work)

    user_msg = Message(session_id=sid, role="user", content=body.question)
    db.add(user_msg)
    db.flush()
    run = Run(session_id=sid, question=body.question,
              data_source_ids=jdump(body.data_source_ids),
              status="running", spec=jdump(body.spec or {}),
              skill_id=skills[0].id if skills else None)
    db.add(run)
    db.flush()
    run_pk = run.id
    db.commit()

    def work(on_event):
        return analysis_runner.run_analysis_stream(
            run_pk, sid, body.question, list(body.data_source_ids),
            body.spec, skill_block, body.agent_id, on_event,
            skill_ids=[s.id for s in skills],
            retrieval=decision.to_dict(),
        )

    return await sse_stream(work)


@router.post("/sessions/{sid}/parse")
def parse(sid: int, body: ParseBody, db: Session = Depends(get_db),
          ctx: UserContext = Depends(require_permission("analysis:execute"))):
    """仅跑意图解析（parse_intent），返回 QuerySpec 供前端确认。"""
    from backend.agent.graph import parse_intent
    from backend.semantic import pack_for_file, render_semantic_prompt
    from backend.models import DataSource

    try:
        sources = [db.get(DataSource, i) for i in body.data_source_ids]
        blocks = [render_semantic_prompt(pack_for_file(s.name)) for s in sources if s]
        semantic_block = "\n\n".join(b for b in blocks if b)
        state = {"question": body.question, "semantic_block": semantic_block, "spec": {}}
        result = parse_intent(state)  # type: ignore
        return {"spec": result.get("spec", {})}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        return {"spec": {"rewritten_question": body.question}, "error": str(exc)}


def _run_workspace(db: Session, run: Run) -> int | None:
    """Run 归属工作区：随其 session（历史匿名会话视为全局 NULL）。"""
    session = db.get(DbSession, run.session_id) if run.session_id else None
    return session.workspace_id if session else None


def _get_visible_run(db: Session, rid: int, ctx: UserContext) -> Run:
    run = db.get(Run, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    if _run_workspace(db, run) not in (ctx.workspace_id, None):
        raise HTTPException(404, "运行记录不存在")
    return run


@router.get("/runs/recent")
def recent_runs(limit: int = 8, db: Session = Depends(get_db),
                ctx: UserContext = Depends(get_current_context)):
    """工作台「最近分析」列表（成功/失败都要，供快捷回访），按工作区过滤。"""
    runs = (db.query(Run).join(DbSession, Run.session_id == DbSession.id)
            .filter((DbSession.workspace_id == ctx.workspace_id)
                    | (DbSession.workspace_id.is_(None)))
            .order_by(Run.id.desc()).limit(min(max(limit, 1), 20)).all())
    out = []
    for r in runs:
        out.append({
            "id": r.id, "session_id": r.session_id, "question": r.question[:80],
            "status": r.status, "ok": bool(r.ok), "attempts": r.attempts,
            "duration_ms": r.duration_ms, "created_at": r.created_at,
        })
    return out


@router.get("/runs/{rid}")
def get_run(rid: int, db: Session = Depends(get_db),
            ctx: UserContext = Depends(get_current_context)):
    return _get_visible_run(db, rid, ctx).to_dict()


@router.get("/runs/{rid}/export")
def export_run(rid: int, db: Session = Depends(get_db),
               ctx: UserContext = Depends(get_current_context)):
    """单轮分析结果导出自包含 HTML。"""
    import urllib.parse

    from fastapi.responses import Response

    from backend.report.exporter import build_run_html

    run = _get_visible_run(db, rid, ctx)
    content = build_run_html(run)
    filename = urllib.parse.quote(f"分析报告-{run.id}.html")
    return Response(content=content, media_type="text/html",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.post("/runs/{rid}/rerun")
async def rerun(rid: int, db: Session = Depends(get_db),
                ctx: UserContext = Depends(require_permission("analysis:execute"))):
    """用存量 spec + 原问题重跑（SSE）。"""
    run = db.get(Run, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    session = db.get(DbSession, run.session_id)
    if not session:
        raise HTTPException(404, "原会话不存在")
    if session.workspace_id not in (ctx.workspace_id, None):
        raise HTTPException(403, "会话不属于当前工作区")

    if _semaphore.locked():
        raise HTTPException(429, "已有分析任务在执行中，请稍候再试")
    await _semaphore.acquire()

    user_msg = Message(session_id=run.session_id, role="user",
                       content=run.question + "（重跑）")
    db.add(user_msg)
    db.flush()
    new_run = Run(session_id=run.session_id, question=run.question,
                  data_source_ids=run.data_source_ids, status="running",
                  spec=run.spec)
    db.add(new_run)
    db.flush()
    run_pk = new_run.id
    db.commit()

    data_source_ids = jload(run.data_source_ids, [])
    spec = jload(run.spec)

    def work(on_event):
        return analysis_runner.run_analysis_stream(
            run_pk, run.session_id, run.question, data_source_ids,
            spec, "", None, on_event,
            user_context=ctx.to_dict(),
        )

    return await sse_stream(work)
