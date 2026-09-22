"""把行业语义包 / QuerySpec 渲染成 prompt 注入块。

两个成本相关的改动（都不改变"注入什么口径"的语义，只改变"注入多少"）：

1. **只注入被引用的口径**——QuerySpec 一旦确定（指标 / 维度），语义层里其余指标与
   维度就不再被这次分析引用，注入它们只是把 prompt 撑大。`focus` 非空时只渲染命中的
   条目 + 时间字段 + 业务口径（业务口径属于"必须知道"的背景，保留）；
   图表建议与示例问题属于软引导，收窄时一并去掉。
2. **缓存渲染结果**——渲染是纯函数（pack + focus → 文本），一次运行里会被重复调用
   （多数据源、trace、评估），按 (pack_id, focus) 缓存即可。

安全边界：`focus` 只影响"渲染多少"，不影响"能渲染什么"——pack 内容来自仓库内的配置
文件，与工作区 / 租户无关，因此这里不存在跨工作域的信息泄漏面。
"""

import functools

from backend.semantic.registry import load_pack


def _aliases(entry: dict) -> str:
    """同义词去掉与指标/维度同名项后拼成别名串。"""
    names = [s for s in entry.get("synonyms", []) if s != entry.get("name")]
    return f"，别名：{'、'.join(names)}" if names else ""


def _render_semantic_lines(pack: dict, pack_id: str, focus: set[str] | None) -> list[str]:
    lines = [f"## 行业语义层（{pack.get('name', pack_id)}）—— 字段口径必须以此为准"]
    lines.append(f"- 时间字段：`{pack.get('time_field', '')}`（粒度 {pack.get('time_grain', 'day')}）")

    focused = bool(focus)
    lines.append("- 指标：")
    rendered_metrics = 0
    for m in pack.get("metrics", []):
        if m.get("optional") and not m.get("_present"):
            continue
        if focused and m.get("name") not in focus:
            continue
        src = (f"字段 `{m['field']}`，聚合 {m['agg']}" if m.get("field") else "派生指标")
        unit = f"，单位 {m['unit']}" if m.get("unit") else ""
        lines.append(f"  - {m['name']}（{src}{unit}）"
                     + (f"，计算：{m['formula']}" if m.get("formula") else "")
                     + _aliases(m)
                     + (f"。{m['note']}" if m.get("note") else ""))
        rendered_metrics += 1
    if focused and not rendered_metrics:
        lines.append("  - （本次未命中指标，口径以问题与数据概况为准）")

    lines.append("- 维度：")
    rendered_dims = 0
    for d in pack.get("dimensions", []):
        if d.get("optional") and not d.get("_present"):
            continue
        if focused and d.get("name") not in focus:
            continue
        lines.append(f"  - {d['name']}（字段 `{d['field']}`）" + _aliases(d))
        rendered_dims += 1
    if focused and not rendered_dims:
        lines.append("  - （本次未命中维度）")

    hints = pack.get("domain_hints", "").strip()
    if hints:
        lines.append(f"- 业务口径：{hints}")
    # 图表建议与示例问题属于软引导：收窄时省略（本次分析的口径已由 QuerySpec 固定）
    if not focused:
        chart = pack.get("chart_hints", {})
        if chart:
            pairs = "；".join(f"{k}→{v}" for k, v in chart.items())
            lines.append(f"- 图表建议：{pairs}")
        examples = [q for q in pack.get("example_questions", []) if q][:3]
        if examples:
            lines.append("- 示例问题：" + " / ".join(examples))
    return lines


@functools.lru_cache(maxsize=64)
def _render_cached(pack_id: str, focus: tuple[str, ...]) -> str:
    pack = load_pack(pack_id)
    if not pack:
        return ""
    return "\n".join(_render_semantic_lines(pack, pack_id, set(focus) if focus else None))


def render_semantic_prompt(pack_id: str, pack: dict | None = None,
                           focus: list[str] | None = None) -> str:
    """把行业包渲染成 prompt 注入块（指标 / 维度 / 口径 / 图表建议 / 示例问题）。

    `pack` 可显式传入一个已加工过的包（例如评估时把 optional 指标标记为「本次数据存在」），
    不传则按 `pack_id` 从 `semantic_packs/` 加载。
    `focus` 非空时**只渲染命中的指标与维度**（成本优化；口径定义与业务口径照旧保留）。
    """
    if pack is not None:
        return "\n".join(_render_semantic_lines(pack, pack_id,
                                                set(focus) if focus else None))
    key = tuple(sorted({str(f) for f in (focus or []) if f}))
    return _render_cached(pack_id, key)


def cache_stats() -> dict:
    """渲染缓存命中情况（写进 Run.trace.performance.cache）。"""
    info = _render_cached.cache_info()
    return {"hits": info.hits, "misses": info.misses, "size": info.currsize}


def clear_cache() -> None:
    _render_cached.cache_clear()


def render_spec_prompt(spec: dict) -> str:
    """把意图确认后的 QuerySpec 渲染成代码生成约束块。"""
    if not spec:
        return ""
    import json as _json

    return (
        "## 已确认的分析意图（QuerySpec）—— 代码必须严格覆盖以下要求，"
        "不得遗漏指标与筛选\n```json\n"
        + _json.dumps(spec, ensure_ascii=False, indent=2)
        + "\n```"
    )
