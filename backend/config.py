"""backend 侧配置：元数据库、物化缓存、前端产物路径。

复用 app.config.PROJECT_ROOT，保证与内核（runs/uploads/semantics）同根。
"""

import os
from pathlib import Path

from app.config import PROJECT_ROOT, RUNS_DIR

DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = Path(os.getenv("APP_DB_PATH", str(DATA_DIR / "app.db")))
UPLOADS_DIR = PROJECT_ROOT / "uploads"
MATERIALIZED_DIR = DATA_DIR / "materialized"

# DB 数据源物化上限（沙箱无网络，分析数据一律以文件进入 /data）
MATERIALIZED_MAX_ROWS = int(os.getenv("MATERIALIZED_MAX_ROWS", "500000"))
MATERIALIZED_TTL_HOURS = int(os.getenv("MATERIALIZED_TTL_HOURS", "24"))

# 上传文件大小上限（MB）
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MATERIALIZED_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

__all__ = [
    "PROJECT_ROOT", "RUNS_DIR", "DATA_DIR", "DB_PATH", "UPLOADS_DIR",
    "MATERIALIZED_DIR", "MATERIALIZED_MAX_ROWS", "MATERIALIZED_TTL_HOURS",
    "MAX_UPLOAD_MB", "FRONTEND_DIST",
]
