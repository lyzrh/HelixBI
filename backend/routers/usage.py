"""Token 用量追踪 API：日/周/月统计、按会话查询、历史明细。

安全边界（Security Hardening V1）：

- 用量与成本属**平台级经营信息**，需要 `usage:read`（当前仅 admin）——
  原先"登录即可看"，任何 viewer 都能看到全平台的花费与会话 id；
- 统计**按工作区过滤**：`TokenUsage.session_id → Session.workspace_id`，
  不再把别的工作区的用量混进来（无法归属会话的记录不计入，避免"谁的都算"）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import Run, Session as DbSession, TokenUsage

# 权限口径（Security Hardening V1）：
# - 聚合用量 / 成本（summary、history）是**平台级经营信息** → `usage:read`（当前仅 admin）；
# - 单次运行的明细（sessions/{sid}/tokens）本身就是分析结果 → `analysis:read`
#   （与 /runs/{rid} 同口径，跨工作区仍返回 404）。
router = APIRouter(prefix="/usage", dependencies=[Depends(get_current_context)])



def _scoped_session_ids(db: Session, ctx: UserContext) -> list[int]:
    """当前工作区可见的会话 id（含历史 `workspace_id IS NULL` 的全局会话）。"""
    rows = (db.query(DbSession.id)
            .filter((DbSession.workspace_id == ctx.workspace_id)
                    | (DbSession.workspace_id.is_(None))).all())
    return [r[0] for r in rows]


def _scoped_usage_query(db: Session, ctx: UserContext):
    """按工作区过滤的用量查询；无法归属会话的记录不参与统计。"""
    ids = _scoped_session_ids(db, ctx)
    return db.query(TokenUsage).filter(TokenUsage.session_id.in_(ids or [-1]))


@router.get("/summary")
def usage_summary(db: Session = Depends(get_db),
                  ctx: UserContext = Depends(require_permission("usage:read"))):
    """总览：本工作区累计 token 用量、总费用、分析次数。"""
    rows = _scoped_usage_query(db, ctx).all()
    total_input = sum(r.input_tokens for r in rows)
    total_output = sum(r.output_tokens for r in rows)
    total_cost = sum(r.cost_usd for r in rows)
    total_runs = len({r.run_id for r in rows if r.run_id is not None})

    # 近 7 天每日用量
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    daily = (_scoped_usage_query(db, ctx)
             .filter(TokenUsage.created_at >= seven_days_ago)
             .with_entities(
                 func.date(TokenUsage.created_at).label("day"),
                 func.sum(TokenUsage.input_tokens).label("input_tokens"),
                 func.sum(TokenUsage.output_tokens).label("output_tokens"),
                 func.sum(TokenUsage.cost_usd).label("cost_usd"))
             .group_by(func.date(TokenUsage.created_at)).all())

    daily_stats = [
        {"date": str(r.day), "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
         "cost_usd": round(r.cost_usd, 6)}
        for r in daily
    ]

    # 按节点类型汇总
    node_stats = (_scoped_usage_query(db, ctx)
                  .with_entities(
                      TokenUsage.node,
                      func.sum(TokenUsage.input_tokens).label("input_tokens"),
                      func.sum(TokenUsage.output_tokens).label("output_tokens"),
                      func.sum(TokenUsage.cost_usd).label("cost_usd"))
                  .group_by(TokenUsage.node).all())

    node_breakdown = [
        {"node": r.node, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
         "cost_usd": round(r.cost_usd, 6)}
        for r in node_stats
    ]

    return {
        "workspace_id": ctx.workspace_id,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "total_analyses": total_runs,
        "last_7_days": daily_stats,
        "by_node": node_breakdown,
    }


@router.get("/history")
def usage_history(
    days: int = 30,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(require_permission("usage:read")),
):
    """Token 用量历史明细（按分析运行聚合，工作区内）。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    query = _scoped_usage_query(db, ctx).filter(TokenUsage.created_at >= cutoff)
    total = query.count()

    rows = (query.order_by(TokenUsage.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())

    # 按 run_id 聚合
    from collections import defaultdict
    by_run: dict[int, dict] = defaultdict(lambda: {
        "run_id": None, "session_id": None, "node": [],
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "created_at": "",
    })
    for r in rows:
        key = r.run_id or r.id
        by_run[key]["run_id"] = r.run_id
        by_run[key]["session_id"] = r.session_id
        by_run[key]["node"].append(r.node)
        by_run[key]["input_tokens"] += r.input_tokens
        by_run[key]["output_tokens"] += r.output_tokens
        by_run[key]["cost_usd"] += r.cost_usd
        by_run[key]["created_at"] = r.created_at

    items = sorted(by_run.values(), key=lambda x: x["created_at"], reverse=True)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": list(items),
    }


@router.get("/sessions/{sid}/tokens")
def usage_by_session(sid: int, db: Session = Depends(get_db),
                     ctx: UserContext = Depends(require_permission("analysis:read"))):
    """某次分析运行的 token 用量明细（运行记录按工作区隔离）。

    权限口径与 `/runs/{rid}` 一致：需要 `analysis:read`（查看分析结果），
    因为它暴露的是某次具体分析的花费明细。
    """
    run = db.get(Run, sid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    session = db.get(DbSession, run.session_id) if run.session_id else None
    if not session:
        raise HTTPException(404, "运行记录不存在")
    if session.workspace_id is not None and session.workspace_id != ctx.workspace_id:
        raise HTTPException(404, "运行记录不存在")
    rows = (db.query(TokenUsage).filter(TokenUsage.run_id == sid)
            .order_by(TokenUsage.created_at).all())
    total_input = sum(r.input_tokens for r in rows)
    total_output = sum(r.output_tokens for r in rows)
    total_cost = sum(r.cost_usd for r in rows)

    return {
        "run_id": sid,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "details": [r.to_dict() for r in rows],
    }

