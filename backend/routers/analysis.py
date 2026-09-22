"""分析路由：SSE 流式分析 + 意图解析 + 运行详情/重跑。

SSE 生命周期（Production Runtime V1）：

    queued → preparing → running → repairing → validating → completed
    异常态：cancelled / timeout / resource_limited / failed

并发策略：全局 `MAX_CONCURRENT_RUNS` + 单用户 `MAX_CONCURRENT_PER_USER`，
超出的请求排队（`RUN_QUEUE_SIZE` 个等待者，超过 `RUN_QUEUE_TIMEOUT` 秒明确
超时）。排队与执行都在 SSE 流内完成——客户端能实时看到自己"在排队"，
而不是对着一个 429 或无限转圈。

断连策略：客户端断开后，后端设置**取消事件**，运行在下一个节点边界收尾并落库
为 `cancelled` 终态（释放并发槽位与沙箱容器）——不会无限占用资源，
已产生的部分结果通过 `/runs/recent` 仍可回访。
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.agent.runerrors import RunCancelled, RunDeadlineExceeded
from backend.analysis.concurrency import (
    RunQueueTimeout, RunRejected, get_registry,
)
from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import Message, Run, Session as DbSession, jdump, jload
from backend.schemas import AnalyzeBody, ParseBody
from backend.analysis import runtime as analysis_runner

router = APIRouter(prefix="", dependencies=[Depends(get_current_context)])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# 断连后仍在后台收尾的任务（保持引用防止 GC；终态落库后自行退出）
_background_tasks: set[asyncio.Task] = set()


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(work_factory, ctx: UserContext | None = None) -> StreamingResponse:
    """把「在线程池里跑的阻塞任务」桥接成 SSE 响应，并接管运行生命周期。

    `work_factory(slot)` 返回一个接受 `on_event(event, data)` 的可调用对象；
    `slot` 是并发槽位（携带取消事件 / 总时限 / 排队耗时，由 work 透传给运行时）。

    生命周期事件（`state`）由本函数与运行时共同发出：
    queued / preparing / running 由这里发，repairing / validating / completed /
    cancelled / timeout / failed 由运行时按真实进度发。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    registry = get_registry()

    def on_event(event: str, data: dict):
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    async def worker(slot):
        try:
            with slot:
                on_event("state", {"phase": "running"})
                work = work_factory(slot)
                await loop.run_in_executor(None, lambda: work(on_event))
        except RunCancelled as exc:
            on_event("state", {"phase": "cancelled", "reason": str(exc)[:160]})
        except RunDeadlineExceeded as exc:
            on_event("state", {"phase": "timeout",
                               "timeout_type": getattr(exc, "timeout_type", "total_run"),
                               "reason": str(exc)[:160]})
        except Exception as exc:  # noqa: BLE001 — 任何异常都要给前端终态
            on_event("state", {"phase": "failed", "reason": str(exc)[:160]})
            on_event("error", {"message": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, (None, None))

    async def gen():
        stats = registry.stats()
        yield _sse_frame("state", {"phase": "queued",
                                   "queue_length": stats.get("queue_waiters", 0),
                                   "concurrency_limit": stats.get("concurrency_limit")})
        try:
            slot = await loop.run_in_executor(
                None, lambda: registry.acquire(ctx.user_id if ctx else None))
        except RunRejected as exc:
            yield _sse_frame("state", {"phase": "resource_limited",
                                       "reason": str(exc)[:160]})
            return
        except RunQueueTimeout as exc:
            yield _sse_frame("state", {"phase": "timeout", "timeout_type": "queue",
                                       "reason": str(exc)[:160]})
            return
        yield _sse_frame("state", {"phase": "preparing",
                                   "queue_wait_ms": int(slot.queue_wait_s * 1000)})

        task = asyncio.create_task(worker(slot))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        try:
            while True:
                event, data = await queue.get()
                if event is None:
                    break
                yield _sse_frame(event, data)
            await task  # 正常完成：等 worker 收尾（断连路径不走这里，见 finally）
        finally:
            if not task.done():
                # 客户端断开：设置取消事件，任务在下一个节点边界收尾落库
                # （cancelled 终态）并释放槽位与容器；这里不 await，不阻塞断开。
                slot.cancel_event.set()

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

    # 请求作用域内完成 Skill 路由：Retrieval（召回）→ Admission（准入）→ 重放 or Agent
    from backend.skills import engine as skill_engine

    t_route = time.perf_counter()
    decision = skill_engine.route_query(
        db, body.question, data_source_ids=body.data_source_ids,
        workspace_id=ctx.workspace_id, user_id=ctx.user_id)
    # few-shot 候选**复用**刚才那次召回的候选，不再重复 match_skills 一遍
    # （旧实现会对同一批 Skill、同一个问题打分两次 + 查两次可见性）
    skills = skill_engine.skills_for_candidates(db, decision.candidates)
    routing_ms = int((time.perf_counter() - t_route) * 1000)
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

        def replay_factory(slot):
            runtime_meta = {"queue_wait_ms": slot.queue_wait_s * 1000,
                            "cancel": slot.cancel_event,
                            "deadline_ts": slot.deadline_ts}

            def work(on_event):
                return skill_engine.run_skill(
                    skill_pk, sid, list(body.data_source_ids), on_event,
                    workspace_id=ctx.workspace_id, question=body.question,
                    decision=decision, routing_ms=routing_ms,
                    runtime_meta=runtime_meta)

            return work

        return await sse_stream(replay_factory, ctx)

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

    def agent_factory(slot):
        runtime_meta = {"queue_wait_ms": slot.queue_wait_s * 1000,
                        "cancel": slot.cancel_event,
                        "deadline_ts": slot.deadline_ts}

        def work(on_event):
            return analysis_runner.run_analysis_stream(
                run_pk, sid, body.question, list(body.data_source_ids),
                body.spec, skill_block, body.agent_id, on_event,
                skill_ids=[s.id for s in skills],
                retrieval={**decision.to_dict(), "routing_ms": routing_ms},
                skill_candidates=skills,
                runtime_meta=runtime_meta,
            )

        return work

    return await sse_stream(agent_factory, ctx)


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
                ctx: UserContext = Depends(require_permission("analysis:read"))):
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
            ctx: UserContext = Depends(require_permission("analysis:read"))):
    """运行详情（含 trace 与产物元数据）——读分析数据需要 analysis:read。"""
    return _get_visible_run(db, rid, ctx).to_dict()


@router.get("/runs/{rid}/export")
def export_run(rid: int, db: Session = Depends(get_db),
               ctx: UserContext = Depends(require_permission("analysis:read"))):
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

    data_source_ids = jload(run.data_source_ids, [])
    if not data_source_ids:
        raise HTTPException(400, "原运行未关联数据源，无法重跑")

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

    spec = jload(run.spec)

    def work_factory(slot):
        runtime_meta = {"queue_wait_ms": slot.queue_wait_s * 1000,
                        "cancel": slot.cancel_event,
                        "deadline_ts": slot.deadline_ts}

        def work(on_event):
            return analysis_runner.run_analysis_stream(
                run_pk, run.session_id, run.question, data_source_ids,
                spec, "", None, on_event,
                user_context=ctx.to_dict(),
                runtime_meta=runtime_meta,
            )

        return work

    return await sse_stream(work_factory, ctx)
