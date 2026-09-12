"""洞察引擎：规则式异常检测（本机 pandas，确定性代码）+ LLM 经营诊断。

FineBI NEXT「主动洞察」的轻量实现：不等问题，主动扫描数据发现问题。
检测规则按语义包选择，制造业与零售业各有专属阈值规则。
"""

from datetime import datetime

import pandas as pd

from backend.agent.graph import get_llm
from backend.semantic import load_pack
from backend.models import DataSource, Insight, jdump

RULE_LABELS = {
    "spike": "指标突变", "streak": "连续趋势", "outlier": "异常值",
    "topn_shift": "头部份额变化", "quality": "数据质量", "threshold": "业务阈值越界",
    "gap": "时间断档",
}


def detect_all(ds: DataSource) -> list[dict]:
    df = _read(ds)
    if df is None or df.empty:
        return []
    pack = load_pack(ds.pack_id) if ds.pack_id else None
    findings: list[dict] = []
    findings += _detect_quality(df, pack)
    findings += _detect_spike(df, pack)
    findings += _detect_streak(df, pack)
    findings += _detect_outlier(df, pack)
    findings += _detect_topn_shift(df, pack)
    findings += _detect_gap(df, pack)
    findings += _detect_threshold(df, pack)
    for i, f in enumerate(findings):
        f["id"] = i + 1
    return findings


def detect_and_persist(db, ds: DataSource) -> dict:
    """刷新该数据源的洞察（upsert 语义）。

    - 已存在的发现（rule_id + title 匹配）：更新严重度/详情/证据，
      保留 status（已读/已解决）与已生成的 LLM 诊断报告
    - 新发现：插入，status=new
    - 已消失的发现：删除
    这样定时扫描反复执行不会把用户的处理状态冲掉。
    """
    findings = detect_all(ds)
    existing = db.query(Insight).filter(Insight.data_source_id == ds.id).all()
    by_key = {(i.rule_id, i.title): i for i in existing}
    seen: set[tuple[str, str]] = set()
    new_count = 0
    for f in findings:
        key = (f["rule_id"], f["title"])
        seen.add(key)
        cur = by_key.get(key)
        if cur is not None:
            cur.severity = f["severity"]
            cur.detail = f["detail"]
            cur.evidence = jdump(f["evidence"])
        else:
            db.add(Insight(
                data_source_id=ds.id, rule_id=f["rule_id"], severity=f["severity"],
                title=f["title"], detail=f["detail"], evidence=jdump(f["evidence"]),
            ))
            new_count += 1
    for key, row in by_key.items():
        if key not in seen:
            db.delete(row)
    db.commit()
    return {"data_source": ds.name, "data_source_id": ds.id,
            "total": len(findings), "new": new_count, "findings": findings}


def generate_report(insight: Insight, ds: DataSource) -> str:
    """LLM 经营诊断：现象 → 数据证据 → 可能原因 → 建议动作。"""
    from langchain_core.messages import HumanMessage

    pack = load_pack(ds.pack_id) if ds.pack_id else {}
    prompt = f"""你是制造业/零售业的经营诊断顾问。基于以下数据发现，输出诊断报告（中文 markdown）。

## 数据源
{ds.name}（行业：{pack.get('name', '通用')}）

## 发现的异常
{insight.title}
{insight.detail}

## 数字证据
```json
{insight.evidence}
```

要求（严格按此结构输出）：
1. **现象**：一两句话说清楚发生了什么
2. **数据证据**：引用上面的数字
3. **可能原因**：2-3 条，基于维度下钻证据合理推断，标注"待验证"
4. **建议动作**：2-3 条可执行的业务动作
不超过 300 字。"""
    response = get_llm().invoke([HumanMessage(content=prompt)])
    return response.content if isinstance(response.content, str) else str(response.content)


# ---- 读取与基础 ----

def _read(ds: DataSource) -> pd.DataFrame | None:
    path = ds.file_path if ds.type == "file" else ds.materialized_path
    if not path:
        return None
    try:
        if str(path).lower().endswith(".parquet"):
            return pd.read_parquet(path)
        if str(path).lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(path)
        return pd.read_csv(path)
    except Exception:
        return None


def _pack_meta(pack: dict | None) -> tuple[str | None, str | None, str | None, str]:
    """→ (time_field, metric_field, metric_name, dim_field)"""
    if not pack:
        return None, None, None, ""
    time_field = pack.get("time_field")
    metric = next((m for m in pack.get("metrics", []) if m.get("field")), None)
    dim = next((d for d in pack.get("dimensions", []) if not d.get("optional")), None)
    return time_field, (metric or {}).get("field"), (metric or {}).get("name"), (dim or {}).get("field", "")


def _monthly(df: pd.DataFrame, time_field: str, metric_field: str) -> pd.DataFrame | None:
    s = pd.to_datetime(df[time_field], errors="coerce")
    if s.isna().all():
        return None
    s = _drop_partial_last_month(s)
    if s.empty:
        return None
    tmp = pd.DataFrame({"period": s.dt.to_period("M").astype(str), "value": df[metric_field].loc[s.index]})
    return tmp.groupby("period")["value"].sum().reset_index()


def _drop_partial_last_month(s: pd.Series) -> pd.Series:
    """剔除不完整的最后一个月（残月会让环比/突变/趋势产生巨大假象）。

    判定：该月最后一条记录距月末 > 3 天即视为残月。
    """
    if s.empty:
        return s
    max_ts = s.max()
    month_end = max_ts.to_period("M").to_timestamp(how="end").normalize()
    if (month_end - max_ts.normalize()).days > 3:
        s = s[s < max_ts.to_period("M").to_timestamp(how="start")]
    return s


# ---- 规则实现 ----

def _detect_quality(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    findings = []
    time_field, metric_field, metric_name, _ = _pack_meta(pack)
    # 关键列缺失率
    for col in ([time_field, metric_field] if time_field and metric_field else []):
        if col in df.columns:
            rate = float(df[col].isna().mean())
            if rate > 0.10:
                findings.append(dict(
                    rule_id="quality", severity="warning",
                    title=f"关键列「{col}」缺失率 {rate * 100:.0f}%",
                    detail=f"缺失率超过 10%，可能影响以此列为核心的分析口径。",
                    evidence={"column": col, "missing_rate": round(rate, 4),
                              "total_rows": len(df)}))
    # 重复行
    dup = int(df.duplicated().sum())
    if len(df) and dup / len(df) > 0.01:
        findings.append(dict(
            rule_id="quality", severity="info",
            title=f"存在 {dup} 行完全重复数据（{dup / len(df) * 100:.1f}%）",
            detail="重复行可能导致销售额/产量等指标被重复统计。",
            evidence={"duplicated_rows": dup, "total_rows": len(df)}))
    # 负值
    if metric_field and metric_field in df.columns:
        neg = int((df[metric_field] < 0).sum())
        if neg:
            findings.append(dict(
                rule_id="quality", severity="warning",
                title=f"指标「{metric_name}」存在 {neg} 行负值",
                detail="销售额/产量类指标出现负值，通常是退货冲减或录入错误，需确认口径。",
                evidence={"negative_rows": neg, "column": metric_field}))
    # 未来日期
    if time_field and time_field in df.columns:
        s = pd.to_datetime(df[time_field], errors="coerce")
        future = int((s > pd.Timestamp.now()).sum())
        if future:
            findings.append(dict(
                rule_id="quality", severity="warning",
                title=f"时间字段「{time_field}」存在 {future} 行未来日期",
                detail="未来日期会污染趋势分析与预测。",
                evidence={"future_rows": future, "column": time_field}))
    return findings


def _detect_spike(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    time_field, metric_field, metric_name, _ = _pack_meta(pack)
    if not (time_field and metric_field) or time_field not in df.columns:
        return []
    m = _monthly(df, time_field, metric_field)
    if m is None or len(m) < 2:
        return []
    findings = []
    for i in range(1, len(m)):
        prev, cur = m.iloc[i - 1], m.iloc[i]
        if abs(prev["value"]) >= 50:
            pct = (cur["value"] - prev["value"]) / abs(prev["value"]) * 100
            if abs(pct) >= 20:
                severity = "critical" if abs(pct) >= 50 else "warning"
                findings.append(dict(
                    rule_id="spike", severity=severity,
                    title=f"{metric_name}在 {cur['period']} 环比{'上升' if pct > 0 else '下降'} {abs(pct):.0f}%",
                    detail=f"{cur['period']} {metric_name}为 {cur['value']:,.0f}，上月 {prev['value']:,.0f}，"
                           f"环比变化 {pct:+.1f}%，波动显著。",
                    evidence={"period": str(cur["period"]), "current": float(cur["value"]),
                              "previous": float(prev["value"]), "pct_change": round(pct, 2)}))
    return findings


def _detect_streak(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    time_field, metric_field, metric_name, _ = _pack_meta(pack)
    if not (time_field and metric_field) or time_field not in df.columns:
        return []
    m = _monthly(df, time_field, metric_field)
    if m is None or len(m) < 4:
        return []
    findings = []
    diffs = m["value"].diff().dropna()
    sign = diffs > 0
    run = 1
    for i in range(1, len(sign)):
        if sign.iloc[i] == sign.iloc[i - 1]:
            run += 1
        else:
            run = 1
        if run >= 3:
            direction = "上升" if sign.iloc[i] else "下降"
            severity = "warning" if not sign.iloc[i] else "info"
            period = str(m.iloc[i + 1]["period"])
            findings.append(dict(
                rule_id="streak", severity=severity,
                title=f"{metric_name}连续 {run} 个月{direction}",
                detail=f"{period} {metric_name}已连续 {run} 个月{direction}，"
                       f"当前值 {m.iloc[i + 1]['value']:,.0f}。",
                evidence={"streak_months": run, "direction": direction,
                          "latest_period": period,
                          "latest_value": float(m.iloc[i + 1]["value"])}))
            break  # 只报最近一次连续趋势
    return findings


def _detect_outlier(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    time_field, metric_field, metric_name, _ = _pack_meta(pack)
    if not (time_field and metric_field) or time_field not in df.columns:
        return []
    m = _monthly(df, time_field, metric_field)
    if m is None or len(m) < 4:
        return []
    q1, q3 = m["value"].quantile([0.25, 0.75])
    iqr = q3 - q1
    if iqr <= 0:
        return []
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    out = m[(m["value"] < lo) | (m["value"] > hi)]
    findings = []
    for _, row in out.iterrows():
        findings.append(dict(
            rule_id="outlier", severity="info",
            title=f"{metric_name}在 {row['period']} 显著偏离常态区间",
            detail=f"{row['period']} 值 {row['value']:,.0f}，超出 IQR 正常区间 [{lo:,.0f}, {hi:,.0f}]。",
            evidence={"period": str(row["period"]), "value": float(row["value"]),
                      "normal_range": [float(lo), float(hi)]}))
    return findings


def _detect_topn_shift(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    time_field, metric_field, metric_name, dim_field = _pack_meta(pack)
    if not (time_field and metric_field and dim_field):
        return []
    if not all(c in df.columns for c in (time_field, metric_field, dim_field)):
        return []
    s = pd.to_datetime(df[time_field], errors="coerce")
    if s.isna().all():
        return []
    s = _drop_partial_last_month(s)
    if s.empty:
        return []
    tmp = pd.DataFrame({"month": s.dt.to_period("M"), "value": df[metric_field].loc[s.index],
                        "dim": df[dim_field].loc[s.index]})
    months = sorted(m for m in tmp["month"].unique() if pd.notna(m))
    if len(months) < 2:
        return []
    findings = []
    last, prev = months[-1], months[-2]
    for mo in (prev, last):
        share = (tmp[tmp["month"] == mo].groupby("dim")["value"].sum()
                 / tmp[tmp["month"] == mo]["value"].sum())
        if mo == prev:
            prev_top, prev_share = (share.idxmax(), float(share.max())) if len(share) else ("", 0)
        else:
            last_top, last_share = (share.idxmax(), float(share.max())) if len(share) else ("", 0)
    if prev_top and last_top:
        delta_pp = (last_share - prev_share) * 100
        if abs(delta_pp) >= 5:
            findings.append(dict(
                rule_id="topn_shift", severity="warning",
                title=f"头部{dim_field}份额变化 {delta_pp:+.1f} 个百分点",
                detail=f"按{dim_field}看，{last} 月 Top1 为「{last_top}」（份额 {last_share * 100:.1f}%），"
                       f"上月 Top1 为「{prev_top}」（{prev_share * 100:.1f}%）。",
                evidence={"last_top": str(last_top), "last_share": round(last_share, 4),
                          "prev_top": str(prev_top), "prev_share": round(prev_share, 4),
                          "delta_pp": round(delta_pp, 2)}))
    return findings


def _detect_gap(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    time_field, _, _, _ = _pack_meta(pack)
    if not time_field or time_field not in df.columns:
        return []
    s = pd.to_datetime(df[time_field], errors="coerce").dropna()
    if len(s) < 10:
        return []
    days = s.dt.normalize().sort_values().unique()
    if len(days) < 2:
        return []
    full = pd.date_range(days[0], days[-1], freq="D")
    missing = len(full) - len(days)
    if missing > len(full) * 0.05 and missing > 3:
        findings = [dict(
            rule_id="gap", severity="info",
            title=f"时间序列存在 {missing} 天断档",
            detail=f"数据覆盖 {str(days[0])[:10]} ~ {str(days[-1])[:10]}，其中 {missing} 天无记录"
                   f"（占比 {missing / len(full) * 100:.0f}%），趋势分析可能出现虚假波动。",
            evidence={"missing_days": int(missing), "total_days": len(full),
                      "start": str(days[0])[:10], "end": str(days[-1])[:10]})]
        return findings
    return []


def _detect_threshold(df: pd.DataFrame, pack: dict | None) -> list[dict]:
    """行业专属阈值：制造（达成率/良率）、零售（区域客单价）。"""
    if not pack:
        return []
    pack_id = pack.get("pack")
    findings = []

    if pack_id == "manufacturing_production":
        cols = {"计划产量", "实际产量", "不良数", "产线"}
        if not cols.issubset(set(df.columns)):
            return findings
        g = df.groupby("产线").agg(计划=("计划产量", "sum"), 实际=("实际产量", "sum"),
                                   不良=("不良数", "sum"))
        g["达成率"] = g["实际"] / g["计划"] * 100
        g["良率"] = (g["实际"] - g["不良"]) / g["实际"] * 100
        for line, row in g.iterrows():
            if row["达成率"] < 90:
                findings.append(dict(
                    rule_id="threshold", severity="critical",
                    title=f"产线「{line}」达成率 {row['达成率']:.1f}%，低于 90% 阈值",
                    detail=f"{line} 实际产量 {row['实际']:,.0f} / 计划 {row['计划']:,.0f}，"
                           f"缺口 {row['计划'] - row['实际']:,.0f} 件，建议优先排查。",
                    evidence={"line": str(line), "achievement_rate": round(float(row["达成率"]), 2),
                              "planned": float(row["计划"]), "actual": float(row["实际"])}))
            if row["良率"] < 95:
                findings.append(dict(
                    rule_id="threshold", severity="critical",
                    title=f"产线「{line}」良率 {row['良率']:.1f}%，低于 95% 阈值",
                    detail=f"{line} 不良数 {row['不良']:,.0f} 件，不良率 {100 - row['良率']:.1f}%。",
                    evidence={"line": str(line), "yield_rate": round(float(row["良率"]), 2),
                              "defects": float(row["不良"])}))

    elif pack_id == "retail_sales":
        cols = {"销售额", "数量", "区域"}
        if not cols.issubset(set(df.columns)):
            return findings
        g = df.groupby("区域").agg(销售额=("销售额", "sum"), 数量=("数量", "sum"))
        g["客单价"] = g["销售额"] / g["数量"]
        mean_price = g["客单价"].mean()
        for region, row in g.iterrows():
            if mean_price and row["客单价"] < mean_price * 0.7:
                findings.append(dict(
                    rule_id="threshold", severity="critical",
                    title=f"区域「{region}」客单价显著偏低",
                    detail=f"{region} 客单价 {row['客单价']:.1f} 元/件，仅为各区域均值 "
                           f"（{mean_price:.1f}）的 {row['客单价'] / mean_price * 100:.0f}%。",
                    evidence={"region": str(region),
                              "avg_price": round(float(row["客单价"]), 2),
                              "mean_price": round(float(mean_price), 2)}))
    return findings
