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

# ---- Self-Repair（错误分类 → 定向修复 → 有界重试 → 兜底）----
#
# MAX_FIX_ATTEMPTS 仍是**唯一全局上限**（每类错误的独立额度只会在它之内更早收手），
# 语义与旧版一致：最多执行 1 + MAX_FIX_ATTEMPTS 次。
#
# REPAIR_POLICY=v1 是冻结的 Baseline（无差别重试、不分类、不复读检测），
# 只为 `--repair-benchmark` 的 V1 vs V2 对照存在，线上不要用。
REPAIR_POLICY = os.getenv("REPAIR_POLICY", "v2")
# 允许的「连续同类错误」次数：1 = 第二次同类错误即视为修复无进展，提前终止重试
REPAIR_REPEAT_LIMIT = int(os.getenv("REPAIR_REPEAT_LIMIT", "1"))

# ---- 数据接入 ----

# DB 数据源物化上限（沙箱无网络，分析数据一律以文件进入 /data）
MATERIALIZED_MAX_ROWS = int(os.getenv("MATERIALIZED_MAX_ROWS", "500000"))
MATERIALIZED_TTL_HOURS = int(os.getenv("MATERIALIZED_TTL_HOURS", "24"))

# 上传文件大小上限（MB）
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))

# ---- Skill Retrieval / Replay Admission（第二阶段检索与重放准入）----
#
# 全部集中在此，便于评测标定与线上调参；`backend/skills/retrieval.py` 只读取这些值，
# 不把权重写进业务代码。
#
# 权重来自 `python -m backend.evaluation --tune-weights` 在固定问题集上的
# 坐标下降标定（目标：先保 False Replay = 0，再最大化 Top1 / Replay Recall）。
# 标定过程与候选权重详见 docs/skill-retrieval-v2.md。

SKILL_RETRIEVAL_TOP_K = int(os.getenv("SKILL_RETRIEVAL_TOP_K", "5"))
# few-shot 注入的相关性下限：低于该加权分的候选**不进 prompt**。
# 注意它不作用于召回本身（召回要广），也不作用于重放准入（准入用 SKILL_ADMISSION_LOW）。
SKILL_RETRIEVAL_MIN_SCORE = float(os.getenv("SKILL_RETRIEVAL_MIN_SCORE", "0.10"))

# 打分权重（会自动归一化）：可解释的线性加权，不用黑盒模型
#
# 比最初设想多出 `ranking` / `time` 两路信号，理由是评测数据逼出来的：
# 「销售额最高的门店」与「销售额最低的门店」在词面/指标/维度上完全一致，
# 「近 30 天趋势」与「近 90 天趋势」也完全一致——但代码里的 sort 方向与
# 时间窗口是写死的，仅靠 metric/dimension/type 无法拦住这两类误重放。
SKILL_RETRIEVAL_WEIGHTS = {
    "semantic": float(os.getenv("SKILL_W_SEMANTIC", "0.28")),
    "metric": float(os.getenv("SKILL_W_METRIC", "0.24")),
    "dimension": float(os.getenv("SKILL_W_DIMENSION", "0.17")),
    "type": float(os.getenv("SKILL_W_TYPE", "0.13")),
    "ranking": float(os.getenv("SKILL_W_RANKING", "0.06")),
    "time": float(os.getenv("SKILL_W_TIME", "0.05")),
    "datasource": float(os.getenv("SKILL_W_DATASOURCE", "0.04")),
    "history": float(os.getenv("SKILL_W_HISTORY", "0.03")),
}

# 准入阈值：>= HIGH 直接重放；[LOW, HIGH) 进入严格校验；< LOW 放弃重放
SKILL_ADMISSION_HIGH = float(os.getenv("SKILL_ADMISSION_HIGH", "0.72"))
SKILL_ADMISSION_LOW = float(os.getenv("SKILL_ADMISSION_LOW", "0.50"))
# Top1 与 Top2 的最小分差：低于该值视为「候选歧义」，不得直接重放
SKILL_ADMISSION_MARGIN = float(os.getenv("SKILL_ADMISSION_MARGIN", "0.03"))

# ---- LLM 计价（仅用于评估的成本估算；未配置则报告里显示「未配置单价」）----
# 默认 0 表示「不假设价格」——避免用编造的单价算出好看的美元数字。
LLM_PRICE_INPUT_PER_MTOK = float(os.getenv("LLM_PRICE_INPUT_PER_MTOK", "0"))
LLM_PRICE_OUTPUT_PER_MTOK = float(os.getenv("LLM_PRICE_OUTPUT_PER_MTOK", "0"))


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
    "LLM_PRICE_INPUT_PER_MTOK", "LLM_PRICE_OUTPUT_PER_MTOK",
    "LLM_TEMPERATURE", "MATERIALIZED_DIR", "MATERIALIZED_MAX_ROWS",
    "MATERIALIZED_TTL_HOURS", "MAX_FIX_ATTEMPTS", "MAX_UPLOAD_MB", "MODEL_NAME",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "PROJECT_ROOT", "REPAIR_POLICY",
    "REPAIR_REPEAT_LIMIT", "RUNS_DIR",
    "SANDBOX_CPUS", "SANDBOX_IMAGE", "SANDBOX_MEMORY", "SEMANTIC_PACKS_DIR",
    "SKILL_ADMISSION_HIGH", "SKILL_ADMISSION_LOW", "SKILL_ADMISSION_MARGIN",
    "SKILL_RETRIEVAL_MIN_SCORE", "SKILL_RETRIEVAL_TOP_K", "SKILL_RETRIEVAL_WEIGHTS",
    "UPLOADS_DIR", "update_llm_config",
]
