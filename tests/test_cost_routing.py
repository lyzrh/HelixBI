"""成本路由单元测试：确定性意图快路径 + 确定性追问推荐。

这一组守的是 Cost & Latency Optimization V1 的两条"省 LLM 调用"的路径：

- **快路径**：确定性解析合格才跳过 LLM，且"未解释内容"必须导致回落——
  这是正确性底线（把"筛选华东"猜成"按区域分组"比多花一次调用糟得多）；
- **确定性追问**：追问不再默认调 LLM，但凑不满时要能保底。
"""

import datetime

import pytest

from backend.agent import intent as intent_mod
from backend.agent import followups as followups_mod
from backend.evaluation.datasets import all_cases

TODAY = datetime.date(2026, 9, 22)


# ---- 1. 快路径的门规则 ----

def test_clear_question_is_resolved_without_llm():
    spec, meta = intent_mod.deterministic_intent(
        "各品类的总销售额是多少？", "retail_sales", today=TODAY)
    assert meta["eligible"] is True
    assert meta["source"] == "deterministic"
    assert spec["metrics"] == ["销售额"] and spec["dimensions"] == ["品类"]
    assert spec["source"] == "deterministic"


def test_value_filter_falls_back_to_llm():
    """「华东」是取值过滤，确定性解析只能识别「区域」这个维度识别不了取值 —— 必须回落。"""
    spec, meta = intent_mod.deterministic_intent(
        "华东地区的销售额是多少", "retail_sales", today=TODAY)
    assert meta["eligible"] is False
    assert meta["reason_code"] == intent_mod.REASON_UNCOVERED
    assert "华东" in meta["uncovered"]
    assert spec == {}


def test_unknown_metric_falls_back_to_llm():
    spec, meta = intent_mod.deterministic_intent(
        "天气怎么样", "retail_sales", today=TODAY)
    assert meta["eligible"] is False
    assert meta["reason_code"] == intent_mod.REASON_NO_METRIC


def test_low_confidence_falls_back_to_llm():
    """只有指标、没有任何结构化信号时保守回落（阈值可配置）。"""
    _, meta = intent_mod.deterministic_intent(
        "订单量有多少？", "retail_sales", min_confidence=0.70, today=TODAY)
    assert meta["eligible"] is False
    assert meta["reason_code"] == intent_mod.REASON_LOW_CONFIDENCE
    assert meta["confidence"] < meta["threshold"]


def test_llm_mode_freezes_the_fast_path():
    """`INTENT_MODE=llm` 是冻结基线：任何问题都走 LLM。"""
    spec, meta = intent_mod.deterministic_intent(
        "各品类的总销售额是多少？", "retail_sales", mode="llm", today=TODAY)
    assert meta["eligible"] is False
    assert meta["reason_code"] == intent_mod.REASON_MODE
    assert spec == {}


# ---- 2. 时间与排序的确定性映射 ----

def test_relative_time_is_mapped_to_absolute_window():
    spec, meta = intent_mod.deterministic_intent(
        "上周各产线的良率", "manufacturing_production", today=TODAY)
    assert meta["eligible"] is True
    assert spec["time_range"] == {"type": "custom", "start": "2026-09-14",
                                  "end": "2026-09-20"}
    assert spec["grain"] == "week"
    assert spec["time_phrase"] == "上周"


def test_named_month_is_mapped_to_bounds():
    spec, _ = intent_mod.deterministic_intent(
        "2024年3月各品类销量", "retail_sales", today=TODAY)
    assert spec["time_range"] == {"type": "custom", "start": "2024-03-01",
                                 "end": "2024-03-31"}
    assert spec["grain"] == "month"


def test_ranking_direction_and_topn_are_explicit():
    """排序方向必须显式进 spec：LLM 版本靠 rewritten_question 承载，确定性版本给结构化字段。"""
    spec, _ = intent_mod.deterministic_intent(
        "销售额最低的 5 家门店是哪些？", "retail_sales", today=TODAY)
    assert spec["topn"] == 5
    assert spec["ranking"] == {"top_n": 5, "order": "asc"}
    assert spec["analysis_type"] == "ranking"


def test_comparison_is_carried_into_spec():
    spec, meta = intent_mod.deterministic_intent(
        "同比看，各渠道销售额表现如何", "retail_sales", today=TODAY)
    assert meta["eligible"] is True
    assert spec["compare"] == "yoy"


# ---- 3. 安全门在真实问题集上的表现（回归门禁） ----

def test_fast_path_accuracy_on_labeled_dataset():
    """命中快路径的用例，其 QuerySpec 必须与人工标注完全一致。

    这是「没有为了省钱牺牲正确性」的可验证证据：宁可在命中率上保守，
    也不允许出现"省了一次调用但解析错了"的用例。
    """
    covered = 0
    strict = 0
    for case in all_cases():
        spec, meta = intent_mod.deterministic_intent(
            case["question"], case["pack"], today=TODAY)
        if not meta["eligible"]:
            continue
        covered += 1
        expected = case["expected"]
        ok = (set(spec["metrics"]) == set(expected["metrics"])
              and set(spec["dimensions"]) == set(expected["dimensions"])
              and (spec["compare"] or "none") == expected["comparison"]
              and spec["analysis_type"] == expected["analysis_type"])
        strict += int(ok)
        assert ok, f"{case['id']} 快路径解析与标注不一致：{spec}"
    assert covered >= 50, "快路径命中太少说明门过于保守（成本优化失去意义）"
    assert strict == covered
    # 覆盖率下限：低于这个数说明快路径形同虚设
    assert covered / len(all_cases()) >= 0.60


def test_uncovered_terms_reports_content_words():
    """未解释内容按"剔除停用词后的残渣"报告（写进 trace 时人能直接看懂）。"""
    from backend.semantic import resolve

    clear = "各品类的总销售额是多少？"
    assert intent_mod.uncovered_terms(
        clear, intent_mod.covered_phrases(resolve(clear, "retail_sales"))) == []

    filtered = "华东地区的销售额"
    uncovered = intent_mod.uncovered_terms(
        filtered, intent_mod.covered_phrases(resolve(filtered, "retail_sales")))
    # 「地区」是「区域」的别名所以被识别为维度，但「华东」这个**取值**解析器理解不了
    assert uncovered == ["华东"]


# ---- 4. 确定性追问 ----

def test_deterministic_followups_are_derived_from_spec():
    spec = {"metrics": ["销售额"], "dimensions": ["区域"], "grain": "month"}
    items = followups_mod.deterministic_followups("各区域销售额", spec, "retail_sales")
    assert len(items) == 3
    assert all(isinstance(q, str) and len(q) >= 4 for q in items)
    assert any("销售额" in q for q in items)
    # 不回问刚刚问过的问题
    assert all("各区域销售额" != q for q in items)


def test_deterministic_followups_avoid_repeat_and_need_metric():
    assert followups_mod.deterministic_followups("x", {}, None) == []


def test_hybrid_mode_only_falls_back_when_short():
    """hybrid：确定性能凑满就不再调 LLM（这是省下那次调用的机制保证）。"""
    spec = {"metrics": ["销售额"], "dimensions": ["区域", "品类"]}
    items, needs_llm = followups_mod.plan_followups(
        "各区域销售额", spec, "retail_sales", mode="hybrid")
    assert len(items) >= 3 and needs_llm is False

    _, needs_llm_none = followups_mod.plan_followups(
        "x", {}, None, mode="hybrid")
    assert needs_llm_none is True, "确定性给不出候选时必须保底回落 LLM"


def test_llm_and_off_modes():
    _, needs_llm = followups_mod.plan_followups(
        "各区域销售额", {"metrics": ["销售额"]}, "retail_sales", mode="llm")
    assert needs_llm is True
    items, needs = followups_mod.plan_followups(
        "各区域销售额", {"metrics": ["销售额"]}, "retail_sales", mode="off")
    assert items == [] and needs is False


@pytest.mark.parametrize("mode", ["deterministic", "hybrid"])
def test_deterministic_modes_never_need_llm_when_candidates_exist(mode):
    spec = {"metrics": ["良率"], "dimensions": ["产线"]}
    items, needs_llm = followups_mod.plan_followups(
        "各产线良率", spec, "manufacturing_production", mode=mode)
    assert items and needs_llm is False
