"""分析路由：SSE 流式分析 + 意图解析 + 运行详情/重跑。"""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Message, Run, Session as DbSession, jdump, jload
from backend.schemas import AnalyzeBody, ParseBody
from backend.analysis import runtime as analysis_runner

router = APIRouter(prefix="")

# 并发护栏：Docker 沙箱资源有限（2 CPU / 2G / 容器）
_semaphore = asyncio.Semaphore(2)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/sessions/{sid}/analyze")
async def analyze(sid: int, body: AnalyzeBody, db: Session = Depends(get_db)):
    session = db.get(DbSession, sid)
    if not session:
        raise HTTPException(404, "会话不存在")
    if not body.data_source_ids:
        raise HTTPException(400, "请至少选择一个数据源")

    if _semaphore.locked():
        raise HTTPException(429, "已有分析任务在执行中，请稍候再试")
    await _semaphore.acquire()

    # 请求作用域内：匹配已启用 Skill（few-shot 注入）+ 落 user message + running run
    from backend.skills import engine as skill_engine
    skills = skill_engine.match_skills(db, body.question)
    skill_block = skill_engine.render_skill_prompt(skills)

    user_msg = Message(session_id=sid, role="user", content=body.question)
    db.add(user_msg)
    db.flush()
    if session.title == "新会话" or not session.title:
        session.title = body.question[:30]
    run = Run(session_id=sid, question=body.question,
              data_source_ids=jdump(body.data_source_ids),
              status="running", spec=jdump(body.spec or {}),
              skill_id=skills[0].id if skills else None)
    db.add(run)
    db.flush()
    run_pk = run.id
    db.commit()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event: str, data: dict):
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def worker():
        try:
            await loop.run_in_executor(
                None,
                lambda: analysis_runner.run_analysis_stream(
                    run_pk, sid, body.question, body.data_source_ids,
                    body.spec, skill_block, body.agent_id, on_event,
                ),
            )
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


@router.post("/sessions/{sid}/parse")
def parse(sid: int, body: ParseBody, db: Session = Depends(get_db)):
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


@router.get("/runs/recent")
def recent_runs(limit: int = 8, db: Session = Depends(get_db)):
    """工作台「最近分析」列表（成功/失败都要，供快捷回访）。"""
    runs = (db.query(Run).order_by(Run.id.desc()).limit(min(max(limit, 1), 20)).all())
    out = []
    for r in runs:
        out.append({
            "id": r.id, "session_id": r.session_id, "question": r.question[:80],
            "status": r.status, "ok": bool(r.ok), "attempts": r.attempts,
            "duration_ms": r.duration_ms, "created_at": r.created_at,
        })
    return out


@router.get("/runs/{rid}")
def get_run(rid: int, db: Session = Depends(get_db)):
    run = db.get(Run, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    return run.to_dict()


@router.get("/runs/{rid}/export")
def export_run(rid: int, db: Session = Depends(get_db)):
    """单轮分析结果导出自包含 HTML。"""
    import urllib.parse

    from fastapi.responses import Response

    from backend.models import Run
    from backend.report.exporter import build_run_html

    run = db.get(Run, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    content = build_run_html(run)
    filename = urllib.parse.quote(f"分析报告-{run.id}.html")
    return Response(content=content, media_type="text/html",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.post("/runs/{rid}/rerun")
async def rerun(rid: int, db: Session = Depends(get_db)):
    """用存量 spec + 原问题重跑（SSE）。"""
    run = db.get(Run, rid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    session = db.get(DbSession, run.session_id)
    if not session:
        raise HTTPException(404, "原会话不存在")

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

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(event: str, data: dict):
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def worker():
        try:
            await loop.run_in_executor(
                None,
                lambda: analysis_runner.run_analysis_stream(
                    run_pk, run.session_id, run.question, data_source_ids,
                    spec, "", None, on_event,
                ),
            )
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
