"""FastAPI 入口：绎数 · Helix BI 服务。

启动：python -m uvicorn backend.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import FRONTEND_DIST, MATERIALIZED_DIR, RUNS_DIR, UPLOADS_DIR
from backend.db import init_db
from backend.routers import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 启动时恢复用户偏好（创意度温度等，设置页保存过则重启仍生效）
    from backend.db import SessionLocal
    from backend.routers.settings import _read_kv, DEFAULT_PREFERENCES, K_PREFERENCES
    from app import config as app_config
    with SessionLocal() as db:
        prefs = _read_kv(db, K_PREFERENCES, DEFAULT_PREFERENCES)
    app_config.LLM_TEMPERATURE = prefs.get("temperature", 0.0)
    from backend.services.insight_scheduler import start_scheduler, stop_scheduler
    start_scheduler()  # 定时洞察扫描后台循环
    yield
    stop_scheduler()


app = FastAPI(title="绎数 · Helix BI 智能数据工作台", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# 产物与数据静态目录（图表 PNG / 上传文件 / 物化 parquet）
app.mount("/runs", StaticFiles(directory=str(RUNS_DIR)), name="runs")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/data", StaticFiles(directory=str(MATERIALIZED_DIR.parent)), name="data")

# 生产模式：serve 前端构建产物（SPA fallback）
if FRONTEND_DIST.exists():
    from fastapi.responses import FileResponse

    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")),
              name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith(("api/", "runs/", "uploads/", "data/", "docs", "openapi")):
            from fastapi import HTTPException
            raise HTTPException(404)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # index.html 禁止缓存：确保浏览器始终加载最新 hash 的 JS bundle
        return FileResponse(FRONTEND_DIST / "index.html",
                            headers={"Cache-Control": "no-cache"})
