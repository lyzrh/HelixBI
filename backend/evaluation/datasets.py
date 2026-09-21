"""评估数据集：固定问题集 + 人工标注的期望口径。

数据集是评估的"基准真值"（ground truth），必须与 `semantic_packs/` 的 canonical name 对齐。
新增行业包时同步新增 `<pack>_questions.json`。
"""

import json
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
# 对抗准入样例单独放子目录：它们自带候选池与数据上下文，不参与语义解析准确率统计
# （避免用「更难的问题」把既有语义指标拉低，也避免为了过门禁去改既有数据集）
ADMISSION_DIR = DATASETS_DIR / "admission"


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


def admission_cases() -> list[dict]:
    """对抗准入样例：每条自带候选池（candidates）、数据上下文（context）与期望判定。

    这是「Skill 重放准入」的 ground truth——常规问题集只能度量「召回得对不对」，
    度量「该不该重放」必须有明确的负样本（相似但语义不同的 Skill）。
    """
    out: list[dict] = []
    for path in sorted(ADMISSION_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.get("_meta", {})
        for raw in data.get("cases", []):
            case = dict(raw)
            case.setdefault("pack", meta.get("pack"))
            case["dataset"] = path.stem
            out.append(case)
    return out


__all__ = ["ADMISSION_DIR", "DATASETS_DIR", "admission_cases", "all_cases",
           "dataset_paths", "load_all", "load_dataset", "packs"]
