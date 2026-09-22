"""Build a compact text profile of each uploaded dataset for prompt injection.

Bare prompts over raw data perform poorly (the core lesson from Vanna/WrenAI
research): the model needs schema, dtypes, missingness and sample rows up
front. This module keeps the profile small enough to inject every turn.

成本/延迟（Cost & Latency Optimization V1）：画像要**读整个文件**（大 CSV / parquet
可能是几十 MB），而同一个数据源会在同一轮甚至连续多轮分析里被反复画像。因此这里加了
一层**按文件指纹**（path + size + mtime）的缓存：

- 指纹变化（文件被替换 / 追加）⇒ 缓存自然失效，不会用旧画像；
- key 里带 workspace_id ⇒ 不同工作区不共享缓存对象（同一物理文件也不会被串用）；
- 命中/未命中计数会进 `Run.trace.performance.cache`，"profile 花了多久"可回溯。
"""

import threading
from collections import OrderedDict

import pandas as pd

MAX_SAMPLE_ROWS = 5
MAX_CATEGORICAL_VALUES = 8
MAX_SHEETS = 5

# 画像缓存：条数上限（每条最多几百 token，32 条足够覆盖活跃数据源）
PROFILE_CACHE_LIMIT = 32
_profile_cache: "OrderedDict[tuple, str]" = OrderedDict()
_profile_lock = threading.Lock()
_profile_stats = {"hits": 0, "misses": 0, "evictions": 0, "skipped_missing": 0}


def file_fingerprint(path: str) -> tuple:
    """文件指纹：路径 + 大小 + 修改时间（拿不到 stat 时退化为"每次都算"）。"""
    try:
        stat = __import__("os").stat(path)
        return (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        return (str(path), -1, -1)


def cache_stats() -> dict:
    with _profile_lock:
        snap = dict(_profile_stats)
    snap["size"] = len(_profile_cache)
    return snap


def clear_cache() -> None:
    with _profile_lock:
        _profile_cache.clear()
        for key in _profile_stats:
            _profile_stats[key] = 0


def profile_dataframe(df: pd.DataFrame, name: str) -> str:
    lines = [f"### 数据集 `{name}`", f"- 形状: {df.shape[0]} 行 x {df.shape[1]} 列"]
    dup = int(df.duplicated().sum())
    if dup:
        lines[-1] += f"，重复行 {dup}"

    missing = df.isna().sum()
    col_lines = []
    for col in df.columns:
        dtype = str(df[col].dtype)
        desc = f"  - `{col}` ({dtype})"
        n_unique = df[col].nunique(dropna=True)
        if missing[col] > 0:
            desc += f", 缺失 {missing[col]}"
        if pd.api.types.is_numeric_dtype(df[col]):
            desc += f", 范围 [{df[col].min()}, {df[col].max()}], 均值 {df[col].mean():.4g}"
        elif n_unique <= 30:
            values = df[col].dropna().unique()[:MAX_CATEGORICAL_VALUES]
            desc += f", 取值示例: {list(map(str, values))}"
        desc += f", 唯一值 {n_unique} 个"
        col_lines.append(desc)
    lines.append("- 列信息:")
    lines.extend(col_lines)

    sample = df.head(MAX_SAMPLE_ROWS).to_string(max_cols=15)
    lines.append(f"- 前 {MAX_SAMPLE_ROWS} 行样本:")
    lines.append("```")
    lines.append(sample)
    lines.append("```")
    return "\n".join(lines)


def profile_file(path: str | pd.DataFrame, name: str | None = None) -> str:
    if isinstance(path, pd.DataFrame):
        df = path
        label = name or "dataframe"
        return profile_dataframe(df, label)

    label = name or path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    suffix = str(path).lower()
    if suffix.endswith((".xlsx", ".xls")):
        book = pd.ExcelFile(path)
        # 最多画像 5 个 sheet，避免 prompt 膨胀
        parts = []
        for sheet in book.sheet_names[:MAX_SHEETS]:
            df = book.parse(sheet)
            parts.append(profile_dataframe(df, f"{label} · 工作表「{sheet}」"))
        if len(book.sheet_names) > MAX_SHEETS:
            parts.append(f"（另有 {len(book.sheet_names) - MAX_SHEETS} 个工作表未展示）")
        return "\n\n".join(parts)
    if suffix.endswith(".parquet"):
        df = pd.read_parquet(path)
        return profile_dataframe(df, label)
    df = pd.read_csv(path)
    return profile_dataframe(df, label)


def profile_all(files: dict[str, str], workspace_id: int | None = None,
                use_cache: bool = True) -> str:
    """files: display-name -> host path（沙箱内挂载名为 key）。

    结果按 (workspace, 文件指纹, 挂载名) 缓存：同一批数据重复分析时**不再重复读盘**，
    这是分析链路里最容易白花的确定性开销（大文件尤其明显）。
    """
    if not use_cache:
        return "\n\n".join(profile_file(p, name) for name, p in files.items())

    parts: list[str] = []
    for name, path in files.items():
        key = (workspace_id, name) + file_fingerprint(path)
        with _profile_lock:
            cached = _profile_cache.get(key)
            if cached is not None:
                _profile_cache.move_to_end(key)
                _profile_stats["hits"] += 1
        if cached is not None:
            parts.append(cached)
            continue
        with _profile_lock:
            _profile_stats["misses"] += 1
        text = profile_file(path, name)
        with _profile_lock:
            _profile_cache[key] = text
            _profile_cache.move_to_end(key)
            while len(_profile_cache) > PROFILE_CACHE_LIMIT:
                _profile_cache.popitem(last=False)
                _profile_stats["evictions"] += 1
        parts.append(text)
    return "\n\n".join(parts)
