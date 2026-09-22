"""CI Regression Gate：成本 / 延迟优化不得靠"改数据"或"牺牲正确性"变好看。

门禁取值原则（与 `test_retrieval_gate.py` / `test_self_repair_eval.py` 一致）：

- 阈值只**收紧**，不放宽；比的是同一份代码 + 两组冻结开关（唯一变量是策略开关）；
- 最重要的一条：**离线基准必须自证"不是实测"**，真实 provider 计费与真实沙箱耗时
  仍走 gated 阶段的「未采集」；
- 省下来的调用必须**可归因**（快路径 / 确定性追问），并伴随"命中处与人工标注 100% 一致"
  的正确性证据——否则就是拿准确率换钱。
"""

import pytest

from backend.evaluation import cost_bench as bench

# ---- 门禁阈值（只收紧，不放宽） ----

GATE = {
    "min_intent_coverage": 0.60,       # 快路径覆盖率下限（低于此优化失去意义）
    "min_token_reduction": 0.20,       # Agent 路径 token 降幅下限
    "min_call_reduction": 0.20,        # Agent 路径 LLM 调用降幅下限
    "min_cases": 65,
}

REQUIRED_METRICS = (
    "llm_calls_per_query", "llm_calls_per_agent_query", "input_tokens_per_query",
    "output_tokens_per_query", "total_tokens_per_query", "cost_usd_per_query",
    "deterministic_latency_ms", "projected_latency_ms", "replay_rate",
    "fallback_rate", "intent_fastpath_rate", "followup_zero_llm_rate",
    "budget_exhausted_rate", "context_tokens_p50", "context_tokens_p95",
)

BUDGET_TERMINATION_REASONS = {"llm_call_limit", "input_token_limit",
                              "output_token_limit", "total_token_limit", "cost_limit"}


@pytest.fixture(scope="session")
def cost_bench() -> dict:
    return bench.evaluate()


# ---- 1. 诚实性：不能把离线基准说成实测 ----

def test_benchmark_is_labeled_as_simulation(cost_bench):
    assert cost_bench["mode"] == "offline_simulation"
    assert cost_bench["measured"] is False
    honesty = cost_bench["honesty"]
    assert set(honesty) == {"measured", "modeled", "not_measured"}
    assert any("沙箱" in item for item in honesty["modeled"])
    assert any("Docker" in item or "沙箱" in item for item in honesty["not_measured"])


def test_token_counter_is_declared(cost_bench):
    """用了哪套分词器必须写在结果里，否则 token 数字无法复核。"""
    assert cost_bench["token_counter"]
    assert "tiktoken" in cost_bench["token_counter"] or "heuristic" in cost_bench["token_counter"]


def test_dataset_is_the_real_fixed_question_set(cost_bench):
    assert cost_bench["dataset"]["cases"] >= GATE["min_cases"]
    assert set(cost_bench["dataset"]["packs"]) == {"retail_sales",
                                                   "manufacturing_production"}


# ---- 2. 优化确实更省 ----

def test_optimized_uses_fewer_calls_and_tokens(cost_bench):
    b, o = cost_bench["baseline"], cost_bench["optimized"]
    assert o["llm_calls_per_agent_query"] < b["llm_calls_per_agent_query"]
    assert o["total_tokens_per_agent_query"] < b["total_tokens_per_agent_query"]
    call_drop = 1 - (o["llm_calls_per_agent_query"] / b["llm_calls_per_agent_query"])
    token_drop = 1 - (o["total_tokens_per_agent_query"] / b["total_tokens_per_agent_query"])
    assert call_drop >= GATE["min_call_reduction"], f"调用降幅不足：{call_drop:.2%}"
    assert token_drop >= GATE["min_token_reduction"], f"Token 降幅不足：{token_drop:.2%}"


def test_generation_context_shrinks(cost_bench):
    b, o = cost_bench["baseline"], cost_bench["optimized"]
    assert o["context_tokens_p50"] < b["context_tokens_p50"]
    assert o["context_tokens_p95"] < b["context_tokens_p95"]


def test_deterministic_latency_does_not_regress(cost_bench):
    b, o = cost_bench["baseline"], cost_bench["optimized"]
    # 允许 5% 抖动（本机噪声），但不允许变慢
    assert (o["deterministic_latency_ms"]["p50_ms"]
            <= b["deterministic_latency_ms"]["p50_ms"] * 1.05)


def test_savings_are_attributable(cost_bench):
    """省下的调用必须有出处：快路径 + 确定性追问。"""
    o = cost_bench["optimized"]
    assert o["intent_fastpath_rate"] > 0
    assert o["followup_zero_llm_rate"] == 1.0
    assert o["intent_sources"].get("llm", 0) + o["intent_sources"].get("deterministic", 0) == \
        cost_bench["dataset"]["cases"]


# ---- 3. 正确性不回归（关键：不能拿准确率换钱） ----

def test_fast_path_matches_labels_exactly(cost_bench):
    gate = cost_bench["intent_gate"]
    shipped = next(g for g in gate["by_threshold"]
                   if abs(g["threshold"] - gate["shipped_threshold"]) < 1e-9)
    assert shipped["strict_accuracy"] == 1.0, "快路径命中处必须与人工标注 100% 一致"
    assert shipped["coverage"] >= GATE["min_intent_coverage"]
    assert shipped["mismatches"] == []


def test_all_thresholds_are_safe(cost_bench):
    """任何门槛取值下，命中处都不允许出现与标注不一致的用例。"""
    for item in cost_bench["intent_gate"]["by_threshold"]:
        assert item["mismatches"] == [], \
            f"门槛 {item['threshold']} 下出现解析偏差：{item['mismatches'][:2]}"
        assert item["strict_accuracy"] == 1.0


def test_routing_is_unchanged_by_the_optimization(cost_bench):
    """成本优化不得改动重放/兜底路由（重放率、False Replay 由检索层决定，未被触碰）。"""
    b, o = cost_bench["baseline"], cost_bench["optimized"]
    assert o["replay_rate"] == b["replay_rate"]
    assert o["fallback_rate"] == b["fallback_rate"]
    routing = cost_bench["routing"]
    assert routing["false_replay_rate"] == 0.0
    assert routing["replay_precision"] == 1.0
    assert routing["admission_accuracy"] >= 0.90


def test_replay_path_still_zero_llm(cost_bench):
    guard = cost_bench["replay_guard"]
    assert guard["passed"] == guard["cases"]
    for check in guard["checks"]:
        assert check["ok"], check["name"]


# ---- 4. 预算：可解释、可兜底 ----

def test_budget_stress_stops_with_reason_and_honest_answer(cost_bench):
    stress = cost_bench["budget_stress"]
    assert stress["cases"] > 0
    assert stress["exhausted_rate"] > 0, "上限压到 2 时必须真的触发终止"
    assert stress["honest_answer_rate"] == 1.0, "预算耗尽也必须给出基于真实执行的结论"
    assert set(stress["termination_reasons"]) <= BUDGET_TERMINATION_REASONS


def test_default_budget_does_not_truncate_normal_runs(cost_bench):
    """默认预算只拦病态循环，不该把正常分析掐掉。"""
    assert cost_bench["optimized"]["budget_exhausted_rate"] == 0.0


# ---- 5. 报告与指标完整性 ----

@pytest.mark.parametrize("metric", REQUIRED_METRICS)
def test_required_metrics_are_present(cost_bench, metric):
    for policy in ("baseline", "optimized"):
        assert metric in cost_bench[policy], metric


def test_markdown_report_covers_the_headline_metrics(cost_bench):
    markdown = bench.render_markdown(cost_bench)
    for needle in ("LLM 调用 / Agent 查询", "总 Token / Agent 查询",
                   "确定性阶段 p50", "意图快路径", "重放零 LLM", "诚实性声明",
                   "offline_simulation"):
        assert needle in markdown, needle


def test_switches_are_explicit_and_frozen(cost_bench):
    switches = cost_bench["switches"]
    assert switches["baseline"] == bench.LEGACY_SWITCHES
    assert switches["optimized"] == bench.OPTIMIZED_SWITCHES
    assert switches["baseline"]["INTENT_MODE"] == "llm"
    assert switches["optimized"]["INTENT_MODE"] == "auto"


def test_modeled_assumptions_are_declared(cost_bench):
    modeled = cost_bench["modeled_assumptions"]
    for key in ("sandbox_ms", "llm_base_ms", "llm_ms_per_1k_input",
                "llm_ms_per_1k_output"):
        assert key in modeled and modeled[key] > 0
