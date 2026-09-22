"""LLM 运行预算的单元 + 图谱级集成测试。

预算要真的"拦得住"，必须满足三件事，这一组用例逐一验证：

1. **调用前预检**：超限的调用根本不发出（不是调用完再统计）；
2. **可解释**：`termination_reason` 落到 trace，能回答"为什么这次只跑了 2 次"；
3. **兜底不编造**：预算耗尽时结论由**真实执行结果**拼出，不伪造数字、不抛异常。
"""

import json
import tempfile
from pathlib import Path

import pytest

from backend.agent import budget as budget_mod
from backend.analysis import runtime
from backend.evaluation import repair_bench as bench
from backend.semantic import render_semantic_prompt


# ---- 1. 预算判定（纯函数） ----

def test_unlimited_budget_never_blocks():
    budget = budget_mod.RunBudget()
    assert budget.limited is False
    assert budget_mod.precheck(budget, budget_mod.BudgetState(), "generate_code", 99999).allowed


def test_call_limit_blocks_before_calling():
    budget = budget_mod.RunBudget(max_llm_calls=2)
    state = budget_mod.BudgetState(llm_calls=2)
    decision = budget_mod.precheck(budget, state, "summarize", 100)
    assert decision.allowed is False
    assert decision.reason_code == budget_mod.TERMINATION_LLM_CALLS


def test_input_token_limit_uses_projected_input():
    budget = budget_mod.RunBudget(max_input_tokens=1000)
    state = budget_mod.BudgetState(input_tokens=900)
    assert budget_mod.precheck(budget, state, "generate_code", 200).allowed is False
    assert budget_mod.precheck(budget, state, "generate_code", 50).allowed is True


def test_output_token_limit_is_checked_after_the_call():
    budget = budget_mod.RunBudget(max_output_tokens=10)
    state = budget_mod.BudgetState(output_tokens=11)
    assert budget_mod.precheck(budget, state, "summarize", 1).allowed is True
    assert budget_mod.postcheck(budget, state, "summarize").allowed is False


def test_cost_budget_requires_configured_price(monkeypatch):
    from backend import config

    state = budget_mod.BudgetState(input_tokens=1_000_000, output_tokens=1_000_000)
    budget = budget_mod.RunBudget(max_cost_usd=0.01)
    monkeypatch.setattr(config, "LLM_PRICE_INPUT_PER_MTOK", 0.0)
    monkeypatch.setattr(config, "LLM_PRICE_OUTPUT_PER_MTOK", 0.0)
    # 未配置单价 → 无法估算成本 → 不拦（诚实：不能拿编造的单价去卡预算）
    assert budget_mod.precheck(budget, state, "summarize", 0).allowed is True
    monkeypatch.setattr(config, "LLM_PRICE_INPUT_PER_MTOK", 10.0)
    assert budget_mod.precheck(budget, state, "summarize", 0).allowed is False


def test_repair_allowance_only_tightens():
    """预算只能**收紧**修复额度，绝不放大 MAX_FIX_ATTEMPTS。"""
    assert budget_mod.repair_allowance(budget_mod.RunBudget(), 3) == 3
    assert budget_mod.repair_allowance(budget_mod.RunBudget(max_repair_attempts=1), 3) == 1
    assert budget_mod.repair_allowance(budget_mod.RunBudget(max_repair_attempts=99), 3) == 3


def test_denied_call_is_recorded_with_reason():
    state = budget_mod.BudgetState()
    decision = budget_mod.BudgetDecision(False, budget_mod.TERMINATION_COST, "太贵")
    budget_mod.note_denied(state, "summarize", decision)
    payload = state.to_dict()
    assert payload["blocked_calls"] == 1
    assert payload["denied_node"] == "summarize"
    assert payload["termination_label"]


# ---- 2. 图谱级：预算真的拦下了调用 ----

def _run(question: str, budget: dict | None = None, packs=("retail_sales",)):
    from backend.agent import graph as graph_mod

    with bench._isolated_db():
        llm = bench.StubLLM()
        ctx = {"merged": {}}
        executor = bench.ScriptedExecutor({"failures": []}, ctx, Path(tempfile.mkdtemp()))
        original_llm, original_run = graph_mod.get_llm, graph_mod.run_in_sandbox
        graph_mod.get_llm = lambda: llm
        graph_mod.run_in_sandbox = executor
        merged: dict = {}
        try:
            for _node, _delta, state in graph_mod.stream_analysis(
                question, bench._sample_files(),
                semantic_block=render_semantic_prompt("retail_sales"),
                semantic_packs=list(packs), budget_overrides=budget, run_id=8801,
            ):
                merged = state
                ctx["merged"] = state
        finally:
            graph_mod.get_llm, graph_mod.run_in_sandbox = original_llm, original_run
        return merged, llm, runtime.build_cost_control_trace(merged, runtime._llm_stats(8801))


def test_no_budget_keeps_normal_path():
    merged, llm, cost = _run("各品类的总销售额是多少？")
    assert merged["termination_reason"] == ""
    assert cost["budget_blocked_calls"] == 0
    assert cost["llm_calls"] == sum(llm.calls_by_node.values())
    assert cost["intent_source"] == "deterministic"


def test_call_budget_stops_and_explains():
    merged, llm, cost = _run("各品类的总销售额是多少？", budget={"max_llm_calls": 1})
    assert llm.calls_by_node == {"generate_code": 1}, "超出预算的调用不得发出"
    assert cost["termination_reason"] == budget_mod.TERMINATION_LLM_CALLS
    assert cost["budget_blocked_calls"] >= 1
    assert cost["blocked_node"] == "summarize"
    assert cost["budget_used"]["llm_calls"] == 1
    assert cost["budget_utilization"]["llm_calls"] == 1.0


def test_budget_exhaustion_still_answers_without_inventing_numbers():
    """预算耗尽时必须有结论，且结论只陈述真实执行结果。"""
    merged, _llm, cost = _run("各品类的总销售额是多少？", budget={"max_llm_calls": 1})
    answer = merged.get("answer") or ""
    assert answer, "预算耗尽也必须给出结论"
    assert merged.get("summarize_source") == "deterministic"
    assert "预算" in answer
    assert "代码执行：成功" in answer, "结论必须来自真实执行结果"


def test_input_token_budget_blocks_the_generate_call():
    merged, llm, cost = _run("各品类的总销售额是多少？",
                             budget={"max_input_tokens": 10})
    assert llm.calls_by_node == {}
    assert merged.get("generate_blocked") is True
    assert cost["termination_reason"] == budget_mod.TERMINATION_INPUT_TOKENS
    assert merged.get("answer")


def test_generate_blocked_skips_execution():
    """没有新代码可跑时不得进沙箱（不浪费一次执行）。"""
    from backend.agent import graph as graph_mod

    merged, _llm, _cost = _run("各品类的总销售额是多少？", budget={"max_input_tokens": 10})
    assert graph_mod.route_after_generate(merged) == "summarize"
    assert merged.get("attempts", 0) == 0, "生成被拦下时不应产生执行次数"


def test_cost_control_is_json_serializable_and_exposes_sources():
    _merged, _llm, cost = _run("各品类的销售额最高的门店", budget={"max_llm_calls": 5})
    payload = json.loads(json.dumps(cost, ensure_ascii=False))
    assert payload["token_counter"]
    assert payload["usage_source"] in ("provider", "estimated")
    assert "budget_limit" in payload and "budget_used" in payload
    # 回答"为什么调了 N 次"必须能落到节点
    assert isinstance(payload["calls_by_node"], dict)


@pytest.mark.parametrize("key", ["llm_calls", "input_tokens", "output_tokens",
                                 "total_tokens", "cost_usd", "blocked_calls"])
def test_budget_state_has_all_counters(key):
    assert key in budget_mod.BudgetState().to_dict()
