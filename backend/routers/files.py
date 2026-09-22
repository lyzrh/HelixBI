"""鉴权文件下发：替代原先的 /runs、/uploads、/data 静态挂载（已知安全缺口）。

所有文件读取都必须经过 `get_current_context`，**再按资源归属工作区校验**，
并按资源类型挂上权限点：

| 路径 | 资源 | 权限点 | 归属校验 |
| --- | --- | --- | --- |
| `/runs/{run_key}/{file_path}` | 分析产物（图表 / result.json） | `analysis:read` | Run → Session.workspace_id（key 支持运行目录名或 Run.id） |
| `/uploads/{file_name}` | 上传的源数据 | `datasource:read` | DataSource.workspace_id（且必须是服务登记过的文件名） |
| `/data/materialized/{file_path}` | DB 物化 parquet 缓存 | `datasource:read` | DataSource.materialized_path 精确匹配 |

安全要点：

1. **路径遍历防护**：所有拼接走 `_safe_join`（resolve 后必须仍在基目录内），
   上传文件只认 `DataSource.file_path` 的 basename，不接受任意文件名；
2. **不暴露存在性**：资源不存在、孤儿运行（会话已删）、跨工作区一律 **404**；
3. **不放行匿名**：路由级依赖 `get_current_context`（无 token → 401）；
4. **被拒留痕**：跨工作区 / 遍历 / 未登记文件都写 `artifact.access_denied` 审计；
5. **data/ 只暴露 materialized 子目录**——原先整个 data/ 可下载，连 SQLite 元数据库都在里面。
"""

import os
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.auth import audit
from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.config import MATERIALIZED_DIR, RUNS_DIR, UPLOADS_DIR
from pathlib import Path
from backend.db import get_db
from backend.models import DataSource, Run, Session as DbSession

router = APIRouter(dependencies=[Depends(get_current_context)])

# 图片 / PDF 内联展示（前端图表与预览直接可用），其余按附件下载
_INLINE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".html")
_INLINE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".pdf": "application/pdf", ".html": "text/html",
}


def _safe_join(base, rel: str):
    """路径安全拼接：拒绝越出基目录的路径遍历（`../`、绝对路径、符号链接逃逸）。"""
    base = base.resolve()
    target = (base / rel).resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise HTTPException(404, "文件不存在")
    return target


def _serve(base, rel: str, request: Request, ctx: UserContext, target: str):
    try:
        path = _safe_join(base, rel)
    except HTTPException:
        audit.record_denied("artifact.access_denied", ctx=ctx, target=target,
                            detail={"reason": "path_traversal"}, request=request)
        raise
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    suffix = path.suffix.lower()
    disposition = "inline" if suffix in _INLINE_SUFFIXES else "attachment"
    media_type = _INLINE_MIME.get(suffix)
    return FileResponse(path, filename=urllib.parse.quote(path.name),
                        media_type=media_type, content_disposition_type=disposition)


def _deny(request: Request, ctx: UserContext, target: str, reason: str):
    """跨工作区 / 越权访问：统一 404（不暴露存在性）并留审计。"""
    audit.record_denied("artifact.access_denied", ctx=ctx, target=target,
                        detail={"reason": reason}, request=request)
    raise HTTPException(404, "文件不存在")


def _resolve_run(db: Session, run_key: str) -> tuple[bool, int | None, Path | None]:
    """按 URL 里的 key 定位运行 → (是否存在且可判定归属, 归属工作区, 产物根目录)。

    key 有两种来源，必须都支持：

    1. **运行目录名**（`/runs/<run_id 字符串>/out/chart.png`）——分析链路与 Skill 重放
       生成的产物 URL 就是这一种（目录名不是数字）；
    2. 数据库主键（数字）——便于按 `Run.id` 直接取。

    归属不再只看"目录名像不像"，而是反查 `Run.run_dir` 的**真实目录名**并与 key 精确比对，
    因此拿到别人目录名也没用：归属仍由 `Run → Session.workspace_id` 决定。
    孤儿运行（会话已删）一律视为不可判定 → 404。
    """
    run = None
    key = (run_key or "").strip()
    if not key or "/" in key or "\\" in key or key in (".", ".."):
        return False, None, None
    if key.isdigit():
        run = db.get(Run, int(key))
    if run is None:
        # 按目录名反查：把 run_dir 的分隔符归一化成 "/" 再匹配（Windows 存的是 "\"）。
        # LIKE 转义符用 "!"（不能拿 "/" 当转义符——那样模式里的分隔符会把自己吃掉）。
        from sqlalchemy import func, or_

        escaped = key.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        normalized = func.replace(Run.run_dir, "\\", "/")
        candidates = (db.query(Run)
                      .filter(or_(normalized.like(f"%/{escaped}/%", escape="!"),
                                  normalized.like(f"%/{escaped}", escape="!")))
                      .order_by(Run.id.desc()).limit(20).all())
        for candidate in candidates:
            if not candidate.run_dir:
                continue
            path = Path(candidate.run_dir)
            # run_dir 指向运行的 out 目录（runs/<key>/out），URL 的 key 是它的父目录名
            if key in (path.name, path.parent.name):
                run = candidate
                break
    if run is None or not run.session_id:
        return False, None, None
    session = db.get(DbSession, run.session_id)
    if not session or not run.run_dir:
        return False, None, None
    out_dir = Path(run.run_dir)
    # 产物根目录：`runs/<key>`（URL 里的 `out/xxx.png` 相对它解析）
    base = out_dir.parent if out_dir.name == "out" else out_dir
    return True, session.workspace_id, base



@router.get("/runs/{run_key}/{file_path:path}")
def serve_run_file(run_key: str, file_path: str, request: Request,
                   db: Session = Depends(get_db),
                   ctx: UserContext = Depends(require_permission("analysis:read"))):
    target = f"runs/{run_key}/{file_path}"
    found, ws, base = _resolve_run(db, run_key)
    if not found or base is None:
        raise HTTPException(404, "文件不存在")
    if ws is not None and ws != ctx.workspace_id:
        _deny(request, ctx, target, "cross_workspace_run")
    return _serve(base, file_path, request, ctx, target)



@router.get("/uploads/{file_name}")
def serve_upload(file_name: str, request: Request, db: Session = Depends(get_db),
                 ctx: UserContext = Depends(require_permission("datasource:read"))):
    target = f"uploads/{file_name}"
    ds = (db.query(DataSource)
          .filter(DataSource.file_name == file_name).first())
    if not ds or not ds.file_path:
        raise HTTPException(404, "文件不存在")
    if ds.workspace_id is not None and ds.workspace_id != ctx.workspace_id:
        _deny(request, ctx, target, "cross_workspace_datasource")
    # 只允许服务登记过的上传文件本体（防路径遍历，不认 file_path 之外的任意文件）
    return _serve(UPLOADS_DIR, os.path.basename(ds.file_path), request, ctx, target)


@router.get("/data/materialized/{file_path:path}")
def serve_materialized(file_path: str, request: Request, db: Session = Depends(get_db),
                       ctx: UserContext = Depends(require_permission("datasource:read"))):
    target = f"data/materialized/{file_path}"
    joined = _safe_join(MATERIALIZED_DIR, file_path)
    ds = (db.query(DataSource)
          .filter(DataSource.materialized_path == str(joined)).first())
    if not ds:
        audit.record_denied("artifact.access_denied", ctx=ctx, target=target,
                            detail={"reason": "unregistered_path"}, request=request)
        raise HTTPException(404, "文件不存在")
    if ds.workspace_id is not None and ds.workspace_id != ctx.workspace_id:
        _deny(request, ctx, target, "cross_workspace_materialized")
    return _serve(MATERIALIZED_DIR, file_path, request, ctx, target)
