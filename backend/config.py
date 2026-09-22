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

# ---- Production Runtime V1（warm pool / 并发控制 / 超时取消）----
#
# 沙箱预热池：0 = 关闭（每次执行仍是 docker run --rm 冷启动）。
# 打开后启动期后台预热 N 个 `docker run -d` 容器（同样的 network none / CPU /
# 内存 / pids 限制），执行走 `docker exec`，执行完清理工作目录再归还。
# 容器用满 MAX_CONTAINER_USES 次强制回收重建（限制残留数据寿命）；
# 异常容器立即销毁并后台补充；池满时调用方排队（最多 QUEUE 个等待者，
# 超过 SANDBOX_ACQUIRE_TIMEOUT 秒拿不到就报 PoolTimeout，由上层降级为
# 临时冷容器——绝不无限等、也绝不超开容器）。
SANDBOX_POOL_SIZE = int(os.getenv("SANDBOX_POOL_SIZE", "2"))
SANDBOX_POOL_MAX_USES = int(os.getenv("SANDBOX_POOL_MAX_USES", "25"))
SANDBOX_POOL_QUEUE = int(os.getenv("SANDBOX_POOL_QUEUE", "8"))
SANDBOX_ACQUIRE_TIMEOUT = float(os.getenv("SANDBOX_ACQUIRE_TIMEOUT", "30"))
SANDBOX_STARTUP_TIMEOUT = float(os.getenv("SANDBOX_STARTUP_TIMEOUT", "60"))

# 全局并发：同一时刻最多多少个分析运行真正执行（含 Skill 重放）；
# 超出的进入等待队列（最多 RUN_QUEUE_SIZE 个，等超过 RUN_QUEUE_TIMEOUT 秒
# 明确拒绝）。MAX_CONCURRENT_PER_USER 防止单个用户占满全部资源。
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "4"))
MAX_CONCURRENT_PER_USER = int(os.getenv("MAX_CONCURRENT_PER_USER", "2"))
RUN_QUEUE_SIZE = int(os.getenv("RUN_QUEUE_SIZE", "16"))
RUN_QUEUE_TIMEOUT = float(os.getenv("RUN_QUEUE_TIMEOUT", "60"))

# 整轮运行超时（秒；0 = 不限制）。覆盖排队 + 代码生成 + 沙箱执行 + 自修复 +
# 结论整理的全过程；Self-Repair 的每一轮修复都要先过这道门，
# 因此修复次数再多也不可能突破总时限。
TOTAL_RUN_TIMEOUT_SECONDS = int(os.getenv("TOTAL_RUN_TIMEOUT_SECONDS", "900"))

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


def _opt_int(name: str, default: str = "0") -> int | None:
    """可选的整数限额：0 / 空 / 负数一律视为「不限制」（None）。"""
    raw = (os.getenv(name, default) or "").strip()
    try:
        value = int(float(raw))
    except ValueError:
        return None
    return value if value > 0 else None


def _opt_float(name: str, default: str = "0") -> float | None:
    raw = (os.getenv(name, default) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# ---- LLM 运行预算（Cost & Latency Optimization V1）----
#
# 「达不到预算就停」而不是「算完再说」：预检在调用之前，超限的那次调用根本不发出。
# 0 / 未配置 = 不限制（引入预算前的行为保持不变）；默认值给的是**宽松上限**，
# 只拦病态循环（复读重试、超大 prompt 反复重发），不拦正常分析。
MAX_LLM_CALLS_PER_RUN = _opt_int("MAX_LLM_CALLS_PER_RUN", "8")
MAX_INPUT_TOKENS_PER_RUN = _opt_int("MAX_INPUT_TOKENS_PER_RUN", "40000")
MAX_OUTPUT_TOKENS_PER_RUN = _opt_int("MAX_OUTPUT_TOKENS_PER_RUN", "12000")
MAX_TOTAL_TOKENS_PER_RUN = _opt_int("MAX_TOTAL_TOKENS_PER_RUN", "60000")
# 成本预算只有在配置了 LLM_PRICE_* 单价时才可判定（否则无法估算，按不限制处理）
MAX_RUN_COST_USD = _opt_float("MAX_RUN_COST_USD", "0")
# 修复次数的运行级收紧（0 = 跟随 MAX_FIX_ATTEMPTS；永远只可能更小，不会放大上限）
MAX_REPAIR_ATTEMPTS = _opt_int("MAX_REPAIR_ATTEMPTS", "0")

# ---- 成本 / 延迟优化开关 ----
#
# CONTEXT_POLICY=v1 是**冻结基线**（改造前的 prompt 装配：全量语义层 / 两例 few-shot /
# 修复块重复注入文件清单），只为评估对照存在；线上默认 v2。
CONTEXT_POLICY = os.getenv("CONTEXT_POLICY", "v2")
# 意图解析：auto = 确定性解析满足门条件就不调 LLM；llm = 冻结的旧行为
INTENT_MODE = os.getenv("INTENT_MODE", "auto")
# 快路径最低置信度：0.60 = 命中指标 + 至少一个结构化信号（维度/时间/对比/排序）
INTENT_FASTPATH_MIN_CONFIDENCE = float(os.getenv("INTENT_FASTPATH_MIN_CONFIDENCE", "0.60"))
# 追问推荐：deterministic（0 次 LLM）| hybrid（凑不满才补 LLM）| llm（冻结基线）| off
FOLLOWUP_MODE = os.getenv("FOLLOWUP_MODE", "hybrid")
# 上下文裁剪参数（v2 生效；调大即更接近基线行为）
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "2"))
HISTORY_ANSWER_CHARS = int(os.getenv("HISTORY_ANSWER_CHARS", "300"))
SKILL_FEWSHOT_MAX = int(os.getenv("SKILL_FEWSHOT_MAX", "1"))
SKILL_EXAMPLE_MAX_LINES = int(os.getenv("SKILL_EXAMPLE_MAX_LINES", "60"))
REPAIR_OUTPUT_CHARS = int(os.getenv("REPAIR_OUTPUT_CHARS", "1500"))
SUMMARIZE_TABLE_ROWS = int(os.getenv("SUMMARIZE_TABLE_ROWS", "30"))
# 数据画像缓存（按文件指纹失效；关掉则每次重新读盘）
PROFILE_CACHE_ENABLED = (os.getenv("PROFILE_CACHE_ENABLED", "1").strip().lower()
                         not in ("0", "false", "no", "off"))


# ---- 安全（Security Hardening V1）----
#
# 三类密钥都只从环境变量读，**不写进数据库、不写进 Git**：
# - HELIX_JWT_SECRET：JWT 签名密钥（多进程部署必须显式配置，否则各进程密钥不同）；
# - HELIX_SECRET_KEY：数据源凭据的**应用级加密主密钥**（缺省时拒绝保存数据库密码，
#   绝不静默降级为明文）；
# - HELIX_ENV：运行环境（development / production），只影响错误提示的语气与
#   是否允许「兼容读取历史明文」这类宽松行为。
ENV_NAME = (os.getenv("HELIX_ENV", "development") or "development").strip().lower()
IS_PRODUCTION = ENV_NAME in ("production", "prod")
SECRET_KEY = os.getenv("HELIX_SECRET_KEY", "")

# Token 生命周期：access 短期有效，refresh 长但可撤销（DB 存储，多进程安全）
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("HELIX_ACCESS_TOKEN_TTL", "3600"))
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("HELIX_REFRESH_TOKEN_TTL", str(30 * 24 * 3600)))


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
    "ACCESS_TOKEN_TTL_SECONDS", "CODE_TIMEOUT_SECONDS", "CONTEXT_POLICY", "DATA_DIR",
    "DB_PATH", "ENV_NAME", "ENV_PATH", "FOLLOWUP_MODE", "FRONTEND_DIST",
    "HISTORY_ANSWER_CHARS", "HISTORY_TURNS", "INTENT_FASTPATH_MIN_CONFIDENCE",
    "INTENT_MODE", "IS_PRODUCTION",
    "LLM_PRICE_INPUT_PER_MTOK", "LLM_PRICE_OUTPUT_PER_MTOK",
    "LLM_TEMPERATURE", "MATERIALIZED_DIR", "MATERIALIZED_MAX_ROWS",
    "MATERIALIZED_TTL_HOURS", "MAX_FIX_ATTEMPTS", "MAX_INPUT_TOKENS_PER_RUN",
    "MAX_LLM_CALLS_PER_RUN", "MAX_OUTPUT_TOKENS_PER_RUN", "MAX_REPAIR_ATTEMPTS",
    "MAX_RUN_COST_USD",     "MAX_TOTAL_TOKENS_PER_RUN", "MAX_UPLOAD_MB", "MAX_CONCURRENT_PER_USER",
    "MAX_CONCURRENT_RUNS", "MODEL_NAME",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "PROFILE_CACHE_ENABLED", "PROJECT_ROOT",
    "REFRESH_TOKEN_TTL_SECONDS", "REPAIR_OUTPUT_CHARS", "REPAIR_POLICY",
    "REPAIR_REPEAT_LIMIT", "RUNS_DIR", "RUN_QUEUE_SIZE", "RUN_QUEUE_TIMEOUT",
    "SECRET_KEY", "SANDBOX_ACQUIRE_TIMEOUT", "SANDBOX_CPUS", "SANDBOX_IMAGE",
    "SANDBOX_MEMORY", "SANDBOX_POOL_MAX_USES", "SANDBOX_POOL_QUEUE",
    "SANDBOX_POOL_SIZE", "SANDBOX_STARTUP_TIMEOUT", "SEMANTIC_PACKS_DIR",
    "SKILL_ADMISSION_HIGH", "SKILL_ADMISSION_LOW", "SKILL_ADMISSION_MARGIN",
    "SKILL_EXAMPLE_MAX_LINES", "SKILL_FEWSHOT_MAX",
    "SKILL_RETRIEVAL_MIN_SCORE", "SKILL_RETRIEVAL_TOP_K", "SKILL_RETRIEVAL_WEIGHTS",
    "SUMMARIZE_TABLE_ROWS", "TOTAL_RUN_TIMEOUT_SECONDS", "UPLOADS_DIR",
    "update_llm_config",
]
