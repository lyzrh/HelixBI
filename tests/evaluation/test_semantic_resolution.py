"""语义解析层评估：确定性解析器 vs 人工标注口径。

阈值即门禁：标准表述必须保持 100%（这是回归保护），
对抗样例只设下限（它本来就是用来暴露缺口的，允许存在已知失败）。
"""

from backend.evaluation import runner
from backend.semantic import resolve


def test_standard_dataset_fully_resolved(report):
    """标准表述：人工标注的口径必须全部命中（回归门禁）。"""
    by_ds = report["semantic"]["by_dataset"]
    for name in ("retail_questions", "manufacturing_questions"):
        assert by_ds[name]["accuracy"] == 1.0, (
            f"{name} 出现语义解析回归：{by_ds[name]}")


def test_hard_cases_above_floor(report):
    """对抗样例（口语化 / 未登记同义词）允许失败，但不能继续恶化。"""
    assert report["semantic"]["by_dataset"]["hard_cases"]["accuracy"] >= 0.5


def test_overall_accuracy_above_threshold(report):
    assert report["semantic"]["accuracy"] >= 0.85
    assert report["semantic"]["relaxed_accuracy"] >= 0.90


def test_strict_is_not_weaker_than_relaxed(report):
    sem = report["semantic"]
    assert sem["accuracy_hits"] <= sem["relaxed_hits"]


def test_component_breakdown_present(report):
    sem = report["semantic"]
    for key in ("metric_exact", "dimension_exact", "comparison", "analysis_type"):
        assert sem[key]["total"] == sem["cases"]


def test_resolve_is_deterministic():
    q = "今年华东地区销售额同比下降了多少？"
    first = resolve(q, "retail_sales")
    assert first == resolve(q, "retail_sales")


def test_resolve_matches_longest_term_first():
    """「销售额」不应被同义词「金额」抢先命中而残留「销售」。"""
    got = resolve("各品类的销售额是多少", "retail_sales")
    names = [m["name"] for m in got["metrics"]]
    assert names == ["销售额"]
    assert got["metrics"][0]["matched"] == "销售额"


def test_resolve_synonyms_and_time():
    got = resolve("近90天各区域的GMV周趋势", "retail_sales")
    assert [m["name"] for m in got["metrics"]] == ["销售额"]
    assert [d["name"] for d in got["dimensions"]] == ["区域"]
    assert got["time"]["value"] == 90 and got["time"]["unit"] == "day"
    assert got["analysis_type"] == "trend"


def test_resolve_ranking_direction():
    assert resolve("销售额最高的门店", "retail_sales")["ranking"]["order"] == "desc"
    assert resolve("销售额最低的门店", "retail_sales")["ranking"]["order"] == "asc"


def test_resolve_derived_metric_keeps_formula():
    got = resolve("各产线的良率对比", "manufacturing_production")
    metric = got["metrics"][0]
    assert metric["name"] == "良率"
    assert metric["formula"], "派生指标必须带上语义包里的公式，供 prompt 注入"


def test_resolve_empty_for_unrelated_question():
    got = resolve("今天天气怎么样", "retail_sales")
    assert got["metrics"] == [] and got["dimensions"] == []
    assert got["analysis_type"] == "unknown"
    # 只有时间词「今天」命中：置信度应当很低但不为 0——时间线索本身是有效信号
    assert got["confidence"] < 0.2


def test_resolve_unknown_pack_returns_empty():
    got = resolve("销售额", "not_a_pack")
    assert got["metrics"] == [] and got["confidence"] == 0.0


def test_resolver_is_fast(every_case):
    """确定性解析必须足够便宜，才能每轮都跑（对比 LLM 的秒级+计费）。"""
    assert report_latency(every_case)["p95_ms"] < 5.0


def report_latency(cases):
    return runner.eval_semantic(cases)["latency_ms"]
