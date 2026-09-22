"""自助分析路由（拖拽式图表）：参数校验 + 转发 explore_engine。

业务逻辑（pandas 聚合引擎、SQL 查询）在 `backend/analysis/explore.py`，
本路由按项目架构规则只做校验与错误码转换。

安全边界（Security Hardening V1）：本组接口能**直接读数据**（画像值 / 聚合结果 /
只读 SQL 结果），因此除了权限点之外，数据源必须按工作区校验——
原先只做了"数据源存在吗"，跨工作区猜 id 就能读到别人的数据；
现在统一走 `_get_visible_data_source`（本工作区或历史全局），越权返回 404 并留审计。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth import audit
from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import DataSource
from backend.schemas import ExploreBody, SqlBody
from backend.analysis import explore as explore_engine

router = APIRouter(prefix="/explore",
                   dependencies=[Depends(get_current_context),
                                 Depends(require_permission("datasource:read"))])


def _get_visible_ds(db: Session, data_source_id: int, ctx: UserContext,
                    request: Request, action: str) -> DataSource:
    """取数据源并校验工作区归属；跨工作区一律 404（不暴露存在性）并留审计。"""
    ds = db.get(DataSource, data_source_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    if ds.workspace_id is not None and ds.workspace_id != ctx.workspace_id:
        audit.record_denied("authz.workspace_denied", ctx=ctx,
                            target=f"datasource:{data_source_id}",
                            detail={"action": action, "reason": "cross_workspace"},
                            request=request)
        raise HTTPException(404, "数据源不存在")
    return ds


@router.get("/schema")
def explore_schema(data_source_id: int, request: Request,
                   db: Session = Depends(get_db),
                   ctx: UserContext = Depends(get_current_context)):
    ds = _get_visible_ds(db, data_source_id, ctx, request, "explore.schema")
    try:
        return explore_engine.schema_fields(ds)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/profile")
def explore_profile(data_source_id: int, request: Request,
                    db: Session = Depends(get_db),
                    ctx: UserContext = Depends(get_current_context)):
    """字段画像（数据概览）：缺失率 / 基数 / 数值分布 / 日期范围 / 高频值。"""
    ds = _get_visible_ds(db, data_source_id, ctx, request, "explore.profile")
    try:
        return explore_engine.profile_fields(ds)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/query")
def explore_query(body: ExploreBody, request: Request, db: Session = Depends(get_db),
                  ctx: UserContext = Depends(require_permission("analysis:execute"))):
    ds = _get_visible_ds(db, body.data_source_id, ctx, request, "explore.query")
    if not body.metrics:
        raise HTTPException(400, "请至少拖入一个指标（纵轴）")
    try:
        return explore_engine.run_query(ds, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/sql")
def explore_sql(body: SqlBody, request: Request, db: Session = Depends(get_db),
                ctx: UserContext = Depends(require_permission("sql:execute"))):
    """只读 SQL 查询：数据载入 sqlite 内存库执行，支持标准 SELECT 语法。

    对应 FineDataLink「库表管理」的直接 SQL 查询能力；纯本地计算，零 token。
    """
    ds = _get_visible_ds(db, body.data_source_id, ctx, request, "explore.sql")
    try:
        return explore_engine.run_sql(ds, body.sql)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
