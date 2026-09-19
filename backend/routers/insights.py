"""洞察路由：生成 / 列表 / 详情 / 诊断报告 / 状态流转 / 定时扫描配置。

权限模型（与全局 permission-checker 机制一致）：
- 读（列表 / 详情 / 导出 / 调度配置查看）：datasource:read
- 生成 / LLM 诊断报告：analysis:execute
- 状态流转：datasource:write
- 调度配置修改 / 立即全量扫描：workspace:manage（系统级操作，仅 admin）

隔离规则：Insight 经 data_source_id 间接归属工作区——按关联数据源的
workspace_id 过滤（本工作区 ∪ 历史全局），越权一律 404 不暴露存在性。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import DataSource, Insight, now_str
from backend.schemas import InsightGenerateBody, InsightPatch
from backend.insights import engine as insight_engine, scheduler as insight_scheduler

router = APIRouter(prefix="/insights",
                   dependencies=[Depends(get_current_context)])


def _visible_filter(ctx: UserContext):
    from sqlalchemy import or_

    return (DataSource.workspace_id == ctx.workspace_id) | (DataSource.workspace_id.is_(None))


def _get_visible_insight(db: Session, iid: int, ctx: UserContext) -> Insight:
    i = db.get(Insight, iid)
    if not i:
        raise HTTPException(404, "洞察不存在")
    ds = db.get(DataSource, i.data_source_id)
    if ds is None or ds.workspace_id not in (ctx.workspace_id, None):
        raise HTTPException(404, "洞察不存在")
    return i


def _get_visible_data_source(db: Session, ds_id: int, ctx: UserContext) -> DataSource:
    ds = db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, f"数据源 {ds_id} 不存在")
    if ds.workspace_id is not None and ds.workspace_id != ctx.workspace_id:
        raise HTTPException(404, f"数据源 {ds_id} 不存在")
    return ds


@router.post("/generate")
def generate(body: InsightGenerateBody, db: Session = Depends(get_db),
             ctx: UserContext = Depends(require_permission("analysis:execute"))):
    results = []
    for ds_id in body.data_source_ids:
        ds = _get_visible_data_source(db, ds_id, ctx)
        if ds.type == "db" and not ds.materialized_path:
            raise HTTPException(400, f"数据库源「{ds.name}」尚未物化，请先在数据源页缓存")
        results.append(insight_engine.detect_and_persist(db, ds))
    return results


@router.get("")
def list_insights(status: str = "", data_source_id: int | None = None,
                  db: Session = Depends(get_db),
                  ctx: UserContext = Depends(require_permission("datasource:read"))):
    q = (db.query(Insight).join(DataSource, Insight.data_source_id == DataSource.id)
         .filter(_visible_filter(ctx)))
    if status:
        q = q.filter(Insight.status == status)
    if data_source_id:
        q = q.filter(Insight.data_source_id == data_source_id)
    insights = q.order_by(Insight.created_at.desc()).all()
    out = []
    for i in insights:
        d = i.to_dict()
        ds = db.get(DataSource, i.data_source_id)
        d["data_source_name"] = ds.name if ds else ""
        out.append(d)
    return out


# ---- 定时扫描（必须声明在 /{iid} 之前，避免 "schedule" 被当作 iid 解析）----


class ScheduleBody(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = None
    auto_diagnose: bool | None = None


@router.get("/schedule")
def get_schedule(db: Session = Depends(get_db),
                 _ctx: UserContext = Depends(require_permission("datasource:read"))):
    return insight_scheduler.get_schedule(db)


@router.put("/schedule")
def update_schedule(body: ScheduleBody, db: Session = Depends(get_db),
                    _ctx: UserContext = Depends(require_permission("workspace:manage"))):
    try:
        return insight_scheduler.set_schedule(db, body.enabled, body.interval_minutes,
                                              body.auto_diagnose)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/schedule/run")
def run_schedule_now(_ctx: UserContext = Depends(require_permission("workspace:manage"))):
    """立即执行一次全量扫描（所有可分析数据源，系统级操作）。"""
    return insight_scheduler.run_scan()


@router.get("/export")
def export_insights(status: str = "", db: Session = Depends(get_db),
                    ctx: UserContext = Depends(require_permission("datasource:read"))):
    """导出洞察清单 CSV（带 BOM，Excel 直接打开不乱码），仅本工作区可见的洞察。"""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    q = (db.query(Insight).join(DataSource, Insight.data_source_id == DataSource.id)
         .filter(_visible_filter(ctx)))
    if status:
        q = q.filter(Insight.status == status)
    insights = q.order_by(Insight.created_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "数据源", "严重度", "规则", "标题", "详情",
                     "状态", "发现时间", "是否已诊断"])
    for i in insights:
        ds = db.get(DataSource, i.data_source_id)
        writer.writerow([
            i.id, ds.name if ds else "", i.severity, i.rule_id, i.title,
            i.detail, i.status, i.created_at, "是" if i.report else "否",
        ])
    buf.seek(0)
    content = "\ufeff" + buf.getvalue()  # BOM
    filename = f"insights_{now_str().replace(':', '').replace(' ', '_').replace('-', '')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{iid}")
def get_insight(iid: int, db: Session = Depends(get_db),
                ctx: UserContext = Depends(require_permission("datasource:read"))):
    i = _get_visible_insight(db, iid, ctx)
    d = i.to_dict()
    ds = db.get(DataSource, i.data_source_id)
    if ds:
        d["data_source_name"] = ds.name
    return d


@router.post("/{iid}/report")
def make_report(iid: int, db: Session = Depends(get_db),
                ctx: UserContext = Depends(require_permission("analysis:execute"))):
    i = _get_visible_insight(db, iid, ctx)
    ds = db.get(DataSource, i.data_source_id)
    if not ds:
        raise HTTPException(404, "关联数据源不存在")
    try:
        report = insight_engine.generate_report(i, ds)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    i.report = report
    db.commit()
    return {"report": report}


@router.patch("/{iid}")
def patch_insight(iid: int, body: InsightPatch, db: Session = Depends(get_db),
                  ctx: UserContext = Depends(require_permission("datasource:write"))):
    i = _get_visible_insight(db, iid, ctx)
    if body.status is not None:
        if body.status not in ("new", "read", "resolved"):
            raise HTTPException(400, "状态仅支持 new/read/resolved")
        i.status = body.status
    db.commit()
    return i.to_dict()
