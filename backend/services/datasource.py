"""数据源服务：文件 / 数据库连接、表浏览、预览与物化。

沙箱无网络（docker --network none），DB 数据必须先物化为 parquet 文件，
与上传文件一样以文件形式进入沙箱 /data —— 这是内核结果契约的硬约束。
"""

import math
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text

from app.semantic import load_pack
from backend.config import (
    MATERIALIZED_DIR, MATERIALIZED_MAX_ROWS, MATERIALIZED_TTL_HOURS,
)
from backend.models import DataSource


def build_url(db_type: str, host: str, port: int | None, database: str,
              username: str, password: str, sqlite_path: str = "") -> str:
    if db_type == "sqlite":
        return f"sqlite:///{sqlite_path or database}"
    if db_type == "mysql":
        return f"mysql+pymysql://{username}:{password}@{host}:{port or 3306}/{database}?charset=utf8mb4"
    if db_type == "postgresql":
        return f"postgresql+psycopg2://{username}:{password}@{host}:{port or 5432}/{database}"
    raise ValueError(f"不支持的数据库类型: {db_type}")


def build_engine(ds: DataSource):
    return create_engine(build_url(
        ds.db_type, ds.host or "", ds.port, ds.database_name or "",
        ds.username or "", ds.password or "",
    ), pool_pre_ping=True)


def test_connection(db_type: str, host: str, port: int | None, database: str,
                    username: str, password: str, sqlite_path: str = "") -> tuple[bool, str]:
    try:
        url = build_url(db_type, host, port, database, username, password, sqlite_path)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "连接成功"
    except Exception as exc:
        return False, f"连接失败: {exc}"


def list_tables(ds: DataSource) -> list[dict]:
    eng = build_engine(ds)
    insp = inspect(eng)
    tables = []
    for name in insp.get_table_names():
        row_estimate = None
        try:
            with eng.connect() as conn:
                row_estimate = int(conn.execute(
                    text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0)
        except Exception:
            pass
        tables.append({"name": name, "row_estimate": row_estimate})
    return tables


def preview(ds: DataSource, table: str | None = None, limit: int = 50) -> dict:
    """db 源预览表；file 源预览文件前 limit 行。"""
    if ds.type == "file":
        df = _read_file(ds.file_path).head(limit)
        return {"columns": list(df.columns), "rows": df.to_dict("records")}
    eng = build_engine(ds)
    df = pd.read_sql_query(f'SELECT * FROM "{table}"', eng).head(limit)
    return {"columns": list(df.columns), "rows": _clean_rows(df)}


def materialize(ds: DataSource, table: str | None = None, force: bool = False) -> dict:
    """db 表 → parquet 缓存（data/materialized/ds{id}_{table}.parquet）。

    超过行数上限时按语义包 time_field 取最近数据并标记 truncated。
    """
    if ds.type != "db":
        raise ValueError("仅数据库类型数据源支持物化")
    table = table or ds.materialized_table
    if not table:
        raise ValueError("请指定要物化的表")
    out_path = MATERIALIZED_DIR / f"ds{ds.id}_{_safe_name(table)}.parquet"

    if (not force and out_path.exists() and ds.materialized_at
            and _within_ttl(ds.materialized_at)):
        return {"path": str(out_path), "rows": ds.row_count or 0,
                "truncated": bool(ds.materialized_truncated), "cached": True}

    eng = build_engine(ds)
    with eng.connect() as conn:
        total = int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)

    truncated = False
    if total <= MATERIALIZED_MAX_ROWS:
        df = pd.read_sql_table(table, eng)
    else:
        pack = load_pack(ds.pack_id) if ds.pack_id else None
        time_field = pack.get("time_field") if pack else None
        cols = [c["name"] for c in inspect(eng).get_columns(table)]
        if time_field and time_field in cols:
            df = pd.read_sql_query(
                f'SELECT * FROM "{table}" ORDER BY "{time_field}" DESC LIMIT {MATERIALIZED_MAX_ROWS}',
                eng)
        else:
            df = pd.read_sql_query(
                f'SELECT * FROM "{table}" LIMIT {MATERIALIZED_MAX_ROWS}', eng)
        truncated = True

    df.to_parquet(out_path, index=False)
    ds.materialized_path = str(out_path)
    ds.materialized_table = table
    ds.materialized_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ds.materialized_truncated = truncated
    ds.row_count = len(df)
    ds.columns_json = _jdump(list(df.columns))
    return {"path": str(out_path), "rows": len(df),
            "truncated": truncated, "cached": False}


def needs_materialize(ds: DataSource) -> bool:
    """惰性物化检查：db 源且缓存缺失或 TTL 过期。"""
    if ds.type != "db":
        return False
    if not ds.materialized_path or not Path(ds.materialized_path).exists():
        return True
    if ds.materialized_at and not _within_ttl(ds.materialized_at):
        return True
    return False


def read_columns(path: str) -> list[str]:
    """轻量读取列名（供 pack 推断与 Skill 匹配）。"""
    df = _read_file(path, nrows=50)
    return list(df.columns)


def count_rows(path: str) -> int:
    """轻量行数统计（csv 按字节流计数，excel/parquet 用 pandas）。"""
    suffix = str(path).lower()
    try:
        if suffix.endswith(".csv"):
            with open(path, "rb") as f:
                return max(sum(1 for _ in f) - 1, 0)
        if suffix.endswith((".xlsx", ".xls")):
            return int(len(pd.read_excel(path)))
        if suffix.endswith(".parquet"):
            return int(len(pd.read_parquet(path)))
    except Exception:
        return 0
    return 0


# ---- 内部工具 ----

def _read_file(path: str, nrows: int | None = None) -> pd.DataFrame:
    suffix = str(path).lower()
    if suffix.endswith((".xlsx", ".xls")):
        return pd.read_excel(path, nrows=nrows)
    if suffix.endswith(".parquet"):
        # parquet 不支持 nrows，读全量后截断
        df = pd.read_parquet(path)
        return df.head(nrows) if nrows else df
    return pd.read_csv(path, nrows=nrows)


def _clean_rows(df: pd.DataFrame) -> list[dict]:
    """把 DataFrame 行转成 JSON 安全的 records（NaN→None、时间→str）。"""
    cleaned = df.copy()
    for col in cleaned.columns:
        if pd.api.types.is_datetime64_any_dtype(cleaned[col]):
            cleaned[col] = cleaned[col].astype(str)
    cleaned = cleaned.where(pd.notna(cleaned), None)
    records = cleaned.to_dict("records")
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) if isinstance(v, float) else False):
                row[k] = None
            elif isinstance(v, (pd.Timestamp,)):
                row[k] = str(v)
    return records


def _within_ttl(materialized_at: str) -> bool:
    try:
        ts = datetime.strptime(materialized_at, "%Y-%m-%d %H:%M:%S")
        return datetime.now() - ts < timedelta(hours=MATERIALIZED_TTL_HOURS)
    except ValueError:
        return False


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _jdump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
