"""规划上下文评估：注入给代码生成的 prompt 是否完整承载口径。

这是「指标定义以语义包为唯一来源」这条不变式的可执行版本——
只要渲染层漏掉某个指标的定义，这里就会红。
"""

from backend.evaluation import runner
from backend.semantic import render_semantic_prompt, render_spec_prompt
from backend.semantic.registry import load_pack


def test_planning_coverage_above_threshold(report):
    plan = report["planning"]
    assert plan["coverage"] >= 0.90, f"口径未注入 prompt：{plan['failures'][:3]}"


def test_rendering_fidelity_is_perfect(report):
    """渲染层不得丢字段 / 公式——这是与解析准确率无关的独立契约。"""
    plan = report["planning"]
    assert plan["resolved_entries"] > 0
    assert plan["fidelity"] == 1.0, f"渲染丢失定义：{plan['failures'][:3]}"


def test_derived_formula_always_injected(report):
    """派生指标（客单价 / 良率 / 达成率）的公式必须进入 prompt。"""
    rate = report["planning"]["formula_rate"]
    assert rate["total"] > 0, "数据集中应当包含派生指标样例"
    assert rate["rate"] == 1.0


def test_spec_block_carries_required_metrics_and_filters():
    spec = {"rewritten_question": "各品类销售额", "metrics": ["销售额"],
            "dimensions": ["品类"], "comparison": "none",
            "filters": [{"field": "区域", "op": "eq", "value": "华东"}]}
    block = render_spec_prompt(spec)
    assert "销售额" in block and "品类" in block
    assert "不得遗漏指标与筛选" in block


def test_optional_metric_skipped_when_column_absent():
    """optional 指标（如订单数）在数据里不存在时不应出现在语义块中。"""
    block = render_semantic_prompt("retail_sales")
    assert "订单数" not in block
    assert "销售额" in block


def test_pack_override_marks_optional_present():
    """显式传入加工过的包时，optional 指标可以被标记为「本次存在」。"""
    pack = load_pack("retail_sales") or {}
    pack = {**pack, "metrics": [{**m, "_present": True} for m in pack.get("metrics", [])]}
    block = render_semantic_prompt("retail_sales", pack)
    assert "订单数" in block


def test_planning_eval_is_not_circular():
    """评估必须由解析器输出驱动——否则「口径注入完整率」会恒等于 100%。"""
    cases = [{
        "id": "synthetic", "pack": "retail_sales", "dataset": "synthetic",
        "question": "今天天气怎么样",
        "expected": {"metrics": ["销售额"], "dimensions": [],
                     "comparison": "none", "analysis_type": "aggregate"},
    }]
    result = runner.eval_planning(cases)
    assert result["coverage"] == 0.0, "解析不到口径时，漏斗完整率必须为 0"
    assert result["resolved_entries"] == 0, "未解析到的口径不计入渲染保真度分母"
