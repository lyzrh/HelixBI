"""把行业语义包 / QuerySpec 渲染成 prompt 注入块。"""

from backend.semantic.registry import load_pack


def _aliases(entry: dict) -> str:
    """同义词去掉与指标/维度同名项后拼成别名串。"""
    names = [s for s in entry.get("synonyms", []) if s != entry.get("name")]
    return f"，别名：{'、'.join(names)}" if names else ""


def render_semantic_prompt(pack_id: str, pack: dict | None = None) -> str:
    """把行业包渲染成 prompt 注入块（指标/维度/口径/图表建议/示例问题）。

    `pack` 可显式传入一个已加工过的包（例如评估时把 optional 指标标记为「本次数据存在」），
    不传则按 `pack_id` 从 `semantic_packs/` 加载。
    """
    pack = pack if pack is not None else load_pack(pack_id)
    if not pack:
        return ""
    lines = [f"## 行业语义层（{pack.get('name', pack_id)}）—— 字段口径必须以此为准"]
    lines.append(f"- 时间字段：`{pack.get('time_field', '')}`（粒度 {pack.get('time_grain', 'day')}）")
    lines.append("- 指标：")
    for m in pack.get("metrics", []):
        if m.get("optional") and not m.get("_present"):
            continue
        src = f"字段 `{m['field']}`，聚合 {m['agg']}" if m.get("field") else "派生指标"
        unit = f"，单位 {m['unit']}" if m.get("unit") else ""
        lines.append(f"  - {m['name']}（{src}{unit}）"
                     + (f"，计算：{m['formula']}" if m.get("formula") else "")
                     + _aliases(m)
                     + (f"。{m['note']}" if m.get("note") else ""))
    lines.append("- 维度：")
    for d in pack.get("dimensions", []):
        if d.get("optional") and not d.get("_present"):
            continue
        lines.append(f"  - {d['name']}（字段 `{d['field']}`）" + _aliases(d))
    hints = pack.get("domain_hints", "").strip()
    if hints:
        lines.append(f"- 业务口径：{hints}")
    chart = pack.get("chart_hints", {})
    if chart:
        pairs = "；".join(f"{k}→{v}" for k, v in chart.items())
        lines.append(f"- 图表建议：{pairs}")
    examples = [q for q in pack.get("example_questions", []) if q][:3]
    if examples:
        lines.append("- 示例问题：" + " / ".join(examples))
    return "\n".join(lines)


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
