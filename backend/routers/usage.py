"""Token 用量追踪 API：日/周/月统计、按会话查询、历史明细。"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import TokenUsage

router = APIRouter(prefix="/usage",
                   dependencies=[Depends(get_current_context)])


@router.get("/summary")
def usage_summary(db: Session = Depends(get_db)):
    """总览：累计 token 用量、总费用、分析次数。"""
    rows = db.query(TokenUsage).all()
    total_input = sum(r.input_tokens for r in rows)
    total_output = sum(r.output_tokens for r in rows)
    total_cost = sum(r.cost_usd for r in rows)
    total_runs = db.query(TokenUsage.run_id).filter(TokenUsage.run_id.isnot(None)).distinct().count()

    # 近 7 天每日用量
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    daily = db.query(
        func.date(TokenUsage.created_at).label("day"),
        func.sum(TokenUsage.input_tokens).label("input_tokens"),
        func.sum(TokenUsage.output_tokens).label("output_tokens"),
        func.sum(TokenUsage.cost_usd).label("cost_usd"),
    ).filter(TokenUsage.created_at >= seven_days_ago).group_by(func.date(TokenUsage.created_at)).all()

    daily_stats = [
        {"date": str(r.day), "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": round(r.cost_usd, 6)}
        for r in daily
    ]

    # 按节点类型汇总
    node_stats = db.query(
        TokenUsage.node,
        func.sum(TokenUsage.input_tokens).label("input_tokens"),
        func.sum(TokenUsage.output_tokens).label("output_tokens"),
        func.sum(TokenUsage.cost_usd).label("cost_usd"),
    ).group_by(TokenUsage.node).all()

    node_breakdown = [
        {"node": r.node, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": round(r.cost_usd, 6)}
        for r in node_stats
    ]

    return {
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
):
    """Token 用量历史明细（按分析运行聚合）。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    query = db.query(TokenUsage).filter(TokenUsage.created_at >= cutoff)
    total = query.count()

    rows = query.order_by(TokenUsage.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

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
                     ctx: UserContext = Depends(get_current_context)):
    """某次分析运行的 token 用量明细（运行记录按工作区隔离）。"""
    from backend.models import Run, Session as DbSession

    run = db.get(Run, sid)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    session = db.get(DbSession, run.session_id) if run.session_id else None
    ws = session.workspace_id if session else None
    if ws not in (ctx.workspace_id, None):
        raise HTTPException(404, "运行记录不存在")
    rows = db.query(TokenUsage).filter(TokenUsage.run_id == sid).order_by(TokenUsage.created_at).all()
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
