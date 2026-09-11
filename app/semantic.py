"""行业语义层：指标/维度/业务口径的行业包（FineBI NEXT 语义层、WrenAI MDL 的轻量实现）。

每个行业包是一个 YAML：指标（含同义词与派生公式）、维度、时间字段、口径说明。
Agent 的意图理解与代码生成都以语义层为准绳，而不是裸猜字段名。
"""

import json
from functools import lru_cache
from pathlib import Path

import yaml

SEMANTICS_DIR = Path(__file__).resolve().parent.parent / "semantics"
REGISTRY_PATH = SEMANTICS_DIR / "registry.json"

DEFAULT_PACK = "retail_sales"

# 规则式行业推断：列名命中关键词 → 对应行业包
INFER_RULES = {
    "manufacturing_production": ["产线", "良率", "不良", "停机", "工序", "计划产量", "实际产量"],
    "retail_sales": ["品类", "销售额", "门店", "客单价", "GMV", "销量"],
}


@lru_cache(maxsize=8)
def load_pack(pack_id: str) -> dict | None:
    path = SEMANTICS_DIR / f"{pack_id}.yaml"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_packs() -> list[dict]:
    packs = []
    for path in sorted(SEMANTICS_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        packs.append({"id": data["pack"], "name": data.get("name", data["pack"])})
    return packs


def pack_name(pack_id: str) -> str:
    p = load_pack(pack_id)
    return p["name"] if p else pack_id


def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def assign_pack(filename: str, pack_id: str) -> None:
    registry = _load_registry()
    registry[filename] = pack_id
    REGISTRY_PATH.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def infer_pack(columns: list[str]) -> str | None:
    joined = "|".join(map(str, columns))
    for pack_id, keywords in INFER_RULES.items():
        if any(k in joined for k in keywords):
            return pack_id
    return None


def pack_for_file(filename: str, columns: list[str] | None = None) -> str:
    """文件 → 行业包：注册表优先，其次规则推断，最后默认包。"""
    registry = _load_registry()
    if filename in registry and load_pack(registry[filename]):
        return registry[filename]
    if columns:
        inferred = infer_pack(columns)
        if inferred:
            return inferred
    return DEFAULT_PACK


def _aliases(entry: dict) -> str:
    """同义词去掉与指标/维度同名项后拼成别名串。"""
    names = [s for s in entry.get("synonyms", []) if s != entry.get("name")]
    return f"，别名：{'、'.join(names)}" if names else ""


def render_semantic_prompt(pack_id: str) -> str:
    """把行业包渲染成 prompt 注入块（指标/维度/口径/图表建议/示例问题）。"""
    pack = load_pack(pack_id)
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
