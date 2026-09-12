"""端到端评估使用的示例数据映射（语义包 → 仓库内置样例文件）。"""

from pathlib import Path

from backend.config import PROJECT_ROOT

EXAMPLES_DIR = PROJECT_ROOT / "examples"

PACK_SAMPLES = {
    "retail_sales": "sample_sales.csv",
    "manufacturing_production": "sample_production.csv",
}


def sample_files_for_pack(pack_id: str) -> dict[str, str]:
    """→ {沙箱内挂载显示名: 宿主路径}；样例缺失时返回空 dict。"""
    name = PACK_SAMPLES.get(pack_id)
    if not name:
        return {}
    path = EXAMPLES_DIR / name
    return {name: str(path)} if path.exists() else {}


__all__ = ["EXAMPLES_DIR", "PACK_SAMPLES", "sample_files_for_pack"]
