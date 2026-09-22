"""数据源路由：文件上传 / DB 连接 / 预览 / 物化 / 行业包切换。

安全边界（Security Hardening V1）：

- 所有端点在登录门之上再按操作挂权限点（读 `datasource:read` / 写 `datasource:write`）；
- **数据库口令只以密文落库**（`backend/datasource/secrets.py`），API 响应永不含明文；
- 缺少加密密钥时拒绝保存并给出可操作提示，不静默降级为明文；
- 凭据写入 / 迁移 / 历史明文读取都进审计（`/auth/audit`）。
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from backend import config
from backend.auth import audit
from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.datasource import secrets as credential_secrets
from backend.semantic import assign_pack, infer_pack, list_packs, load_pack
from backend.config import MAX_UPLOAD_MB, UPLOADS_DIR
from backend.db import get_db
from backend.models import DataSource
from backend.schemas import (
    DataSourceDbCreate, DataSourcePatch, DbTestBody, MaterializeBody,
)
from backend.datasource.service import (
    count_rows, detect_file_type, list_tables, materialize, preview,
    read_columns, test_connection,
)
from backend.models import jdump

router = APIRouter(prefix="/datasources",
                   dependencies=[Depends(get_current_context)])

ALLOWED_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet"}


def _decode_form_text(value: str) -> str:
    """回收 multipart 表单字段乱码。

    python-multipart 对非 UTF-8 字节（如 Windows 控制台 curl 以 GBK 发送中文）
    会按 latin-1 逐字节解码成乱码；浏览器路径始终是 UTF-8，不受影响。
    这里把 latin-1 可逆的字节按 utf-8/gb18030 依次回收，失败保持原值。
    """
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError:
        return value  # 含 latin-1 之外的字符：已按 UTF-8 正确解码
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return value


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), name: str = Form(""),
                      db: Session = Depends(get_db),
                      ctx: UserContext = Depends(require_permission("datasource:write"))):
    original = _decode_form_text(file.filename or "upload.csv")
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型 {suffix}，仅支持 csv/tsv/xlsx/xls/json/parquet")

    display_name = _decode_form_text(name).strip() or original
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{original}"
    size_bytes = 0
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(413, f"文件超过大小限制 {MAX_UPLOAD_MB}MB")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size_bytes == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "空文件")

    try:
        columns = read_columns(str(dest))
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"文件解析失败: {exc}")

    pack_id = infer_pack(columns) or "retail_sales"
    assign_pack(display_name, pack_id)
    obj = DataSource(
        name=display_name, type="file", file_path=str(dest),
        file_name=dest.name, file_type=detect_file_type(str(dest)),
        size_bytes=size_bytes, pack_id=pack_id, columns_json=jdump(columns),
        row_count=count_rows(str(dest)),
        workspace_id=ctx.workspace_id,
    )
    db.add(obj)
    db.flush()
    db.commit()
    return obj.to_dict()


@router.post("/db")
def create_db_source(body: DataSourceDbCreate, request: Request,
                     db: Session = Depends(get_db),
                     ctx: UserContext = Depends(require_permission("datasource:write"))):
    cfg = body.config
    ok, msg = test_connection(
        cfg.db_type, cfg.host, cfg.port, cfg.database,
        cfg.username, cfg.password, cfg.sqlite_path,
    )
    if not ok:
        raise HTTPException(400, msg)
    if cfg.db_type == "sqlite" and not cfg.sqlite_path and not cfg.database:
        raise HTTPException(400, "sqlite 需提供文件路径")
    # 落库前加密：没有配置 HELIX_SECRET_KEY 时**明确报错**，绝不静默写明文
    try:
        stored_password = credential_secrets.encrypt_secret(cfg.password or "")
    except credential_secrets.SecretsUnavailable as exc:
        audit.record("datasource.credential_saved", "failed", user_id=ctx.user_id,
                     username=ctx.username, workspace_id=ctx.workspace_id,
                     target=f"datasource:{body.name}",
                     detail={"reason": "missing_encryption_key"}, request=request)
        raise HTTPException(500 if config.IS_PRODUCTION else 400, str(exc))
    obj = DataSource(
        name=body.name, type="db", db_type=cfg.db_type,
        host=cfg.host or "", port=cfg.port, database_name=cfg.database or cfg.sqlite_path,
        username=cfg.username or "", password=stored_password,
        pack_id=body.pack_id, workspace_id=ctx.workspace_id,
    )
    db.add(obj)
    db.flush()
    db.commit()
    # 审计只记"是否带口令、是否已加密"，不记口令本身
    audit.record("datasource.credential_saved", "ok", user_id=ctx.user_id,
                 username=ctx.username, workspace_id=ctx.workspace_id,
                 target=f"datasource:{obj.id}",
                 detail={"db_type": cfg.db_type, "has_password": bool(cfg.password),
                         "encrypted": credential_secrets.is_encrypted(stored_password)},
                 request=request)
    return obj.to_dict()



@router.post("/test")
def test_db(body: DbTestBody,
            _ctx: UserContext = Depends(require_permission("datasource:write"))):
    cfg = body.config
    ok, msg = test_connection(
        cfg.db_type, cfg.host, cfg.port, cfg.database,
        cfg.username, cfg.password, cfg.sqlite_path,
    )
    return {"ok": ok, "message": msg}


@router.get("")
def list_data_sources(db: Session = Depends(get_db),
                      ctx: UserContext = Depends(get_current_context)):
    # 数据源按工作区隔离：本工作区 + 历史全局数据（workspace_id 为空）
    return [ds.to_dict() for ds in db.query(DataSource)
            .filter((DataSource.workspace_id == ctx.workspace_id)
                    | (DataSource.workspace_id.is_(None)))
            .order_by(DataSource.id).all()]


def _get_visible_data_source(db: Session, dsid: int, ctx: UserContext) -> DataSource:
    """按 id 取数据源并校验工作区归属（本工作区或历史全局），越权统一 404。"""
    ds = db.get(DataSource, dsid)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    if ds.workspace_id is not None and ds.workspace_id != ctx.workspace_id:
        raise HTTPException(404, "数据源不存在")
    return ds


@router.get("/{dsid}")
def get_data_source(dsid: int, db: Session = Depends(get_db),
                    ctx: UserContext = Depends(get_current_context)):
    ds = _get_visible_data_source(db, dsid, ctx)
    d = ds.to_dict()
    if ds.pack_id:
        d["pack_detail"] = load_pack(ds.pack_id)
    return d


@router.get("/{dsid}/tables")
def get_tables(dsid: int, db: Session = Depends(get_db),
               ctx: UserContext = Depends(get_current_context)):
    ds = _get_visible_data_source(db, dsid, ctx)
    if ds.type != "db":
        return [{"name": ds.name, "row_estimate": ds.row_count}]
    return list_tables(ds)


@router.get("/{dsid}/preview")
def get_preview(dsid: int, table: str = "", limit: int = 50,
                db: Session = Depends(get_db),
                ctx: UserContext = Depends(get_current_context)):
    ds = _get_visible_data_source(db, dsid, ctx)
    try:
        return preview(ds, table or None, min(limit, 200))
    except Exception as exc:
        raise HTTPException(400, f"预览失败: {exc}")


@router.post("/{dsid}/materialize")
def materialize_source(dsid: int, body: MaterializeBody,
                       db: Session = Depends(get_db),
                       ctx: UserContext = Depends(require_permission("datasource:write"))):
    ds = _get_visible_data_source(db, dsid, ctx)
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
                      db: Session = Depends(get_db),
                      ctx: UserContext = Depends(require_permission("datasource:write"))):
    ds = _get_visible_data_source(db, dsid, ctx)
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
def delete_data_source(dsid: int, db: Session = Depends(get_db),
                       ctx: UserContext = Depends(require_permission("datasource:write"))):
    ds = _get_visible_data_source(db, dsid, ctx)
    if ds.builtin:
        raise HTTPException(400, "内置示例数据源不可删除")
    db.delete(ds)
    db.commit()
    return {"ok": True}
