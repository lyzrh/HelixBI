"""自助分析引擎：字段 schema 探测 + pandas 聚合/SQL 查询。

与对话式分析（LLM 驱动）互补的零 token 路径：前端把结构化查询
（维度/指标/聚合/筛选/排序）发来，本地 pandas 直接算，毫秒级返回。
本模块只做计算，不做 HTTP；参数错误一律抛 ValueError，由路由层转 400。
"""

import math
import re
import sqlite3

import numpy as np
import pandas as pd

from backend.models import DataSource
from backend.schemas import ExploreBody
from backend.datasource.service import _read_file

AGG_FUNCS = {"sum", "avg", "count", "count_distinct", "max", "min"}
FILTER_OPS = {"eq", "ne", "gt", "lt", "ge", "le", "contains", "in"}

# API 聚合名 -> pandas 函数名
PANDAS_FN = {"sum": "sum", "avg": "mean", "max": "max", "min": "min", "count": "count", "count_distinct": "nunique"}


def load_df(ds: DataSource) -> pd.DataFrame:
    if ds.type == "file":
        if not ds.file_path:
            raise ValueError("文件型数据源缺少文件路径")
        return _read_file(ds.file_path)
    # db 源：读物化缓存
    if ds.materialized_path:
        return pd.read_parquet(ds.materialized_path)
    raise ValueError("数据库数据源请先在数据源页执行「物化」再自助分析")


def dtype_of(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return "number"
    # 尝试日期解析（csv 读进来是 object）
    sample = series.dropna().head(20)
    if len(sample):
        try:
            parsed = pd.to_datetime(sample, errors="coerce")
            if parsed.notna().mean() > 0.8:
                return "date"
        except Exception:
            pass
    return "text"


def py(v):
    """numpy/pandas 标量 → JSON 安全的 Python 原生类型。"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    if hasattr(v, "isoformat"):
        return str(v)[:19]
    return v


def clean_rows(df: pd.DataFrame) -> list[dict]:
    cleaned = df.copy()
    for col in cleaned.columns:
        if pd.api.types.is_datetime64_any_dtype(cleaned[col]):
            cleaned[col] = cleaned[col].astype(str)
    cleaned = cleaned.where(pd.notna(cleaned), None)
    records = []
    for row in cleaned.to_dict("records"):
        for k, v in row.items():
            if isinstance(v, float) and math.isnan(v):
                row[k] = None
            elif isinstance(v, pd.Timestamp):
                row[k] = str(v)
            elif hasattr(v, "item"):  # numpy 标量
                row[k] = v.item()
        records.append(row)
    return records


# ---- schema 探测 ----

def schema_fields(ds: DataSource) -> dict:
    df = load_df(ds)
    fields = []
    for col in df.columns:
        dtype = dtype_of(df[col])
        sample = df[col].dropna().iloc[0] if df[col].notna().any() else None
        fields.append({
            "name": str(col),
            "dtype": dtype,
            "sample": str(sample) if sample is not None else "",
        })
    return {"fields": fields, "rows": len(df)}


# ---- 字段画像 ----

def profile_fields(ds: DataSource) -> dict:
    df = load_df(ds)
    if len(df) > 200_000:  # 画像用抽样即可，保证接口秒级返回
        df = df.sample(200_000, random_state=42)

    fields = []
    for col in df.columns:
        s = df[col]
        dtype = dtype_of(s)
        item: dict = {
            "name": str(col),
            "dtype": dtype,
            "count": int(len(s)),
            "missing_rate": round(float(s.isna().mean()) * 100, 1),
            "distinct": int(s.nunique(dropna=True)),
        }
        if dtype == "number":
            num = pd.to_numeric(s, errors="coerce").dropna()
            if len(num):
                desc = num.describe(percentiles=[0.25, 0.5, 0.75])
                hist = None
                if num.nunique() > 3:
                    # np.histogram 直方图；极端重复值导致箱边界不唯一时优雅降级
                    try:
                        counts, edges = np.histogram(num, bins=10)
                        hist = {
                            "bins": [f"{edges[i]:.4g}~{edges[i + 1]:.4g}"
                                     for i in range(len(counts))],
                            "counts": [int(c) for c in counts],
                        }
                    except (ValueError, TypeError):
                        hist = None
                item.update({
                    "min": py(desc["min"]), "max": py(desc["max"]),
                    "mean": py(round(float(desc["mean"]), 4)),
                    "median": py(round(float(desc["50%"]), 4)),
                    "std": py(round(float(desc["std"]), 4)) if desc["std"] == desc["std"] else None,
                    "p25": py(round(float(desc["25%"]), 4)),
                    "p75": py(round(float(desc["75%"]), 4)),
                    "hist": hist,
                    "zeros": int((num == 0).sum()),
                    "negatives": int((num < 0).sum()),
                })
        elif dtype == "date":
            dt = pd.to_datetime(s, errors="coerce").dropna()
            if len(dt):
                item.update({
                    "min": str(dt.min())[:10], "max": str(dt.max())[:10],
                    "span_days": int((dt.max() - dt.min()).days),
                })
        else:
            vc = s.value_counts(dropna=True).head(6)
            item["top_values"] = [{"value": str(k)[:40], "count": int(v)} for k, v in vc.items()]
        fields.append(item)

    return {
        "rows": len(df),
        "duplicate_rate": round(float(df.duplicated().mean()) * 100, 2),
        "fields": fields,
    }


# ---- 聚合查询 ----

def run_query(ds: DataSource, body: ExploreBody) -> dict:
    df = load_df(ds)
    cols = set(df.columns)

    # 校验字段
    for f in [*body.dimensions, *(m.field for m in body.metrics if m.agg != "count"),
              *(c.field for c in body.filters)]:
        if f not in cols:
            raise ValueError(f"字段不存在: {f}")
    for m in body.metrics:
        if m.agg not in AGG_FUNCS:
            raise ValueError(f"不支持的聚合方式: {m.agg}")

    # 筛选
    for c in body.filters:
        if c.op not in FILTER_OPS:
            raise ValueError(f"不支持的筛选操作: {c.op}")
        col = df[c.field]
        try:
            if c.op == "contains":
                df = df[col.astype(str).str.contains(str(c.value), na=False)]
                continue
            if c.op == "in":
                vals = c.value if isinstance(c.value, list) else [c.value]
                df = df[col.isin(vals)]
                continue
            if dtype_of(col) == "number":
                v = float(c.value)
            else:
                v = c.value
            ops = {"eq": col == v, "ne": col != v, "gt": col > v,
                   "lt": col < v, "ge": col >= v, "le": col <= v}
            df = df[ops[c.op]]
        except Exception as exc:
            raise ValueError(f"筛选条件错误（{c.field} {c.op} {c.value}）: {exc}")
    if df.empty:
        return {"rows": [], "total": 0}

    # 维度预处理：日期按粒度截断；数值维度保留
    dims = []
    for d in body.dimensions:
        if dtype_of(df[d]) == "date":
            grain = body.date_grain or "day"
            s = pd.to_datetime(df[d], errors="coerce")
            df[d] = (s.dt.to_period("M").astype(str) if grain == "month"
                     else s.dt.strftime("%Y-%m-%d"))
        dims.append(d)

    # 聚合
    out_cols: dict[str, str] = {}  # df 列名 -> 展示名
    specs = []
    for i, m in enumerate(body.metrics):
        label = f"{m.field}({m.agg})" if m.agg != "sum" else m.field
        # 同名字段冲突时加序号
        if label in body.dimensions or label in out_cols.values():
            label = f"{label}#{i}"
        if m.agg == "count":
            specs.append((m.field, "count", label))
        else:
            specs.append((m.field, PANDAS_FN[m.agg], label))
        out_cols[label] = label

    try:
        if dims:
            grouped = df.groupby(dims, dropna=False)
            agg_df = grouped.agg({
                **{f: fn for f, fn, _ in specs if fn != "count"},
            }) if any(fn != "count" for _, fn, _ in specs) else None
            count_df = grouped.size().to_frame("__count__") if any(
                fn == "count" for _, fn, _ in specs) else None
            if agg_df is None:
                result = count_df.copy()
            elif count_df is None:
                result = agg_df.copy()
            else:
                result = agg_df.join(count_df)
            result = result.reset_index()
            # count 列重命名
            ren: dict[str, str] = {}
            for f, fn, label in specs:
                if fn == "count":
                    ren["__count__"] = label
                elif len([1 for f2, _, _ in specs if f2 == f]) == 1:
                    ren[f] = label
                else:
                    # 同字段多种聚合：pandas 会给多级列名
                    for c in result.columns:
                        if isinstance(c, tuple) and c[0] == f and c[1] == fn:
                            ren[c] = label
            result = result.rename(columns=ren)
        else:
            row = {}
            for f, fn, label in specs:
                if fn == "count":
                    row[label] = int(len(df))
                elif fn == "nunique":
                    row[label] = int(df[f].nunique())
                else:
                    row[label] = float(df[f].agg(fn))
            result = pd.DataFrame([row])
    except Exception as exc:
        raise ValueError(f"聚合计算失败: {exc}")

    # 排序
    if body.sort_field:
        sf = body.sort_field
        if sf in result.columns:
            result = result.sort_values(sf, ascending=body.sort_order == "asc")

    # TopN 截断（有维度时才有意义）
    if body.limit and body.limit > 0 and dims:
        result = result.head(body.limit)

    return {"rows": clean_rows(result), "total": len(result)}


# ---- 只读 SQL ----

SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|pragma)\b",
    re.IGNORECASE,
)


def run_sql(ds: DataSource, sql: str) -> dict:
    """只读 SQL 查询：数据载入 sqlite 内存库执行，支持标准 SELECT 语法。"""
    sql = sql.strip().rstrip(";")
    if not sql.lower().startswith("select") and not sql.lower().startswith("with"):
        raise ValueError("仅支持 SELECT 查询（只读）")
    if SQL_FORBIDDEN.search(sql):
        raise ValueError("包含禁止的关键字，仅支持只读 SELECT 查询")

    df = load_df(ds)
    # 保留原始列名：SQLite 原生支持中文等非 ASCII 标识符；
    # 含空格等特殊字符的列名用双引号包裹即可（标准 SQL 语法）。
    df_sql = df.copy()
    df_sql.columns = [str(c) for c in df_sql.columns]

    con = sqlite3.connect(":memory:")
    try:
        df_sql.to_sql("t", con, index=False)
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        raw_rows = cur.fetchmany(501)  # 多取 1 行用于截断提示
    except sqlite3.Error as exc:
        raise ValueError(f"SQL 执行失败：{exc}")
    finally:
        con.close()

    truncated = len(raw_rows) > 500
    rows = []
    for r in raw_rows[:500]:
        rows.append({
            c: (None if (isinstance(v, float) and math.isnan(v))
                else str(v)[:19] if hasattr(v, "isoformat") else py(v))
            for c, v in zip(cols, r)
        })
    return {
        "columns": cols,
        "rows": rows,
        "total": len(rows),
        "truncated": truncated,
        "executed_sql": sql,
    }
