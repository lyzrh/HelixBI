"""Build a compact text profile of each uploaded dataset for prompt injection.

Bare prompts over raw data perform poorly (the core lesson from Vanna/WrenAI
research): the model needs schema, dtypes, missingness and sample rows up
front. This module keeps the profile small enough to inject every turn.
"""

import pandas as pd

MAX_SAMPLE_ROWS = 5
MAX_CATEGORICAL_VALUES = 8
MAX_SHEETS = 5


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


def profile_all(files: dict[str, str]) -> str:
    """files: display-name -> sandbox path (under /data)."""
    return "\n\n".join(profile_file(p, name) for name, p in files.items())
