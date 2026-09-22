"""仪表板路由：CRUD + 条目 + HTML 导出。"""

import urllib.parse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import (
    Dashboard, DashboardItem, Insight, Run, Session as DbSession,
)
from backend.schemas import (
    DashboardCreate, DashboardItemCreate, DashboardItemPatch, DashboardPatch,
    ItemReorderBody,
)
from backend.report.exporter import build_dashboard_html

router = APIRouter(prefix="/dashboards",
                   dependencies=[Depends(get_current_context),
                                 Depends(require_permission("dashboard:read"))])


def _visible_filter(ctx: UserContext):
    """本工作区 + 历史全局（workspace_id IS NULL）仪表板。"""
    from sqlalchemy import or_

    return or_(Dashboard.workspace_id == ctx.workspace_id,
               Dashboard.workspace_id.is_(None))


def _get_visible_dashboard(db: Session, did: int, ctx: UserContext) -> Dashboard:
    d = db.get(Dashboard, did)
    if not d:
        raise HTTPException(404, "仪表板不存在")
    if d.workspace_id is not None and d.workspace_id != ctx.workspace_id:
        # 不暴露他工作区仪表板的存在性，统一按 404 处理
        raise HTTPException(404, "仪表板不存在")
    return d


@router.get("")
def list_dashboards(db: Session = Depends(get_db),
                    ctx: UserContext = Depends(get_current_context)):
    out = []
    for d in db.query(Dashboard).filter(_visible_filter(ctx)).order_by(Dashboard.id).all():
        item = d.to_dict()
        item["item_count"] = db.query(DashboardItem).filter(
            DashboardItem.dashboard_id == d.id).count()
        out.append(item)
    return out


@router.post("")
def create_dashboard(body: DashboardCreate, db: Session = Depends(get_db),
                     ctx: UserContext = Depends(require_permission("dashboard:write"))):
    obj = Dashboard(name=body.name, description=body.description,
                    workspace_id=ctx.workspace_id)
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.get("/{did}")
def get_dashboard(did: int, db: Session = Depends(get_db),
                  ctx: UserContext = Depends(get_current_context)):
    d = _get_visible_dashboard(db, did, ctx)
    result = d.to_dict()
    items = (db.query(DashboardItem)
             .filter(DashboardItem.dashboard_id == did)
             .order_by(DashboardItem.sort_order, DashboardItem.id).all())
    result["items"] = [i.to_dict() for i in items]
    return result


@router.patch("/{did}")
def patch_dashboard(did: int, body: DashboardPatch, db: Session = Depends(get_db),
                    ctx: UserContext = Depends(require_permission("dashboard:write"))):
    d = _get_visible_dashboard(db, did, ctx)
    if body.name is not None:
        d.name = body.name
    if body.description is not None:
        d.description = body.description
    db.commit()
    return d.to_dict()


@router.delete("/{did}")
def delete_dashboard(did: int, db: Session = Depends(get_db),
                     ctx: UserContext = Depends(require_permission("dashboard:write"))):
    d = _get_visible_dashboard(db, did, ctx)
    db.query(DashboardItem).filter(DashboardItem.dashboard_id == did).delete()
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.post("/{did}/items")
def add_item(did: int, body: DashboardItemCreate, db: Session = Depends(get_db),
             ctx: UserContext = Depends(require_permission("dashboard:write"))):
    d = _get_visible_dashboard(db, did, ctx)
    # insight 类型自动补全文本载荷
    payload = body.payload
    if body.type == "insight" and "insight_id" in payload:
        ins = db.get(Insight, payload["insight_id"])
        if ins:
            payload.setdefault("text", f"【{ins.severity}】{ins.title}\n\n{ins.detail}")
    max_order = db.query(DashboardItem.sort_order).filter(
        DashboardItem.dashboard_id == did).order_by(
        DashboardItem.sort_order.desc()).first()
    next_order = (max_order[0] + 1) if max_order and max_order[0] is not None else 0
    import json
    if body.source_run_id:
        run = db.get(Run, body.source_run_id)
        run_session = (db.get(DbSession, run.session_id)
                       if run and run.session_id else None)
        if not run_session:
            raise HTTPException(404, "运行记录不存在")
        if run_session.workspace_id is not None                 and run_session.workspace_id != ctx.workspace_id:
            raise HTTPException(404, "运行记录不存在")
    obj = DashboardItem(dashboard_id=did, type=body.type, title=body.title,
                        payload=json.dumps(payload, ensure_ascii=False),
                        source_run_id=body.source_run_id, sort_order=next_order)
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db),
                ctx: UserContext = Depends(require_permission("dashboard:write"))):
    item = db.get(DashboardItem, item_id)
    if not item:
        raise HTTPException(404, "仪表板条目不存在")
    _get_visible_dashboard(db, item.dashboard_id, ctx)
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.patch("/items/{item_id}")
def patch_item(item_id: int, body: DashboardItemPatch,
               db: Session = Depends(get_db),
               ctx: UserContext = Depends(require_permission("dashboard:write"))):
    item = db.get(DashboardItem, item_id)
    if not item:
        raise HTTPException(404, "仪表板条目不存在")
    _get_visible_dashboard(db, item.dashboard_id, ctx)
    if body.title is not None:
        item.title = body.title.strip()[:255] or item.title
    if body.span is not None:
        item.span = body.span
    if body.payload is not None:
        import json
        item.payload = json.dumps(body.payload, ensure_ascii=False)
    db.commit()
    return item.to_dict()


@router.post("/{did}/items/reorder")
def reorder_items(did: int, body: ItemReorderBody, db: Session = Depends(get_db),
                  ctx: UserContext = Depends(require_permission("dashboard:write"))):
    """按前端给定的 id 顺序重排条目。"""
    _get_visible_dashboard(db, did, ctx)
    items = {i.id: i for i in db.query(DashboardItem).filter(
        DashboardItem.dashboard_id == did).all()}
    for idx, iid in enumerate(body.ids):
        if iid in items:
            items[iid].sort_order = idx
    db.commit()
    return {"ok": True}


@router.get("/{did}/export")
def export_dashboard(did: int, db: Session = Depends(get_db),
                     ctx: UserContext = Depends(get_current_context)):
    """导出仪表板 HTML。

    安全：条目里的 `chart_url` / `source_run_id` 是客户端提交的，导出前按当前
    工作区可见的产物目录与运行 id 过滤，避免把别的工作区的产物内嵌进报告
    （同时拒绝 `../` 这类路径遍历）。
    """
    from backend.analysis.runtime import visible_run_dirs
    from backend.models import Run

    d = _get_visible_dashboard(db, did, ctx)
    items = (db.query(DashboardItem)
             .filter(DashboardItem.dashboard_id == did)
             .order_by(DashboardItem.sort_order, DashboardItem.id).all())
    allowed_dirs = visible_run_dirs(db, ctx.workspace_id)
    visible_run_ids = {
        rid for (rid,) in (db.query(Run.id)
                           .join(DbSession, Run.session_id == DbSession.id)
                           .filter((DbSession.workspace_id == ctx.workspace_id)
                                   | (DbSession.workspace_id.is_(None))).all())
    }
    content = build_dashboard_html(d, items, allowed_run_dirs=allowed_dirs,
                                   visible_run_ids=visible_run_ids)
    filename = urllib.parse.quote(f"{d.name}.html")
    return Response(content=content, media_type="text/html",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})
