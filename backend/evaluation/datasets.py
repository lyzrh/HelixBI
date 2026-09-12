"""评估数据集：固定问题集 + 人工标注的期望口径。

数据集是评估的"基准真值"（ground truth），必须与 `semantic_packs/` 的 canonical name 对齐。
新增行业包时同步新增 `<pack>_questions.json`。
"""

import json
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


def dataset_paths() -> list[Path]:
    return sorted(DATASETS_DIR.glob("*.json"))


def load_dataset(path: str | Path) -> dict:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    meta = data.get("_meta", {})
    cases: list[dict] = []
    for raw in data.get("cases", []):
        case = dict(raw)
        case.setdefault("pack", meta.get("pack"))
        case["dataset"] = p.stem
        cases.append(case)
    return {"name": p.stem, "meta": meta, "cases": cases}


def load_all() -> list[dict]:
    return [load_dataset(p) for p in dataset_paths()]


def all_cases() -> list[dict]:
    """展平所有数据集的问题（每条带上来源与语义包）。"""
    out: list[dict] = []
    for ds in load_all():
        out.extend(ds["cases"])
    return out


def packs() -> list[str]:
    return sorted({c["pack"] for c in all_cases() if c.get("pack")})


__all__ = ["DATASETS_DIR", "dataset_paths", "load_dataset", "load_all", "all_cases", "packs"]
