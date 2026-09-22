"""Runtime Benchmark 门禁（Production Runtime V1）。

与 Self-Repair / Cost 基准同一套约定：离线仿真自证 `measured=False`，
断言的是**真实池与并发调度代码**的行为（复用、无泄漏、排队收益、容量内不拒绝），
而非建模的睡眠时间。
"""

import pytest

from backend.evaluation import runtime_bench


@pytest.fixture(scope="module")
def result():
    return runtime_bench.evaluate()


def test_benchmark_is_offline_simulation(result):
    assert result["measured"] is False
    assert result["mode"] == "offline_simulation"
    assert result["docker_warmup_succeeded"] is True


def test_pool_returns_to_full_idle_after_load(result):
    """任何路径都不能泄漏容器：负载结束后池必须回到满员空闲。"""
    assert result["pool_leak_check"] is True
    pf = result["pool_final"]
    assert pf["idle"] == pf["warm_count"]


def test_warm_beats_cold_at_every_concurrency(result):
    for c in result["comparisons"]:
        assert c["warm_p50_ms"] < c["cold_p50_ms"], f"conc={c['concurrency']}"
        assert c["throughput_gain"] > 1.5, f"conc={c['concurrency']}"


def test_reuse_actually_happens_under_load(result):
    pf = result["pool_final"]
    assert pf["reuse_count"] > 0, "负载下容器必须被复用（否则池没有意义）"


def test_no_rejection_within_capacity_and_no_crashes(result):
    pf = result["pool_final"]
    assert pf["rejected_waiters"] == 0
    assert pf["crash_replacements"] == 0


def test_scenarios_cover_required_concurrency_levels(result):
    levels = {s["concurrency"] for s in result["scenarios"]}
    assert {1, 5, 10, 20} <= levels
    modes = {s["mode"] for s in result["scenarios"]}
    assert modes == {"cold", "warm"}


def test_markdown_declares_honesty(result):
    md = runtime_bench.render_markdown(result)
    assert "measured=false" in md
    assert "建模值" in md and "未采集" in md
