"""LLM 运行预算：把"这次分析最多花多少"变成一条硬约束，而不是事后统计。

设计要点：

1. **预检在调用之前**——`calls / input_tokens / total_tokens / cost 下界` 都能在拼完
   prompt 后立刻算出，因此超预算时**不发起**这次调用（不产生费用，也不浪费时间）；
2. **终止原因可解释**——`termination_reason` 落到 `Run.trace.cost_control`，回答
   "为什么这次只跑了 2 次 LLM 就结束"；
3. **兜底不伪造**——预算耗尽时节点返回的是**结构化失败/降级**（无 LLM 也能给出
   基于真实执行结果的说明），绝不返回编造的数字；
4. **零默认限额**——`None` 表示"不限制"（默认全部不限制），保证引入预算前行为不变；
   只有显式配置了才生效，避免"为了省钱悄悄改变分析质量"。

预算与 Self-Repair 的关系：`max_repair_attempts` 是**修复维度**的额度，它只在
`config.MAX_FIX_ATTEMPTS` 之内进一步收紧（不会放大上限）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 终止原因（写进 trace，前端可视化与排障都靠它）
TERMINATION_NONE = ""                     # 正常结束
TERMINATION_LLM_CALLS = "llm_call_limit"      # LLM 调用次数用完
TERMINATION_INPUT_TOKENS = "input_token_limit"
TERMINATION_OUTPUT_TOKENS = "output_token_limit"
TERMINATION_TOTAL_TOKENS = "total_token_limit"
TERMINATION_COST = "cost_limit"
TERMINATION_ENV = "environment_unavailable"   # 环境不可用（沙箱不存在）

TERMINATION_LABELS = {
    TERMINATION_LLM_CALLS: "LLM 调用次数已达预算上限",
    TERMINATION_INPUT_TOKENS: "输入 token 已达预算上限",
    TERMINATION_OUTPUT_TOKENS: "输出 token 已达预算上限",
    TERMINATION_TOTAL_TOKENS: "总 token 已达预算上限",
    TERMINATION_COST: "估算成本已达预算上限",
    TERMINATION_ENV: "沙箱环境不可用，未继续调用 LLM",
}


@dataclass(frozen=True)
class RunBudget:
    """一次运行的预算上限；None = 不限制。"""

    max_llm_calls: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    max_repair_attempts: int | None = None

    @property
    def limited(self) -> bool:
        return any(v is not None for v in (
            self.max_llm_calls, self.max_input_tokens, self.max_output_tokens,
            self.max_total_tokens, self.max_cost_usd, self.max_repair_attempts))

    def to_dict(self) -> dict:
        return {
            "max_llm_calls": self.max_llm_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_total_tokens": self.max_total_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_repair_attempts": self.max_repair_attempts,
            "limited": self.limited,
        }


def budget_from_config(overrides: dict | None = None) -> RunBudget:
    """从配置构造预算；`overrides` 供单次运行显式指定（评估与单测用）。"""
    from backend import config

    values = {
        "max_llm_calls": getattr(config, "MAX_LLM_CALLS_PER_RUN", None),
        "max_input_tokens": getattr(config, "MAX_INPUT_TOKENS_PER_RUN", None),
        "max_output_tokens": getattr(config, "MAX_OUTPUT_TOKENS_PER_RUN", None),
        "max_total_tokens": getattr(config, "MAX_TOTAL_TOKENS_PER_RUN", None),
        "max_cost_usd": getattr(config, "MAX_RUN_COST_USD", None),
        "max_repair_attempts": getattr(config, "MAX_REPAIR_ATTEMPTS", None),
    }
    for key, value in (overrides or {}).items():
        if key in values:
            values[key] = value
    return RunBudget(**values)


@dataclass
class BudgetState:
    """已用量 + 终止状态（以普通 dict 存进 AgentState，便于序列化与回溯）。"""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    blocked_calls: int = 0
    termination_reason: str = ""
    denied_node: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "blocked_calls": self.blocked_calls,
            "termination_reason": self.termination_reason,
            "termination_label": TERMINATION_LABELS.get(self.termination_reason, ""),
            "denied_node": self.denied_node,
            "notes": list(self.notes),
        }


def state_from_dict(raw: dict | None) -> BudgetState:
    raw = raw or {}
    return BudgetState(
        llm_calls=int(raw.get("llm_calls") or 0),
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cost_usd=float(raw.get("cost_usd") or 0.0),
        blocked_calls=int(raw.get("blocked_calls") or 0),
        termination_reason=str(raw.get("termination_reason") or ""),
        denied_node=str(raw.get("denied_node") or ""),
        notes=list(raw.get("notes") or []),
    )


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason_code: str = TERMINATION_NONE
    reason: str = ""

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reason_code": self.reason_code,
                "reason": self.reason}


def _cost_of(budget: RunBudget, input_tokens: int, output_tokens: int) -> float | None:
    from backend.evaluation.efficiency import estimated_cost

    return estimated_cost(input_tokens, output_tokens)


def precheck(budget: RunBudget, state: BudgetState, node: str,
             estimated_input_tokens: int) -> BudgetDecision:
    """发起调用前的检查：能提前算出来的约束先拦掉。

    说明：`output / cost` 只能给"下界"判断（输出长度未知），所以这里按输入侧下界
    估算成本——宁可提前一点收手，也不要在调用之后才发现超支。
    """
    if budget is None:
        return BudgetDecision(True)
    if budget.max_llm_calls is not None and state.llm_calls + 1 > budget.max_llm_calls:
        return BudgetDecision(False, TERMINATION_LLM_CALLS,
                              f"{node}：本次运行已调用 {state.llm_calls} 次 LLM，"
                              f"达到上限 {budget.max_llm_calls}")
    if (budget.max_input_tokens is not None
            and state.input_tokens + estimated_input_tokens > budget.max_input_tokens):
        return BudgetDecision(False, TERMINATION_INPUT_TOKENS,
                              f"{node}：输入 token 预计 {state.input_tokens + estimated_input_tokens}"
                              f"，超过上限 {budget.max_input_tokens}")
    if (budget.max_total_tokens is not None
            and state.total_tokens + estimated_input_tokens > budget.max_total_tokens):
        return BudgetDecision(False, TERMINATION_TOTAL_TOKENS,
                              f"{node}：总 token 预计超过上限 {budget.max_total_tokens}")
    if budget.max_cost_usd is not None:
        projected = _cost_of(budget, state.input_tokens + estimated_input_tokens,
                             state.output_tokens)
        if projected is not None and projected > budget.max_cost_usd:
            return BudgetDecision(False, TERMINATION_COST,
                                  f"{node}：估算成本 {projected:.6f} 超过上限 "
                                  f"{budget.max_cost_usd}")
    return BudgetDecision(True)


def postcheck(budget: RunBudget, state: BudgetState, node: str) -> BudgetDecision:
    """调用完成后复查（输出 token / 成本只有这时才知道），为**下一次**调用定性。"""
    if budget is None:
        return BudgetDecision(True)
    if (budget.max_output_tokens is not None
            and state.output_tokens > budget.max_output_tokens):
        return BudgetDecision(False, TERMINATION_OUTPUT_TOKENS,
                              f"{node}：输出 token {state.output_tokens} 超过上限 "
                              f"{budget.max_output_tokens}")
    if (budget.max_total_tokens is not None
            and state.total_tokens > budget.max_total_tokens):
        return BudgetDecision(False, TERMINATION_TOTAL_TOKENS,
                              f"{node}：总 token {state.total_tokens} 超过上限 "
                              f"{budget.max_total_tokens}")
    if budget.max_cost_usd is not None:
        cost = _cost_of(budget, state.input_tokens, state.output_tokens)
        if cost is not None and cost > budget.max_cost_usd:
            return BudgetDecision(False, TERMINATION_COST,
                                  f"{node}：估算成本 {cost:.6f} 超过上限 "
                                  f"{budget.max_cost_usd}")
    return BudgetDecision(True)


def note_denied(state: BudgetState, node: str, decision: BudgetDecision) -> None:
    """记录一次被拦下的调用（可解释性：谁说不行、因为哪条约束）。"""
    state.blocked_calls += 1
    state.denied_node = node
    state.termination_reason = decision.reason_code
    state.notes.append(f"{node}: {decision.reason}")


def repair_allowance(budget: RunBudget, max_fix_attempts: int) -> int:
    """修复额度：预算只允许**收紧**，不允许超过 `MAX_FIX_ATTEMPTS`。"""
    if budget is None or budget.max_repair_attempts is None:
        return max_fix_attempts
    return max(0, min(int(budget.max_repair_attempts), int(max_fix_attempts)))


__all__ = [
    "BudgetDecision", "BudgetState", "RunBudget", "TERMINATION_COST",
    "TERMINATION_ENV", "TERMINATION_INPUT_TOKENS", "TERMINATION_LABELS",
    "TERMINATION_LLM_CALLS", "TERMINATION_NONE", "TERMINATION_OUTPUT_TOKENS",
    "TERMINATION_TOTAL_TOKENS", "budget_from_config", "note_denied", "postcheck",
    "precheck", "repair_allowance", "state_from_dict",
]
