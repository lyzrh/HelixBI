"""洞察路由：生成 / 列表 / 详情 / 诊断报告 / 状态流转 / 定时扫描配置。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import DataSource, Insight, now_str
from backend.schemas import InsightGenerateBody, InsightPatch
from backend.insights import engine as insight_engine, scheduler as insight_scheduler

router = APIRouter(prefix="/insights",
                   dependencies=[Depends(get_current_context)])


@router.post("/generate")
def generate(body: InsightGenerateBody, db: Session = Depends(get_db)):
    results = []
    for ds_id in body.data_source_ids:
        ds = db.get(DataSource, ds_id)
        if not ds:
            raise HTTPException(404, f"数据源 {ds_id} 不存在")
        if ds.type == "db" and not ds.materialized_path:
            raise HTTPException(400, f"数据库源「{ds.name}」尚未物化，请先在数据源页缓存")
        results.append(insight_engine.detect_and_persist(db, ds))
    return results


@router.get("")
def list_insights(status: str = "", data_source_id: int | None = None,
                  db: Session = Depends(get_db)):
    q = db.query(Insight)
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
def get_schedule(db: Session = Depends(get_db)):
    return insight_scheduler.get_schedule(db)


@router.put("/schedule")
def update_schedule(body: ScheduleBody, db: Session = Depends(get_db)):
    try:
        return insight_scheduler.set_schedule(db, body.enabled, body.interval_minutes,
                                              body.auto_diagnose)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/schedule/run")
def run_schedule_now():
    """立即执行一次全量扫描（所有可分析数据源）。"""
    return insight_scheduler.run_scan()


@router.get("/export")
def export_insights(status: str = "", db: Session = Depends(get_db)):
    """导出洞察清单 CSV（带 BOM，Excel 直接打开不乱码）。"""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    q = db.query(Insight)
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
def get_insight(iid: int, db: Session = Depends(get_db)):
    i = db.get(Insight, iid)
    if not i:
        raise HTTPException(404, "洞察不存在")
    d = i.to_dict()
    ds = db.get(DataSource, i.data_source_id)
    if ds:
        d["data_source_name"] = ds.name
    return d


@router.post("/{iid}/report")
def make_report(iid: int, db: Session = Depends(get_db)):
    i = db.get(Insight, iid)
    if not i:
        raise HTTPException(404, "洞察不存在")
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
def patch_insight(iid: int, body: InsightPatch, db: Session = Depends(get_db)):
    i = db.get(Insight, iid)
    if not i:
        raise HTTPException(404, "洞察不存在")
    if body.status is not None:
        if body.status not in ("new", "read", "resolved"):
            raise HTTPException(400, "状态仅支持 new/read/resolved")
        i.status = body.status
    db.commit()
    return i.to_dict()
