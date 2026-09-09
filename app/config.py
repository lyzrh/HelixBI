import os
from pathlib import Path

from dotenv import load_dotenv, set_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"

ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
# 回答创意度（设置页可改，运行时热更新；默认 0 保证代码生成稳定）
LLM_TEMPERATURE = 0.0

SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "daa-sandbox:latest")
MAX_FIX_ATTEMPTS = int(os.getenv("MAX_FIX_ATTEMPTS", "3"))
SANDBOX_CPUS = float(os.getenv("SANDBOX_CPUS", "2"))
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "2g")
CODE_TIMEOUT_SECONDS = int(os.getenv("CODE_TIMEOUT_SECONDS", "120"))


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
