"""确定性缓存的统一登记入口：谁缓存了什么、这次运行省下了多少。

本模块**不实现**缓存本身（各自的持有者实现：画像在 `agent/profiler.py`、语义渲染在
`semantic/render.py`、确定性解析在 `semantic/resolver.py`、Skill 意图在
`skills/retrieval.py`），只做三件事：

1. `snapshot()` / `diff()`：给分析运行做"命中数增量"统计，落进
   `Run.trace.performance.cache`——这样"省下来的是不是真的"有数据，而不是感觉；
2. `clear_all()`：测试与配置变更后的统一失效入口；
3. `describe()`：把"哪些内容允许被缓存、为什么安全"写成可读说明，供文档与审计引用。

缓存安全约定（重要）：

- 只缓存**确定性、只读、与权限无关**的派生结果：文件画像（按指纹）、语义渲染（按
  pack + focus）、确定性解析（按问题 + pack）、Skill 意图（按 skill + 更新时间）；
- key 里包含**能区分数据归属的维度**：文件画像带 workspace_id、Skill 意图带 skill id
  与 updated_at；指纹变化（文件被替换、Skill 被改）即失效；
- **不缓存**任何与身份有关的判定结论（权限、工作区可见性、准入决策本身），
  这些每次请求都重新算——缓存"谁能不能看"是跨工作区污染的高危面。
"""

from __future__ import annotations

# 参与统计的缓存类别（顺序即报告顺序）
CATEGORIES = ("profile", "semantic_render", "resolver", "skill_intent")


def _profile() -> dict:
    from backend.agent import profiler

    return dict(profiler.cache_stats())


def _semantic_render() -> dict:
    from backend.semantic import render

    return dict(render.cache_stats())


def _resolver() -> dict:
    from backend.semantic import resolver

    info = resolver.cache_stats()
    return {"hits": info["hits"], "misses": info["misses"], "size": info["size"]}


def _skill_intent() -> dict:
    from backend.skills import retrieval

    return dict(retrieval.cache_stats())


_READERS = {
    "profile": _profile,
    "semantic_render": _semantic_render,
    "resolver": _resolver,
    "skill_intent": _skill_intent,
}


def snapshot() -> dict:
    """当前累计命中/未命中（用于 `diff` 求单次运行的增量）。"""
    out: dict[str, dict] = {}
    for name in CATEGORIES:
        try:
            out[name] = _READERS[name]()
        except Exception:  # noqa: BLE001 — 统计失败不得影响分析
            out[name] = {"hits": 0, "misses": 0}
    return out


def diff(before: dict | None) -> dict:
    """相对快照的增量统计 + 汇总（写进 trace，回答"缓存有没有起作用"）。"""
    after = snapshot()
    before = before or {}
    per_category: dict[str, dict] = {}
    hits = misses = 0
    for name in CATEGORIES:
        now, then = after.get(name, {}), before.get(name, {})
        hit = max(int(now.get("hits", 0)) - int(then.get("hits", 0)), 0)
        miss = max(int(now.get("misses", 0)) - int(then.get("misses", 0)), 0)
        hits += hit
        misses += miss
        per_category[name] = {"hits": hit, "misses": miss}
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / total, 4) if total else 0.0,
        "by_category": per_category,
    }


def clear_all() -> None:
    """清空全部确定性缓存（测试、配置变更、Skill 变更后调用）。"""
    from backend.agent import profiler
    from backend.semantic import render, resolver
    from backend.skills import retrieval

    profiler.clear_cache()
    render.clear_cache()
    resolver.clear_cache()
    retrieval.clear_caches()


def describe() -> list[dict]:
    """缓存登记表（key 里带了什么、什么时候失效）——文档与审计共用一份口径。"""
    return [
        {"name": "profile", "owner": "agent/profiler.py",
         "key": "workspace_id + 挂载名 + (path, size, mtime_ns)",
         "invalidated_by": "文件被替换 / 追加 / 版本变化",
         "caches": "数据集画像文本（列信息 / 缺失 / 样本行）"},
        {"name": "semantic_render", "owner": "semantic/render.py",
         "key": "pack_id + 命中的指标维度集合",
         "invalidated_by": "语义包文件变更（进程内缓存，重启即失效）",
         "caches": "语义层 prompt 渲染结果（与租户无关的公开配置）"},
        {"name": "resolver", "owner": "semantic/resolver.py",
         "key": "问题 + pack_id",
         "invalidated_by": "语义包变更 / 进程重启",
         "caches": "确定性解析结果（纯函数，不含任何权限判断）"},
        {"name": "skill_intent", "owner": "skills/retrieval.py",
         "key": "skill_id + updated_at + question + pack_id",
         "invalidated_by": "Skill 更新（updated_at 变化）",
         "caches": "Skill 侧意图解析（用于打分，不含可见性结论）"},
    ]


__all__ = ["CATEGORIES", "clear_all", "describe", "diff", "snapshot"]
