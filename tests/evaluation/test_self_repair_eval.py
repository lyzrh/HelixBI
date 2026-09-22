"""CI Regression Gate：Self-Repair V2 不得回退，且指标不得靠"改数据"变好看。

门禁取值原则（与 `test_retrieval_gate.py` 一致）：
- 阈值只能**收紧**，不能为了让改动过而放宽；
- V1/V2 比的是同一批场景、同一个图谱、同一套桩（唯一变量是重试策略）；
- 最重要的一条：**离线仿真必须自证"不是实测"**——`measured=false`、
  `mode="offline_simulation"`，真实的执行/自修复指标仍走 gated 阶段的「未采集」。
"""

import pytest

from backend.agent import repair as repair_mod
from backend.evaluation import repair_bench as bench
from backend.evaluation import runner

# ---- 门禁阈值（只收紧，不放宽） ----

GATE = {
    "v2_overall_success_rate": 0.70,
    "v2_repair_success_rate": 0.60,
    "v2_max_avg_repair_attempts": 1.50,
    "expectation_pass_rate": 1.0,
    "scenarios": 12,
    "replay_guard_cases": 2,
}

REQUIRED_METRICS = (
    "first_pass_success_rate", "repair_success_rate", "overall_success_rate",
    "avg_repair_attempts", "repeated_error_rate", "repair_exhaustion_rate",
    "fallback_rate", "llm_calls_per_query", "tokens_per_query", "repair_latency_ms",
)

SCENARIO_KINDS = {"first_pass", "syntax_error", "column_error", "type_error", "empty_result",
                  "timeout", "resource_limit", "repeated_error", "multi_error", "exhausted",
                  "acceptance_failed", "environment"}


@pytest.fixture(scope="session")
def repair_bench() -> dict:
    return bench.evaluate()


# ---- 1. 仿真诚实性：不能把仿真说成实测 ----

def test_offline_simulation_is_labeled(repair_bench):
    assert repair_bench["mode"] == "offline_simulation"
    assert repair_bench["measured"] is False
    notes = repair_bench["simulation_notes"]
    assert notes["measured"] is False
    assert set(notes["stubbed"]) == {"llm", "sandbox_executor"}
    assert notes["assumption"], "必须显式写出仿真假设"
    assert "langgraph" in " ".join(notes["real"]).lower()


def test_gated_self_repair_stage_still_declares_uncollected(report):
    """真实自修复指标仍按项目口径呈现：跑不了就「未采集 + 原因」，不填数字。"""
    stage = report["self_repair"]
    if stage["status"] == "skipped":
        assert stage["reason"], "跳过了却没说原因"
        assert "rate" not in stage
    assert report["self_repair_offline"]["status"] == "ok"


def test_existing_evaluation_cases_are_untouched(report):
    """不靠改数据集制造提升：固定问题集仍是 65 条，离线阶段仍全绿。"""
    assert report["dataset"]["cases"] == 65
    assert report["semantic"]["status"] == "ok"
    assert report["retrieval"]["v2"]["fine"]["false_replay_rate"] == 0.0


# ---- 2. 覆盖率：需求点名的情形都要有场景 ----

def test_scenarios_cover_required_kinds(repair_bench):
    kinds = set(repair_bench["dataset"]["kinds"])
    assert SCENARIO_KINDS <= kinds, f"缺少场景类型：{SCENARIO_KINDS - kinds}"
    assert repair_bench["dataset"]["scenarios"] >= GATE["scenarios"]


def test_every_required_error_category_has_a_strategy():
    for category in (repair_mod.CATEGORY_SYNTAX, repair_mod.CATEGORY_NAME,
                     repair_mod.CATEGORY_COLUMN, repair_mod.CATEGORY_TYPE,
                     repair_mod.CATEGORY_EMPTY, repair_mod.CATEGORY_TIMEOUT,
                     repair_mod.CATEGORY_RESOURCE, repair_mod.CATEGORY_CONTRACT,
                     repair_mod.CATEGORY_UNKNOWN):
        strategy = repair_mod.strategy_for(category)
        assert strategy.hint and strategy.strategy_id


# ---- 3. 指标完整性 ----

@pytest.mark.parametrize("policy", ["v1", "v2"])
def test_all_required_metrics_present(repair_bench, policy):
    entry = repair_bench[policy]
    for metric in REQUIRED_METRICS:
        assert metric in entry, f"{policy} 缺少指标 {metric}"
    latency = entry["repair_latency_ms"]
    assert latency["p50_ms"] <= latency["p95_ms"]
    assert entry["queries"] == repair_bench["dataset"]["scenarios"]


def test_cost_never_invented(repair_bench):
    entry = repair_bench["v2"]
    assert entry["cost_usd_per_query"] is None or entry["price_configured"]


# ---- 4. V2 必须优于 V1（唯一变量是重试策略） ----

def test_v2_overall_success_beats_baseline(repair_bench):
    v1, v2 = repair_bench["v1"], repair_bench["v2"]
    assert v2["overall_success_rate"] >= GATE["v2_overall_success_rate"], v2["outcomes"]
    assert v2["overall_success_rate"] > v1["overall_success_rate"]


def test_v2_repair_success_beats_baseline(repair_bench):
    v1, v2 = repair_bench["v1"], repair_bench["v2"]
    assert v2["repair_success_rate"] >= GATE["v2_repair_success_rate"], v2["outcomes"]
    assert v2["repair_success_rate"] > v1["repair_success_rate"]


def test_v2_uses_fewer_repairs_and_tokens(repair_bench):
    """定向修复 + 复读检测的直接收益：更少的重试、更少的 LLM 调用与 token。"""
    v1, v2 = repair_bench["v1"], repair_bench["v2"]
    assert v2["avg_repair_attempts"] < v1["avg_repair_attempts"]
    assert v2["avg_repair_attempts"] <= GATE["v2_max_avg_repair_attempts"]
    assert v2["llm_calls_per_query"] < v1["llm_calls_per_query"]
    assert v2["tokens_per_query"] < v1["tokens_per_query"]


def test_first_pass_success_is_equal_across_policies(repair_bench):
    """首次成功率只由"第一次就写对"决定，不该被重试策略改变（改了就说明有副作用）。"""
    assert repair_bench["v1"]["first_pass_success_rate"] == \
        repair_bench["v2"]["first_pass_success_rate"]


def test_repeat_stop_only_exists_in_v2(repair_bench):
    v1, v2 = repair_bench["v1"], repair_bench["v2"]
    assert v2["repeated_error_rate"] > 0, "V2 必须能识别复读"
    assert v1["repeated_error_rate"] == 0, "V1 没有复读检测"


def test_environment_failure_only_short_circuits_in_v2(repair_bench):
    """环境不可用时 V2 直接兜底（不重试），V1 会把额度烧光——这是"该停就停"的证据。"""
    v1, v2 = repair_bench["v1"], repair_bench["v2"]
    assert v2["fallback_rate"] > 0
    assert v1["fallback_rate"] == 0
    assert v2["repair_exhaustion_rate"] < v1["repair_exhaustion_rate"]


def test_max_fix_attempts_still_bounded(repair_bench):
    """上限没有被放大：任何场景的执行次数都不能超过 1 + MAX_FIX_ATTEMPTS。"""
    limit = 1 + repair_bench["dataset"]["max_fix_attempts"]
    for policy in ("v1", "v2"):
        for outcome in repair_bench[policy]["outcomes"]:
            assert outcome["executions"] <= limit, (policy, outcome)
            assert outcome["repair_attempts"] <= repair_bench["dataset"]["max_fix_attempts"]


def test_scenario_expectations_hold_for_both_policies(repair_bench):
    """每条场景的判定必须与设计期望一致——否则说明分类/策略/止停逻辑跑偏了。"""
    for policy in ("v1", "v2"):
        entry = repair_bench[policy]
        assert entry["expectation_pass_rate"] == GATE["expectation_pass_rate"], \
            entry["expectation_failures"]


def test_v2_picks_the_strategy_matching_the_error(repair_bench):
    """修复策略必须与错误类别对应（不是随便挑一个）。"""
    expected = {
        "sr02_syntax_fixed": "code_structure",
        "sr03_column_fixed": "schema_alignment",
        "sr04_type_fixed": "dtype_conversion",
        "sr05_empty_fixed": "filter_and_scope",
        "sr06_timeout_fixed": "bounded_compute",
        "sr07_resource_limit_fixed": "memory_bound",
    }
    got = {o["id"]: o["repair_strategy"] for o in repair_bench["v2"]["outcomes"]}
    for scenario_id, strategy in expected.items():
        assert got[scenario_id] == strategy, f"{scenario_id}: {got[scenario_id]}"


# ---- 5. 与 Skill Retrieval V2 的联动 ----

def test_skill_replay_never_enters_self_repair(repair_bench):
    guard = repair_bench["replay_guard"]
    assert guard["cases"] == GATE["replay_guard_cases"]
    assert guard["passed"] == guard["cases"], guard["results"]
    for item in guard["results"]:
        assert item["got"]["repair_attempts"] == 0
        assert item["got"]["llm_calls"] == 0, "重放路径必须零 LLM"


def test_high_confidence_replay_still_zero_llm(report):
    """Skill 高置信重放仍是 0 LLM 调用（两条独立证据：重放占比 + llm 画像）。"""
    entry = report["retrieval"]["v2"]["efficiency"]
    assert entry["replay_llm_calls"] == 0
    assert entry["replay_share"] >= 0.9


# ---- 6. 可复现 ----

def test_offline_repair_metrics_are_reproducible(repair_bench):
    second = runner.eval_self_repair_offline()
    for policy in ("v1", "v2"):
        for metric in REQUIRED_METRICS:
            first_value = repair_bench[policy][metric]
            if metric == "repair_latency_ms":
                assert first_value["p50_ms"] == second[policy][metric]["p50_ms"]
                continue
            assert first_value == second[policy][metric], f"{policy}/{metric} 不确定"
    assert repair_bench["replay_guard"]["passed"] == second["replay_guard"]["passed"]


def test_benchmark_markdown_contains_the_story(repair_bench):
    text = bench.render_markdown(repair_bench)
    for needle in ("Baseline(V1) vs V2", "First-pass Success Rate", "Repair Success Rate",
                   "Repeated Error Rate", "Repair Exhaustion Rate", "Fallback Rate",
                   "LLM Calls / Query", "Tokens / Query", "Repair Latency",
                   "离线策略仿真", "Skill 重放不进入 Self-Repair"):
        assert needle in text, f"benchmark 报告缺少：{needle}"
