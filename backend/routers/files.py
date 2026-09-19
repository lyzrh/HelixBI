"""鉴权文件下发：替代原先的 /runs、/uploads、/data 静态挂载（已知安全缺口）。

所有文件读取必须经过 get_current_context，并按资源归属工作区校验：
- /runs/{rid}/...        → Run 产物（图表等），按 run.session 的 workspace 校验
- /uploads/{file_name}   → 上传源数据文件，按 DataSource.workspace_id 校验
- /data/materialized/... → DB 物化 parquet 缓存，按 DataSource.workspace_id 校验
                           （不再暴露整个 data/ 目录——原先连 SQLite 元数据库都可匿名下载）

统一约定：资源不存在或不属于当前工作区一律 404，不暴露存在性。
"""

import os
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.auth.context import UserContext
from backend.auth.deps import get_current_context
from backend.config import MATERIALIZED_DIR, RUNS_DIR, UPLOADS_DIR
from backend.db import get_db
from backend.models import DataSource, Run, Session as DbSession

router = APIRouter(dependencies=[Depends(get_current_context)])


def _safe_join(base, rel: str):
    """路径安全拼接：拒绝越出基目录的路径遍历。"""
    base = base.resolve()
    target = (base / rel).resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise HTTPException(404, "文件不存在")
    return target


def _serve(base, rel: str):
    path = _safe_join(base, rel)
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=urllib.parse.quote(path.name))


def _run_workspace(db: Session, rid: int) -> int | None:
    run = db.get(Run, rid)
    if not run:
        raise HTTPException(404, "文件不存在")
    session = db.get(DbSession, run.session_id) if run.session_id else None
    return session.workspace_id if session else None


@router.get("/runs/{rid}/{file_path:path}")
def serve_run_file(rid: int, file_path: str, db: Session = Depends(get_db),
                   ctx: UserContext = Depends(get_current_context)):
    if _run_workspace(db, rid) not in (ctx.workspace_id, None):
        raise HTTPException(404, "文件不存在")
    return _serve(RUNS_DIR / str(rid), file_path)


@router.get("/uploads/{file_name}")
def serve_upload(file_name: str, db: Session = Depends(get_db),
                 ctx: UserContext = Depends(get_current_context)):
    ds = (db.query(DataSource)
          .filter(DataSource.file_name == file_name).first())
    if not ds or not ds.file_path:
        raise HTTPException(404, "文件不存在")
    if ds.workspace_id not in (ctx.workspace_id, None):
        raise HTTPException(404, "文件不存在")
    # 只允许服务登记过的上传文件本体（防路径遍历，不认 file_path 之外的任意文件）
    return _serve(UPLOADS_DIR, os.path.basename(ds.file_path))


@router.get("/data/materialized/{file_path:path}")
def serve_materialized(file_path: str, db: Session = Depends(get_db),
                       ctx: UserContext = Depends(get_current_context)):
    target = _safe_join(MATERIALIZED_DIR, file_path)
    ds = (db.query(DataSource)
          .filter(DataSource.materialized_path == str(target)).first())
    if not ds:
        raise HTTPException(404, "文件不存在")
    if ds.workspace_id not in (ctx.workspace_id, None):
        raise HTTPException(404, "文件不存在")
    return _serve(MATERIALIZED_DIR, file_path)
