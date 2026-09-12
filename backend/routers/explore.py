"""自助分析路由（拖拽式图表）：参数校验 + 转发 explore_engine。

业务逻辑（pandas 聚合引擎、SQL 查询）在 backend/analysis/explore.py，
本路由按项目架构规则只做校验与错误码转换。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import DataSource
from backend.schemas import ExploreBody, SqlBody
from backend.analysis import explore as explore_engine

router = APIRouter(prefix="/explore")


def _get_ds(db: Session, data_source_id: int) -> DataSource:
    ds = db.get(DataSource, data_source_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    return ds


@router.get("/schema")
def explore_schema(data_source_id: int, db: Session = Depends(get_db)):
    ds = _get_ds(db, data_source_id)
    try:
        return explore_engine.schema_fields(ds)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/profile")
def explore_profile(data_source_id: int, db: Session = Depends(get_db)):
    """字段画像（数据概览）：缺失率 / 基数 / 数值分布 / 日期范围 / 高频值。"""
    ds = _get_ds(db, data_source_id)
    try:
        return explore_engine.profile_fields(ds)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/query")
def explore_query(body: ExploreBody, db: Session = Depends(get_db)):
    ds = _get_ds(db, body.data_source_id)
    if not body.metrics:
        raise HTTPException(400, "请至少拖入一个指标（纵轴）")
    try:
        return explore_engine.run_query(ds, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/sql")
def explore_sql(body: SqlBody, db: Session = Depends(get_db)):
    """只读 SQL 查询：数据载入 sqlite 内存库执行，支持标准 SELECT 语法。

    对应 FineDataLink「库表管理」的直接 SQL 查询能力；纯本地计算，零 token。
    """
    ds = _get_ds(db, body.data_source_id)
    try:
        return explore_engine.run_sql(ds, body.sql)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
