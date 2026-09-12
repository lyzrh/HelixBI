"""评估指标与报告渲染。

约定：
- 所有比率都是 **0~1 的小数**，渲染时再转百分比；
- 集合级指标用 micro precision / recall / F1（对各样本求 TP/FP/FN 后汇总）；
- 空真值集合视为"无需匹配"，precision 记 1（避免用"没标注"来惩罚系统）。
"""

import unicodedata
from datetime import datetime


def prf(pred: set, truth: set) -> dict:
    """集合级 precision / recall / F1。"""
    tp = len(pred & truth)
    fp = len(pred - truth)
    fn = len(truth - pred)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn}


class Counter:
    """比率累加器：记录命中数 / 总数。"""

    def __init__(self) -> None:
        self.total = 0
        self.hits = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.hits += int(bool(ok))

    @property
    def rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        return {"hits": self.hits, "total": self.total, "rate": self.rate}


class Latency:
    """耗时采样（毫秒），给出 p50 / p95。确定性阶段的耗时常在亚毫秒级，保留 3 位。"""

    def __init__(self) -> None:
        self.samples: list[float] = []

    def add(self, ms: float) -> None:
        self.samples.append(ms)

    def _quantile(self, q: float) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        idx = min(int(q * (len(ordered) - 1) + 0.5), len(ordered) - 1)
        return ordered[idx]

    def as_dict(self) -> dict:
        return {
            "count": len(self.samples),
            "p50_ms": round(self._quantile(0.50), 3),
            "p95_ms": round(self._quantile(0.95), 3),
            "avg_ms": round(sum(self.samples) / len(self.samples), 3) if self.samples else 0.0,
            "max_ms": round(max(self.samples), 3) if self.samples else 0.0,
        }


# ---- 报告渲染（等宽表格，中文按 2 列宽计） ----

def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def _row(label: str, value: str, extra: str = "", indent: int = 0) -> str:
    prefix = "  " * indent
    tail = f"  {extra}" if extra else ""
    return f"{_pad(prefix + label, 34)}{_pad(value, 10)}{tail}".rstrip()


def render_report(report: dict) -> str:
    """把 run_all() 的结果渲染成可读报告（CLI 与 README 共用一份口径）。"""
    lines: list[str] = []
    lines.append("Evaluation Report — HelixBI Agent Pipeline")
    lines.append("=" * 66)
    ds = report.get("dataset", {})
    lines.append(_row("问题集", f"{ds.get('cases', 0)} 条",
                      f"语义包：{', '.join(ds.get('packs', [])) or '-'}"))
    if ds.get("generated_at"):
        lines.append(_row("生成时间", "", ds["generated_at"]))
    lines.append("-" * 66)

    sem = report.get("semantic", {})
    if sem.get("status", "ok") == "ok":
        n = sem["cases"]
        lines.append(_row("语义解析准确率 (严格)", _pct(sem["accuracy"]),
                          f"({sem['accuracy_hits']}/{n})"))
        lines.append(_row("语义解析准确率 (宽松)", _pct(sem["relaxed_accuracy"]),
                          f"({sem['relaxed_hits']}/{n})"))
        lines.append(_row("指标集合命中", _pct(sem["metric_exact"]["rate"]),
                          f"({sem['metric_exact']['hits']}/{n})", indent=1))
        lines.append(_row("维度集合命中", _pct(sem["dimension_exact"]["rate"]),
                          f"({sem['dimension_exact']['hits']}/{n})", indent=1))
        lines.append(_row("同比/环比判定", _pct(sem["comparison"]["rate"]),
                          f"({sem['comparison']['hits']}/{n})", indent=1))
        lines.append(_row("分析类型判定", _pct(sem["analysis_type"]["rate"]),
                          f"({sem['analysis_type']['hits']}/{n})", indent=1))
        mp, dp = sem["metric_prf"], sem["dimension_prf"]
        lines.append(_row("指标级 P / R", f"{_pct(mp['precision'])}/{_pct(mp['recall'])}",
                          f"F1 {_pct(mp['f1'])}", indent=1))
        lines.append(_row("维度级 P / R", f"{_pct(dp['precision'])}/{_pct(dp['recall'])}",
                          f"F1 {_pct(dp['f1'])}", indent=1))
        lat = sem.get("latency_ms", {})
        lines.append(_row("解析耗时 p50 / p95",
                          f"{lat.get('p50_ms', 0)}/{lat.get('p95_ms', 0)} ms",
                          "确定性解析，零 token", indent=1))
        for name, st in (sem.get("by_dataset") or {}).items():
            lines.append(_row(f"└ {name}", _pct(st["accuracy"]),
                              f"({st['hits']}/{st['cases']})", indent=1))
    else:
        lines.append(_row("语义解析准确率", "未采集", sem.get("reason", "")))

    plan = report.get("planning", {})
    if plan.get("status", "ok") == "ok":
        lines.append(_row("口径注入完整率", _pct(plan["coverage"]),
                          f"({plan['coverage_hits']}/{plan['coverage_total']} 条口径)"))
        lines.append(_row("已解析口径渲染保真度", _pct(plan["fidelity"]),
                          f"({plan['fidelity_hits']}/{plan['resolved_entries']})", indent=1))
        lines.append(_row("派生指标公式注入", _pct(plan["formula_rate"]["rate"]),
                          f"({plan['formula_rate']['hits']}/{plan['formula_rate']['total']})",
                          indent=1))
    else:
        lines.append(_row("口径注入完整率", "未采集", plan.get("reason", "")))

    sk = report.get("skills", {})
    if sk.get("status", "ok") == "ok":
        lines.append(_row("Skill 匹配 Top1 准确率", _pct(sk["top1_accuracy"]),
                          f"{sk['queries']} 次查询 / {sk['groups']} 条路径"))
        lines.append(_row(f"Skill 匹配 Recall@{sk.get('k', 2)}", _pct(sk["recall"]), ""))
        lines.append(_row("Skill 命中率", _pct(sk["hit_rate"]), "", indent=1))
    else:
        lines.append(_row("Skill 匹配", "未采集", sk.get("reason", "")))

    rp = report.get("replay", {})
    if rp.get("status", "ok") == "ok":
        lines.append(_row("重放准入判定正确率", _pct(rp["eligibility_rate"]),
                          f"({rp['eligibility_hits']}/{rp['cases']})"))

    lines.append("-" * 66)
    lines.append("以下阶段需要 Docker 沙箱 / LLM，资源缺失时显示「未采集」而非编造数字：")
    for key, label in (("execution", "代码执行成功率"), ("self_repair", "自修复成功率"),
                       ("end_to_end", "端到端成功率")):
        stage = report.get(key, {})
        if stage.get("status") == "ok":
            extra = f"({stage.get('ok_hits', 0)}/{stage.get('cases', 0)})"
            lat = stage.get("latency_ms") or {}
            if lat.get("p50_ms"):
                extra += f"  延迟 p50 {lat['p50_ms']} ms"
            calls = stage.get("llm_calls") or {}
            if calls.get("avg_ms"):
                extra += f"  平均节点数 {calls['avg_ms']}"
            lines.append(_row(f"  {label}", _pct(stage.get("rate", 0.0)), extra))
        else:
            lines.append(_row(f"  {label}", "未采集", stage.get("reason", "")))
    lines.append("=" * 66)
    return "\n".join(lines)


def new_report(cases: int, packs: list[str]) -> dict:
    return {
        "dataset": {"cases": cases, "packs": packs,
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    }


__all__ = ["prf", "Counter", "Latency", "render_report", "new_report"]
