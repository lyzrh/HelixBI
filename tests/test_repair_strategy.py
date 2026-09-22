"""修复策略选择与有界重试决策：Self-Repair V2 的"怎么修 / 何时停"。

这一组用例保护三件事：

1. **提示词不是同一段**：每个错误类别必须给出不同的定向处方（否则 V2 就退化回 V1）；
2. **停下来是对的**：复读、额度用尽、环境不可用都必须停止重试，而不是继续烧 token；
3. **上限没有被放大**：全局仍受 `MAX_FIX_ATTEMPTS` 约束（每类额度只会更早收手）。
"""

import pytest

from backend import config
from backend.agent.repair import (
    CATEGORY_COLUMN,
    CATEGORY_CONTRACT,
    CATEGORY_EMPTY,
    CATEGORY_ENVIRONMENT,
    CATEGORY_NAME,
    CATEGORY_RESOURCE,
    CATEGORY_SYNTAX,
    CATEGORY_TIMEOUT,
    CATEGORY_TYPE,
    CATEGORY_UNKNOWN,
    LEGACY_UNIFORM_HINT,
    STATUS_EXHAUSTED,
    STATUS_NOT_NEEDED,
    STATUS_NOT_REPAIRABLE,
    STATUS_REPEATED,
    STATUS_REPAIRING,
    STATUS_SUCCEEDED,
    RepairPolicy,
    decide_repair,
    error_signature,
    strategy_for,
    summarize_errors,
)
from backend.agent.repair import ErrorInfo
from backend.evaluation import repair_cases as cases

FIXABLE = (CATEGORY_SYNTAX, CATEGORY_NAME, CATEGORY_COLUMN, CATEGORY_TYPE,
           CATEGORY_EMPTY, CATEGORY_TIMEOUT, CATEGORY_RESOURCE, CATEGORY_CONTRACT,
           CATEGORY_UNKNOWN)


def _info(category: str, message: str = "boom", accepted: bool = False) -> ErrorInfo:
    return ErrorInfo(
        category=category, signature=error_signature(category, message),
        message=message, accepted=accepted,
        retryable=strategy_for(category).retryable,
    )


def _record(info: ErrorInfo, strategy: str = "") -> dict:
    return {"category": info.category, "label": info.category,
            "signature": info.signature, "message": info.message, "strategy": strategy}


# ---- 定向处方：每类错误不同 ----

def test_each_category_has_its_own_strategy():
    ids = {strategy_for(c).strategy_id for c in FIXABLE}
    assert len(ids) == len(FIXABLE), "不同错误类别必须有不同的修复策略"


def test_hints_are_targeted_not_uniform():
    hints = {strategy_for(c).hint for c in FIXABLE}
    assert len(hints) == len(FIXABLE), "修复提示不能是同一段话"
    for hint in hints:
        assert hint != LEGACY_UNIFORM_HINT
        assert len(hint) > 40


@pytest.mark.parametrize("category,keyword", [
    (CATEGORY_COLUMN, "列名"),
    (CATEGORY_TYPE, "dtype"),
    (CATEGORY_EMPTY, "过滤条件"),
    (CATEGORY_SYNTAX, "代码结构"),
    (CATEGORY_TIMEOUT, "超时"),
    (CATEGORY_RESOURCE, "内存"),
    (CATEGORY_CONTRACT, "dahelper"),
])
def test_hint_mentions_the_actual_cause(category, keyword):
    """提示必须指向该错误的病因，而不是泛泛的"请分析错误原因"。"""
    assert keyword in strategy_for(category).hint


def test_column_hint_asks_to_check_real_schema_and_semantics():
    hint = strategy_for(CATEGORY_COLUMN).hint
    assert "数据概况" in hint and "语义层" in hint and "列名" in hint


def test_timeout_and_resource_are_bounded_to_one_attempt():
    """超时 / OOM 再修一次通常还是超时：额度收紧到 1 次，把预算留给别的情形。"""
    assert strategy_for(CATEGORY_TIMEOUT).max_attempts == 1
    assert strategy_for(CATEGORY_RESOURCE).max_attempts == 1


def test_environment_is_not_retryable():
    strategy = strategy_for(CATEGORY_ENVIRONMENT)
    assert strategy.retryable is False
    assert strategy.max_attempts == 0


def test_unknown_category_falls_back_to_general_diagnosis():
    assert strategy_for("nonexistent_category").strategy_id == \
        strategy_for(CATEGORY_UNKNOWN).strategy_id


# ---- 决策：何时修 ----

def test_accepted_means_no_repair():
    decision = decide_repair(_info(CATEGORY_COLUMN, accepted=True), [], RepairPolicy(), 1)
    assert decision.action == "stop"
    assert decision.status == STATUS_NOT_NEEDED


def test_first_failure_triggers_targeted_repair():
    decision = decide_repair(_info(CATEGORY_COLUMN), [], RepairPolicy(), 1)
    assert decision.action == "repair"
    assert decision.status == STATUS_REPAIRING
    assert decision.strategy.strategy_id == "schema_alignment"
    assert decision.attempt == 1
    assert decision.reason_code == "targeted_repair"


def test_identical_error_stops_immediately():
    """相同指纹 → 复读，直接放弃（这就是"不要无限重新生成"）。"""
    first = _info(CATEGORY_COLUMN, "KeyError: '销售额'")
    same = _info(CATEGORY_COLUMN, "KeyError: '销售额'")
    decision = decide_repair(same, [_record(first, "schema_alignment")], RepairPolicy(), 3)
    assert decision.action == "stop"
    assert decision.status == STATUS_REPEATED
    assert decision.repeat_kind == "identical"
    assert decision.exhausted is True


def test_equivalent_error_stops_early():
    """同类但正文不同的错误（换个列名继续报 KeyError）也是无进展 → 提前终止。"""
    first = _info(CATEGORY_COLUMN, "KeyError: '销售额'")
    other = _info(CATEGORY_COLUMN, "KeyError: '利润'")
    assert first.signature != other.signature
    decision = decide_repair(other, [_record(first)], RepairPolicy(), 3)
    assert decision.status == STATUS_REPEATED
    assert decision.repeat_kind == "equivalent"


def test_different_categories_keep_repairing():
    first = _info(CATEGORY_SYNTAX, "SyntaxError: eof")
    second = _info(CATEGORY_COLUMN, "KeyError: 'x'")
    decision = decide_repair(second, [_record(first, "code_structure")], RepairPolicy(), 2)
    assert decision.action == "repair"
    assert decision.strategy.strategy_id == "schema_alignment"


def test_global_cap_is_respected():
    """执行次数超过 MAX_FIX_ATTEMPTS 后必须停止——上限与旧行为一致。"""
    decision = decide_repair(_info(CATEGORY_COLUMN), [], RepairPolicy(max_fix_attempts=3), 4)
    assert decision.status == STATUS_EXHAUSTED
    assert decision.reason_code == "max_attempts"
    assert "MAX_FIX_ATTEMPTS=3" in decision.reason


def test_cap_comes_from_config_by_default():
    assert RepairPolicy().max_fix_attempts == config.MAX_FIX_ATTEMPTS


def test_per_category_cap_retires_the_same_category():
    """同一类别反复出现（中间隔着别的错误，因此不算连续复读）也会用完该类额度。"""
    errors = [_record(_info(CATEGORY_EMPTY, "empty-1")),
              _record(_info(CATEGORY_EMPTY, "empty-2")),
              _record(_info(CATEGORY_TYPE, "ValueError: x"))]
    decision = decide_repair(_info(CATEGORY_EMPTY, "empty-3"), errors, RepairPolicy(), 3)
    assert decision.status == STATUS_EXHAUSTED
    assert decision.reason_code == "category_cap"


def test_environment_stops_without_consuming_budget():
    decision = decide_repair(_info(CATEGORY_ENVIRONMENT), [], RepairPolicy(), 1)
    assert decision.action == "stop"
    assert decision.status == STATUS_NOT_REPAIRABLE
    assert decision.reason_code == "not_retryable"


# ---- 决策：V1 基线是冻结的 ----

def test_legacy_policy_retries_uniformly_without_repeat_detection():
    same = _info(CATEGORY_COLUMN, "KeyError: 'x'")
    decision = decide_repair(same, [_record(same)], RepairPolicy(name="v1"), 2)
    assert decision.action == "repair", "V1 不做复读检测：同一错误也会继续重试"
    assert decision.strategy.strategy_id == "uniform_legacy"
    assert decision.reason_code == "legacy_uniform_retry"


def test_legacy_policy_stops_at_the_same_cap():
    decision = decide_repair(_info(CATEGORY_COLUMN), [], RepairPolicy(name="v1"), 4)
    assert decision.status == STATUS_EXHAUSTED
    assert decision.reason_code == "legacy_max_attempts"


def test_legacy_policy_ignores_not_repairable_category():
    """V1 连"重试也没用"的环境错误都会重试——这正是 V2 要修的浪费。"""
    decision = decide_repair(_info(CATEGORY_ENVIRONMENT), [], RepairPolicy(name="v1"), 1)
    assert decision.action == "repair"


# ---- 汇总 ----

def test_summarize_errors_counts_categories_and_repeats():
    same_a = _info(CATEGORY_COLUMN, "KeyError: 'x'")
    same_b = _info(CATEGORY_COLUMN, "KeyError: 'x'")
    other = _info(CATEGORY_SYNTAX, "SyntaxError: eof")
    summary = summarize_errors([_record(same_a), _record(same_b), _record(other, "code_structure")])
    assert summary["count"] == 3
    assert summary["by_category"][CATEGORY_COLUMN] == 2
    assert summary["repeat_count"] == 1
    assert len(summary["signatures"]) == 3


def test_summarize_errors_handles_empty():
    assert summarize_errors([])["count"] == 0
    assert summarize_errors(None)["by_category"] == {}


def test_decision_is_json_serializable():
    import json

    from backend.agent.repair import decide_repair as decide

    payload = json.loads(json.dumps(
        decide(_info(CATEGORY_TYPE), [], RepairPolicy(), 1).to_dict(), ensure_ascii=False))
    assert payload["strategy"]["category"] == CATEGORY_TYPE


def test_scenario_templates_match_classifier():
    """场景脚本里的 stderr 必须真的被分类成它声明的类别（否则仿真就是假的）。"""
    import json as _json

    from backend.agent.acceptance import acceptance_gate
    from backend.agent.repair import classify_error

    for scenario in cases.scenarios():
        for failure in scenario["failures"]:
            payload = failure["result"]
            got = classify_error(payload, acceptance_gate(payload)).category
            assert got == failure["category"], (
                f"{scenario['id']} 声明 {failure['category']}，实际分类为 {got}")
            assert _json.dumps(payload, ensure_ascii=False)  # 场景数据可序列化


def test_every_scenario_strategy_is_reachable():
    """V2 场景声明的 fixed_by 策略必须是真实存在的策略 id（不能写错名字让仿真失真）。"""
    from backend.agent.repair import CATEGORIES, STRATEGIES

    valid = {s.strategy_id for s in STRATEGIES.values()}
    for scenario in cases.scenarios():
        for failure in scenario["failures"]:
            for strategy in failure["fixed_by"]:
                assert strategy == "*" or strategy in valid, (
                    f"{scenario['id']} 使用了不存在的策略 {strategy}")
            assert failure["category"] in CATEGORIES


def test_status_constants_are_distinct():
    assert len({STATUS_NOT_NEEDED, STATUS_REPAIRING, STATUS_SUCCEEDED, STATUS_EXHAUSTED,
                STATUS_REPEATED, STATUS_NOT_REPAIRABLE}) == 6
