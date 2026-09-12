"""语义包注册表：读取 / 检索 `semantic_packs/` 下的行业语义包配置。

每个行业包是一个 YAML：指标（含同义词与派生公式）、维度、时间字段、口径说明。
Agent 的意图理解与代码生成都以语义层为准绳，而不是裸猜字段名。

渲染为 prompt 注入块的部分见 `backend/semantic/render.py`。
"""

import json
from functools import lru_cache

import yaml

from backend.config import SEMANTIC_PACKS_DIR

REGISTRY_PATH = SEMANTIC_PACKS_DIR / "registry.json"

DEFAULT_PACK = "retail_sales"

# 规则式行业推断：列名命中关键词 → 对应行业包
INFER_RULES = {
    "manufacturing_production": ["产线", "良率", "不良", "停机", "工序", "计划产量", "实际产量"],
    "retail_sales": ["品类", "销售额", "门店", "客单价", "GMV", "销量"],
}


@lru_cache(maxsize=8)
def load_pack(pack_id: str) -> dict | None:
    path = SEMANTIC_PACKS_DIR / f"{pack_id}.yaml"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_packs() -> list[dict]:
    packs = []
    for path in sorted(SEMANTIC_PACKS_DIR.glob("*.yaml")):
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


__all__ = [
    "DEFAULT_PACK", "INFER_RULES", "REGISTRY_PATH", "SEMANTIC_PACKS_DIR",
    "assign_pack", "infer_pack", "list_packs", "load_pack", "pack_for_file",
    "pack_name",
]
