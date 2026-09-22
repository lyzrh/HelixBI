"""确定性缓存的正确性测试：**缓存不能变成"串味的来源"**。

缓存最危险的两类错误：

1. **失效判断错**：文件变了却仍用旧画像（分析基于过期 schema，结论错得无声无息）；
2. **隔离做错**：把 A 工作区/数据源的结果给到 B（跨工作域污染，安全事件）。

这一组用例把这两条钉住，并验证命中统计是真的（`Run.trace.performance.cache` 的数据来源）。
"""

import time
from pathlib import Path

import pytest

from backend.agent import profiler
from backend.analysis import cache as cache_mod


@pytest.fixture(autouse=True)
def _clean_cache():
    cache_mod.clear_all()
    yield
    cache_mod.clear_all()


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ---- 1. 命中与失效 ----

def test_second_profile_hits_the_cache(tmp_path):
    csv = _write(tmp_path / "a.csv", "品类,销售额\n食品,10\n")
    files = {"a.csv": str(csv)}
    first = profiler.profile_all(files, workspace_id=1)
    misses = profiler.cache_stats()["misses"]
    second = profiler.profile_all(files, workspace_id=1)
    stats = profiler.cache_stats()
    assert first == second
    assert stats["hits"] >= 1
    assert stats["misses"] == misses, "第二次不应再读盘"


def test_file_change_invalidates_the_cache(tmp_path):
    csv = _write(tmp_path / "a.csv", "品类,销售额\n食品,10\n")
    files = {"a.csv": str(csv)}
    before = profiler.profile_all(files, workspace_id=1)
    # mtime 精度在某些文件系统上只有秒级：显式推进时间再写，避免"改了但指纹相同"
    time.sleep(0.01)
    _write(csv, "品类,销售额,门店\n食品,10,门店A\n")
    after = profiler.profile_all(files, workspace_id=1)
    assert before != after
    assert "门店" in after, "指纹变化后必须重新画像"


def test_workspace_is_part_of_the_key(tmp_path):
    """同一物理文件、不同工作区 → 不共享缓存条目（防跨工作域串用）。"""
    csv = _write(tmp_path / "a.csv", "品类,销售额\n食品,10\n")
    files = {"a.csv": str(csv)}
    profiler.profile_all(files, workspace_id=1)
    stats_after_first = profiler.cache_stats()
    profiler.profile_all(files, workspace_id=2)
    stats_after_second = profiler.cache_stats()
    assert stats_after_second["misses"] == stats_after_first["misses"] + 1


def test_cache_can_be_disabled(tmp_path):
    csv = _write(tmp_path / "a.csv", "品类,销售额\n食品,10\n")
    files = {"a.csv": str(csv)}
    profiler.profile_all(files, workspace_id=1, use_cache=False)
    profiler.profile_all(files, workspace_id=1, use_cache=False)
    assert profiler.cache_stats()["hits"] == 0
    assert profiler.cache_stats()["misses"] == 0


def test_missing_file_is_not_cached_forever(tmp_path):
    """文件不存在时返回空/异常都由上层处理，但缓存不得因此永久毒化该 key。"""
    ghost = tmp_path / "ghost.csv"
    files = {"ghost.csv": str(ghost)}
    with pytest.raises(Exception):
        profiler.profile_all(files, workspace_id=1)
    csv = _write(ghost, "品类,销售额\n食品,10\n")
    assert "食品" in profiler.profile_all({"ghost.csv": str(csv)}, workspace_id=1)


# ---- 2. 增量统计（trace 的数据来源） ----

def test_diff_reports_per_category_deltas(tmp_path):
    before = cache_mod.snapshot()
    csv = _write(tmp_path / "a.csv", "品类,销售额\n食品,10\n")
    profiler.profile_all({"a.csv": str(csv)}, workspace_id=1)
    from backend.semantic import resolve, render_semantic_prompt

    render_semantic_prompt("retail_sales", focus=["销售额"])
    render_semantic_prompt("retail_sales", focus=["销售额"])   # 第二次命中
    resolve("各品类的总销售额是多少？", "retail_sales")
    resolve("各品类的总销售额是多少？", "retail_sales")         # 第二次命中
    delta = cache_mod.diff(before)
    assert delta["hits"] >= 2
    assert delta["misses"] >= 1
    assert set(delta["by_category"]) == set(cache_mod.CATEGORIES)
    assert 0.0 <= delta["hit_rate"] <= 1.0


def test_diff_is_zero_without_activity():
    before = cache_mod.snapshot()
    delta = cache_mod.diff(before)
    assert delta["hits"] == 0 and delta["misses"] == 0
    assert delta["hit_rate"] == 0.0


def test_clear_all_resets_every_cache(tmp_path):
    csv = _write(tmp_path / "a.csv", "品类,销售额\n食品,10\n")
    profiler.profile_all({"a.csv": str(csv)}, workspace_id=1)
    from backend.semantic import render_semantic_prompt

    render_semantic_prompt("retail_sales", focus=["销售额"])
    cache_mod.clear_all()
    stats = cache_mod.snapshot()
    assert all(v.get("hits", 0) == 0 for v in stats.values())


# ---- 3. 登记表：缓存了什么、什么时候失效（文档与审计共用） ----

def test_describe_lists_every_category_with_key_and_invalidation():
    rows = cache_mod.describe()
    names = {r["name"] for r in rows}
    assert names == set(cache_mod.CATEGORIES)
    for row in rows:
        assert row["owner"] and row["key"] and row["invalidated_by"] and row["caches"]


def test_no_identity_decision_is_cached():
    """登记表只允许"数据 / 配置"类缓存；身份判定（准入结论、可见性、权限）不得进缓存。

    这里检查两件事：① 没有任何条目声称缓存了身份判定；② 模块里写明了这条约定
    （约定写进文档才算数，否则下一个人很容易顺手加一个"准入结果缓存"）。
    """
    for row in cache_mod.describe():
        assert not any(bad in row["caches"]
                       for bad in ("准入决策", "准入结论", "可见性判定", "权限判定",
                                   "who_can", "permission"))
    doc = cache_mod.__doc__ or ""
    assert "不缓存" in doc and "身份" in doc


def test_resolver_cache_returns_independent_copies():
    """缓存返回的必须是副本，调用方改写不得污染后续请求。"""
    from backend.semantic import resolve

    first = resolve("各品类的总销售额是多少？", "retail_sales")
    first["metrics"].append({"name": "被篡改"})
    first["confidence"] = -1
    second = resolve("各品类的总销售额是多少？", "retail_sales")
    assert all(m["name"] != "被篡改" for m in second["metrics"])
    assert second["confidence"] >= 0
