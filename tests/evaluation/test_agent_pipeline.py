"""端到端与执行层评估：需要 Docker 沙箱 + LLM。

本机/CI 没有沙箱时**显式跳过**（而不是假装通过）——
"当前环境跑不了" 和 "跑通了" 是两件必须区分的事。
"""

import pytest

from backend.evaluation import runner

_BLOCKER = runner.pipeline_blocker()

pytestmark = pytest.mark.skipif(
    _BLOCKER is not None,
    reason=f"需要 Docker 沙箱 + LLM：{_BLOCKER}",
)


@pytest.fixture(scope="module")
def pipeline_report() -> dict:
    return runner.run_all(with_pipeline=True, limit=3)


def test_execution_success_rate(pipeline_report):
    stage = pipeline_report["execution"]
    assert stage["status"] == "ok", stage.get("reason")
    assert stage["cases"] > 0
    assert stage["rate"] >= 0.6, f"执行成功率过低：{stage}"


def test_pipeline_measures_latency_and_calls(pipeline_report):
    stage = pipeline_report["execution"]
    assert stage["latency_ms"]["p50_ms"] > 0
    assert stage["llm_calls"]["count"] > 0


def test_self_repair_is_observable(pipeline_report):
    """自修复阶段必须报告"发生过几次重试"，而不是只给一个成功率。"""
    stage = pipeline_report["self_repair"]
    assert stage["status"] == "ok"
    assert "cases" in stage and "rate" in stage


def test_blocker_reason_is_actionable():
    """跳过原因必须能指导操作（提到 Docker 或 API Key）。"""
    reason = runner.pipeline_blocker()
    if reason:
        assert ("Docker" in reason) or ("API Key" in reason)
