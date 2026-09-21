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


def _signed(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}"


def _pp(value: float, digits: int = 1) -> str:
    """比率差值 → 百分点（读报告的人关心的是 pp，不是小数）。"""
    return f"{value * 100:+.{digits}f}pp"


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
    base_sk = report.get("baseline_skills", {})
    if sk.get("status", "ok") == "ok":
        lines.append(_row("Skill 检索 Top1 准确率", _pct(sk["top1_accuracy"]),
                          f"{sk['queries']} 次查询 / {sk['groups']} 条路径"))
        lines.append(_row(f"Skill 检索 Recall@{sk.get('k', 3)}", _pct(sk["recall"]),
                          f"Recall@2 {_pct(sk.get('recall_at_2', 0.0))}", indent=1))
        lines.append(_row("Skill 命中率", _pct(sk["hit_rate"]), "", indent=1))
        if base_sk.get("status") == "ok":
            lines.append(_row("└ 基线（V1 词面打分）Top1", _pct(base_sk["top1_accuracy"]),
                              f"Δ {_pp(sk['top1_accuracy'] - base_sk['top1_accuracy'])}",
                              indent=1))
    else:
        lines.append(_row("Skill 匹配", "未采集", sk.get("reason", "")))

    rp = report.get("replay", {})
    if rp.get("status", "ok") == "ok":
        lines.append(_row("重放准入判定正确率", _pct(rp["eligibility_rate"]),
                          f"({rp['eligibility_hits']}/{rp['cases']})  结构守卫"))

    ret = report.get("retrieval", {})
    if ret.get("status") == "ok":
        v1, v2 = ret["v1"], ret["v2"]
        lines.append("-" * 66)
        lines.append("Skill Retrieval V2（完整路径签名口径：包 / 指标 / 维度 / 类型 / 排序 / 时间窗）")
        f1, f2 = v1["fine"], v2["fine"]
        lines.append(_row("检索 Top1（重放口径）", _pct(f2["top1_accuracy"]),
                          f"V1 {_pct(f1['top1_accuracy'])}  "
                          f"Δ {_pp(f2['top1_accuracy'] - f1['top1_accuracy'])}"))
        lines.append(_row("Recall@2", _pct(f2["recall_at_2"]),
                          f"V1 {_pct(f1['recall_at_2'])}", indent=1))
        lines.append(_row("重放 Precision", _pct(f2["replay_precision"]),
                          f"V1 {_pct(f1['replay_precision'])}", indent=1))
        lines.append(_row("重放 Recall", _pct(f2["replay_recall"]),
                          f"V1 {_pct(f1['replay_recall'])}", indent=1))
        lines.append(_row("False Replay Rate", _pct(f2["false_replay_rate"]),
                          f"V1 {_pct(f1['false_replay_rate'])}  "
                          f"({len(f1['false_replay_cases'])} → "
                          f"{len(f2['false_replay_cases'])} 条)", indent=1))
        a1, a2 = v1["admission"], v2["admission"]
        lines.append(_row("准入判定正确率（对抗集）", _pct(a2["admission_accuracy"]),
                          f"{a2['cases']} 条对抗样例 / V1 {_pct(a1['admission_accuracy'])}"))
        lines.append(_row("└ 误放行 / 误拒绝",
                          f"{a2['false_accept']} / {a2['false_reject']}",
                          f"V1 {a1['false_accept']} / {a1['false_reject']}", indent=1))
        lines.append(_row("└ 判定+原因一致性", _pct(a2["decision_and_reason_accuracy"]),
                          "零 token、可解释", indent=1))
        e1, e2 = v1["efficiency"], v2["efficiency"]
        lines.append(_row("重放占比", _pct(e2["replay_share"]),
                          f"V1 {_pct(e1['replay_share'])}  Agent 兜底 "
                          f"{_pct(e2['agent_fallback_rate'])}"))
        lines.append(_row("LLM 调用 / 查询", f"{e2['llm_calls_per_query']:.2f}",
                          f"V1 {e1['llm_calls_per_query']:.2f}  "
                          f"Δ {_signed(e2['llm_calls_per_query'] - e1['llm_calls_per_query'], 2)}",
                          indent=1))
        lines.append(_row("└ 计入误重放后", f"{e2['llm_calls_per_query_incl_misreplay']:.2f}",
                          f"V1 {e1['llm_calls_per_query_incl_misreplay']:.2f}"
                          "（V1 的低调用量来自错误重放）", indent=1))
        lines.append(_row("平均 Token / 查询", f"{e2['avg_tokens_per_query']:.0f}",
                          f"V1 {e1['avg_tokens_per_query']:.0f}", indent=1))
        lines.append(_row("平均 LLM 延迟 / 查询",
                          f"{e2['avg_latency_ms']:.0f} ms",
                          f"V1 {e1['avg_latency_ms']:.0f} ms"
                          f"  单次 Agent ~{e2['avg_latency_ms_per_agent_query']:.0f} ms",
                          indent=1))
        cost = e2["cost_usd_per_query"]
        lines.append(_row("估算成本 / 查询",
                          f"${cost:.6f}" if cost is not None else "未配置单价",
                          f"画像来源 {e2['profile']['source']}", indent=1))
        lines.append(_row("└ 沙箱耗时", "未采集", "需 Docker 实测，不编造数字", indent=1))

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
