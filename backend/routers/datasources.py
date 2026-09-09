"""数据源路由：文件上传 / DB 连接 / 预览 / 物化 / 行业包切换。"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.semantic import assign_pack, infer_pack, list_packs, load_pack
from backend.config import UPLOADS_DIR
from backend.db import get_db
from backend.models import DataSource
from backend.schemas import (
    DataSourceDbCreate, DataSourcePatch, DbTestBody, MaterializeBody,
)
from backend.services.datasource import (
    count_rows, list_tables, materialize, preview, read_columns, test_connection,
)
from backend.models import jdump

router = APIRouter(prefix="/datasources")

ALLOWED_EXT = {".csv", ".xlsx", ".xls", ".parquet"}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), name: str = Form(""),
                      db: Session = Depends(get_db)):
    original = file.filename or "upload.csv"
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型 {suffix}，仅支持 csv/xlsx/parquet")

    display_name = name.strip() or original
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{original}"
    content = await file.read()
    dest.write_bytes(content)

    try:
        columns = read_columns(str(dest))
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"文件解析失败: {exc}")

    pack_id = infer_pack(columns) or "retail_sales"
    assign_pack(display_name, pack_id)
    obj = DataSource(
        name=display_name, type="file", file_path=str(dest),
        size_bytes=len(content), pack_id=pack_id, columns_json=jdump(columns),
        row_count=count_rows(str(dest)),
    )
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.post("/db")
def create_db_source(body: DataSourceDbCreate, db: Session = Depends(get_db)):
    cfg = body.config
    ok, msg = test_connection(
        cfg.db_type, cfg.host, cfg.port, cfg.database,
        cfg.username, cfg.password, cfg.sqlite_path,
    )
    if not ok:
        raise HTTPException(400, msg)
    if cfg.db_type == "sqlite" and not cfg.sqlite_path and not cfg.database:
        raise HTTPException(400, "sqlite 需提供文件路径")
    obj = DataSource(
        name=body.name, type="db", db_type=cfg.db_type,
        host=cfg.host or "", port=cfg.port, database_name=cfg.database or cfg.sqlite_path,
        username=cfg.username or "", password=cfg.password or "",
        pack_id=body.pack_id,
    )
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.post("/test")
def test_db(body: DbTestBody):
    cfg = body.config
    ok, msg = test_connection(
        cfg.db_type, cfg.host, cfg.port, cfg.database,
        cfg.username, cfg.password, cfg.sqlite_path,
    )
    return {"ok": ok, "message": msg}


@router.get("")
def list_data_sources(db: Session = Depends(get_db)):
    return [ds.to_dict() for ds in db.query(DataSource).order_by(DataSource.id).all()]


@router.get("/{dsid}")
def get_data_source(dsid: int, db: Session = Depends(get_db)):
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    d = ds.to_dict()
    if ds.pack_id:
        d["pack_detail"] = load_pack(ds.pack_id)
    return d


@router.get("/{dsid}/tables")
def get_tables(dsid: int, db: Session = Depends(get_db)):
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    if ds.type != "db":
        return [{"name": ds.name, "row_estimate": ds.row_count}]
    return list_tables(ds)


@router.get("/{dsid}/preview")
def get_preview(dsid: int, table: str = "", limit: int = 50,
                db: Session = Depends(get_db)):
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    try:
        return preview(ds, table or None, min(limit, 200))
    except Exception as exc:
        raise HTTPException(400, f"预览失败: {exc}")


@router.post("/{dsid}/materialize")
def materialize_source(dsid: int, body: MaterializeBody,
                       db: Session = Depends(get_db)):
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    try:
        result = materialize(ds, body.table, body.force)
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"物化失败: {exc}")


@router.patch("/{dsid}")
def patch_data_source(dsid: int, body: DataSourcePatch,
                      db: Session = Depends(get_db)):
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    if body.name is not None:
        ds.name = body.name
    if body.pack_id is not None:
        valid = {p["id"] for p in list_packs()}
        if body.pack_id not in valid:
            raise HTTPException(400, f"语义包不存在，可选: {sorted(valid)}")
        ds.pack_id = body.pack_id
        if ds.type == "file":
            assign_pack(ds.name, body.pack_id)
    db.commit()
    return ds.to_dict()


@router.delete("/{dsid}")
def delete_data_source(dsid: int, db: Session = Depends(get_db)):
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    if ds.builtin:
        raise HTTPException(400, "内置示例数据源不可删除")
    db.delete(ds)
    db.commit()
    return {"ok": True}
