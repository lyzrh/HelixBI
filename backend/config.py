"""全局配置：路径、LLM 连接、沙箱与数据接入参数。

单一配置入口——Agent 内核（`backend/agent/`）、领域层（`backend/analysis|skills|...`）
与 API 层（`backend/routers/`）都从这里读取，不再保留第二份配置来源。

约定：
- 路径类常量集中在顶部，运行期目录在此确保存在。
- LLM 连接信息支持 `.env` 与设置中心热更新（`update_llm_config`，保存即生效）。
"""

import os
from pathlib import Path

from dotenv import load_dotenv, set_key

# ---- 路径 ----

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_DIR = PROJECT_ROOT / "uploads"
MATERIALIZED_DIR = DATA_DIR / "materialized"
SEMANTIC_PACKS_DIR = PROJECT_ROOT / "semantic_packs"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

DB_PATH = Path(os.getenv("APP_DB_PATH", str(DATA_DIR / "app.db")))

# ---- LLM ----

ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
# 回答创意度（设置页可改，运行时热更新；默认 0 保证代码生成稳定）
LLM_TEMPERATURE = 0.0

# ---- 沙箱 ----

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "helix-sandbox:latest")
MAX_FIX_ATTEMPTS = int(os.getenv("MAX_FIX_ATTEMPTS", "3"))
SANDBOX_CPUS = float(os.getenv("SANDBOX_CPUS", "2"))
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "2g")
CODE_TIMEOUT_SECONDS = int(os.getenv("CODE_TIMEOUT_SECONDS", "120"))

# ---- 数据接入 ----

# DB 数据源物化上限（沙箱无网络，分析数据一律以文件进入 /data）
MATERIALIZED_MAX_ROWS = int(os.getenv("MATERIALIZED_MAX_ROWS", "500000"))
MATERIALIZED_TTL_HOURS = int(os.getenv("MATERIALIZED_TTL_HOURS", "24"))

# 上传文件大小上限（MB）
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))


def update_llm_config(base_url: str | None = None, api_key: str | None = None,
                      model: str | None = None) -> None:
    """运行时更新 LLM 配置（模块级变量 + 持久化回 .env）。

    传入 None 的字段保持不变；空串视为清空该字段。
    """
    global OPENAI_BASE_URL, OPENAI_API_KEY, MODEL_NAME
    if base_url is not None:
        OPENAI_BASE_URL = base_url.strip() or "https://api.openai.com/v1"
    if api_key is not None:
        OPENAI_API_KEY = api_key.strip()
    if model is not None:
        MODEL_NAME = model.strip() or "gpt-4o-mini"

    # .env 不存在时创建（set_key 需要）
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")
    set_key(str(ENV_PATH), "OPENAI_BASE_URL", OPENAI_BASE_URL)
    set_key(str(ENV_PATH), "OPENAI_API_KEY", OPENAI_API_KEY)
    set_key(str(ENV_PATH), "MODEL_NAME", MODEL_NAME)


# 运行期目录：首次导入即确保存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
MATERIALIZED_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
# runs 目录被 main.py 挂载为静态目录（StaticFiles 要求存在），
# 全新环境（如 CI checkout）没有该目录时会在 import backend.main 时崩溃
RUNS_DIR.mkdir(parents=True, exist_ok=True)

__all__ = [
    "CODE_TIMEOUT_SECONDS", "DATA_DIR", "DB_PATH", "ENV_PATH", "FRONTEND_DIST",
    "LLM_TEMPERATURE", "MATERIALIZED_DIR", "MATERIALIZED_MAX_ROWS",
    "MATERIALIZED_TTL_HOURS", "MAX_FIX_ATTEMPTS", "MAX_UPLOAD_MB", "MODEL_NAME",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "PROJECT_ROOT", "RUNS_DIR",
    "SANDBOX_CPUS", "SANDBOX_IMAGE", "SANDBOX_MEMORY", "SEMANTIC_PACKS_DIR",
    "UPLOADS_DIR", "update_llm_config",
]
