"""效率模型：把「路由决策」翻译成 LLM 调用 / Token / 延迟 / 成本。

诚实性约定（与 `backend/evaluation/runner.py` 的 skipped 原则一致）：

- **能实测的就实测**：Agent 路径的 LLM 调用次数 / 输入输出 token / 耗时的锚点来自
  `TokenUsage` 与 `Run` 表（真实历史遥测），并记录样本量与来源。
- **测不到的不编造**：本机 / CI 没有 Docker 沙箱时，重放路径的**沙箱执行耗时**无法实测，
  这里只计算「LLM 侧」的量；报告里把沙箱耗时标注为「未采集」，而不是填一个估计值。
- **成本不假设单价**：`LLM_PRICE_*` 未配置时 cost 返回 None，渲染成「未配置单价」。
  用编造的单价算出好看的美元数字比不报更糟。

这样 V1 与 V2 的差异仍然是可比、可复现的：沙箱执行成本在两条策略下完全相同，
因此 LLM 侧差异就是策略差异。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from backend import config

# 本机实测锚点（2026-09-21，data/app.db，4 次成功运行）——仅在读不到遥测时使用。
# 数字来自 TokenUsage / Run 表的真实记录，不是估计值；但样本很小，故必须标注来源。
MEASURED_FALLBACK_PROFILE = {
    "runs": 3,
    "llm_calls": 4.0,          # parse_intent + generate_code + summarize + followup
    "input_tokens": 2095.0,    # (2261 + 4031 + 26644/3) / 3 ≈ 2095
    "output_tokens": 731.0,
    "latency_ms": 7667.0,      # Run.duration_ms（唯一一条带耗时的完整运行）
    "self_repair_rate": 0.0,   # 历史 attempts 均为 1
}


@dataclass(frozen=True)
class AgentProfile:
    """Agent 路径的单次画像（LLM 调用 / token / 延迟 / 自修复率）。"""

    runs: float
    llm_calls: float
    input_tokens: float
    output_tokens: float
    latency_ms: float
    self_repair_rate: float
    source: str

    @property
    def total_tokens(self) -> float:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "runs": self.runs, "llm_calls": round(self.llm_calls, 3),
            "input_tokens": round(self.input_tokens, 1),
            "output_tokens": round(self.output_tokens, 1),
            "total_tokens": round(self.total_tokens, 1),
            "latency_ms": round(self.latency_ms, 1),
            "self_repair_rate": round(self.self_repair_rate, 4),
            "source": self.source,
        }


def load_agent_profile(db_path: str | None = None) -> AgentProfile:
    """从真实遥测读 Agent 画像；遥测不足则回退到实测锚点常量。"""
    path = db_path or str(config.DB_PATH)
    try:
        measured = _telemetry(path)
    except Exception:  # noqa: BLE001 — 评估不得因为读不到库而中断
        measured = None
    if measured and measured["runs"] >= 1:
        return AgentProfile(
            runs=measured["runs"], llm_calls=measured["llm_calls"],
            input_tokens=measured["input_tokens"], output_tokens=measured["output_tokens"],
            latency_ms=measured["latency_ms"], self_repair_rate=measured["self_repair_rate"],
            source=f"measured:{path}",
        )
    f = MEASURED_FALLBACK_PROFILE
    return AgentProfile(
        runs=f["runs"], llm_calls=f["llm_calls"], input_tokens=f["input_tokens"],
        output_tokens=f["output_tokens"], latency_ms=f["latency_ms"],
        self_repair_rate=f["self_repair_rate"],
        source="baked-in measured anchor (2026-09-21)",
    )


def _telemetry(path: str) -> dict | None:
    """按 run 聚合 TokenUsage，并取 Run 的耗时 / 自修复率。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT run_id, SUM(input_tokens), SUM(output_tokens), COUNT(*) "
            "FROM token_usages WHERE run_id IS NOT NULL GROUP BY run_id").fetchall()
        runs = conn.execute(
            "SELECT duration_ms, attempts FROM runs WHERE duration_ms > 0").fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    n = len(rows)
    durations = [r[0] for r in runs if r[0]] or [0.0]
    repairs = [1 for r in runs if (r[1] or 0) > 1]
    return {
        "runs": float(n),
        "llm_calls": sum(r[3] for r in rows) / n,
        "input_tokens": sum(r[1] or 0 for r in rows) / n,
        "output_tokens": sum(r[2] or 0 for r in rows) / n,
        "latency_ms": sum(durations) / len(durations),
        "self_repair_rate": (len(repairs) / len(runs)) if runs else 0.0,
    }


def replay_profile() -> dict:
    """重放路径的画像：LLM 侧为零；沙箱耗时本机不可实测 → 标注未采集。"""
    return {"llm_calls": 0.0, "input_tokens": 0.0, "output_tokens": 0.0,
            "latency_ms": 0.0, "sandbox_ms": None}


def estimated_cost(input_tokens: float, output_tokens: float) -> float | None:
    """按配置单价估算成本；未配置单价返回 None（渲染为「未配置单价」）。"""
    pi = config.LLM_PRICE_INPUT_PER_MTOK
    po = config.LLM_PRICE_OUTPUT_PER_MTOK
    if pi <= 0 and po <= 0:
        return None
    return input_tokens / 1e6 * pi + output_tokens / 1e6 * po


def estimate(outcomes: list[dict], profile: AgentProfile | None = None) -> dict:
    """把每条查询的路由结果汇总成效率指标。

    `outcomes`: [{"decision": "replay"|"agent", "expected": "replay"|"agent",
                  "correct": bool, "replay_failed": bool}, ...]
    """
    profile = profile or load_agent_profile()
    n = len(outcomes)
    if not n:
        return {"status": "ok", "queries": 0, "profile": profile.to_dict()}

    replay_n = sum(1 for o in outcomes if o["decision"] == "replay")
    agent_n = n - replay_n
    failed_n = sum(1 for o in outcomes if o.get("replay_failed"))
    # 错误重放 = 重放了但选错了路径：它「看起来省了 LLM 调用」，实际返回的是错的答案。
    # 计入成本时必须把它还原成 Agent 成本，否则会得出「越敢乱重放越省钱」的荒谬结论。
    misreplay_n = sum(1 for o in outcomes
                      if o["decision"] == "replay" and not o.get("correct", True))
    effective_agent_n = agent_n + misreplay_n

    total_calls = agent_n * profile.llm_calls
    total_in = agent_n * profile.input_tokens
    total_out = agent_n * profile.output_tokens
    # 延迟只算 LLM 侧：Agent 路径按遥测画像，重放路径为 0（沙箱耗时另计，未采集）
    total_latency = agent_n * profile.latency_ms
    cost = estimated_cost(total_in, total_out)

    replay = replay_profile()
    per_query_latency_with_sandbox = None  # 需要 Docker 实测，缺失不编造

    return {
        "status": "ok",
        "queries": n,
        "replay_queries": replay_n,
        "agent_queries": agent_n,
        "misreplay_queries": misreplay_n,
        "replay_share": replay_n / n,
        "agent_fallback_rate": agent_n / n,
        "replay_failure_rate": (failed_n / replay_n) if replay_n else 0.0,
        "llm_calls_per_query": total_calls / n,
        "llm_calls_per_query_incl_misreplay": effective_agent_n * profile.llm_calls / n,
        "avg_tokens_per_query": (total_in + total_out) / n,
        "avg_tokens_per_query_incl_misreplay":
            effective_agent_n * profile.total_tokens / n,
        "avg_input_tokens": total_in / n,
        "avg_output_tokens": total_out / n,
        "avg_latency_ms": total_latency / n,
        "avg_latency_ms_incl_misreplay": effective_agent_n * profile.latency_ms / n,
        "avg_latency_ms_per_agent_query": profile.latency_ms,
        "cost_usd_per_query": cost / n if cost is not None else None,
        "cost_usd_per_query_incl_misreplay":
            (estimated_cost(effective_agent_n * profile.input_tokens,
                            effective_agent_n * profile.output_tokens) / n)
            if cost is not None else None,
        "total_cost_usd": cost,
        "price_configured": cost is not None,
        "replay_llm_calls": replay["llm_calls"],
        "sandbox_ms": replay["sandbox_ms"],
        "latency_scope": "LLM 侧延迟（Agent 路径用遥测画像，重放路径为 0；"
                         "沙箱执行耗时未采集）",
        "profile": profile.to_dict(),
        "unmeasured": ([] if per_query_latency_with_sandbox is not None
                       else ["sandbox_ms"]),
    }
