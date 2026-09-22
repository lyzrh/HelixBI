"""追问推荐：由 QuerySpec + 语义包**确定性生成**，默认不再调用 LLM。

现状（成本瓶颈）：`suggest_followups` 是每轮分析的**最后一次 LLM 调用**，但它推荐的
"下钻 / 对比 / 趋势"这几类问题，本质上可以从本次的指标与维度组合直接拼出来——
用一次 LLM 调用去生成三句模板化的问题，性价比很低，而且它不影响任何分析结论。

策略（`config.FOLLOWUP_MODE`）：

- `deterministic`：完全由模板生成，0 次 LLM（默认）；
- `hybrid`：确定性优先，**凑不满** `limit` 条时才补一次 LLM（保底不降级）；
- `llm`：冻结的旧行为，一律调 LLM（评估基线用）；
- `off`：不推荐追问。

诚实边界：确定性追问的**措辞**不如 LLM 灵活，但问题本身（下钻哪个维度、看什么趋势、
比什么周期）来自真实的口径与数据结构，不是编造的。
"""

from __future__ import annotations

MODE_DETERMINISTIC = "deterministic"
MODE_HYBRID = "hybrid"
MODE_LLM = "llm"
MODE_OFF = "off"

DEFAULT_LIMIT = 3


def _normalize(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch not in "？?。.，,、 \t")


def deterministic_followups(question: str, spec: dict, pack_id: str | None = None,
                            limit: int = DEFAULT_LIMIT) -> list[str]:
    """从本次意图与语义包拼出候选追问（零 token、确定性）。"""
    from backend.semantic.registry import load_pack

    spec = spec or {}
    metrics = [m for m in (spec.get("metrics") or []) if m]
    dimensions = [d for d in (spec.get("dimensions") or []) if d]
    pack = load_pack(pack_id) if pack_id else None
    pack_dims = [d.get("name") for d in (pack or {}).get("dimensions", []) if d.get("name")]
    pack_metrics = [m.get("name") for m in (pack or {}).get("metrics", []) if m.get("name")]
    if not pack_metrics:
        pack_metrics = metrics
    metric = metrics[0] if metrics else (pack_metrics[0] if pack_metrics else "")
    if not metric:
        return []

    other_dims = [d for d in pack_dims if d not in dimensions]
    grain = spec.get("grain") or "month"
    grain_word = {"day": "每天", "week": "每周", "month": "每月",
                  "quarter": "每季度", "year": "每年"}.get(grain, "每月")
    top5_dim = (dimensions or other_dims or ["主要类别"])[0]

    candidates: list[str] = []
    if other_dims:
        candidates.append(f"按{other_dims[0]}拆解{metric}，看哪个{other_dims[0]}贡献最大？")
    candidates.append(f"{metric}的{grain_word}变化趋势如何？")
    candidates.append(f"{metric}最高的前 5 个{top5_dim}是哪些？")
    if not spec.get("compare"):
        candidates.append(f"{metric}的同比和环比变化分别是多少？")
    if dimensions:
        candidates.append(f"各{dimensions[0]}的{metric}占比结构是怎样的？")
    if len(metrics) > 1:
        candidates.append(f"{metrics[1]}与{metric}的关系如何？")
    for extra_metric in pack_metrics:
        if extra_metric != metric:
            candidates.append(f"{extra_metric}的表现如何？")
            break

    asked = _normalize(question)
    out: list[str] = []
    for candidate in candidates:
        if len(out) >= limit:
            break
        if _normalize(candidate) == asked or _normalize(candidate) in asked:
            continue
        if candidate not in out:
            out.append(candidate)
    return out


def plan_followups(question: str, spec: dict, pack_id: str | None = None,
                   mode: str | None = None,
                   limit: int = DEFAULT_LIMIT) -> tuple[list[str], bool]:
    """→ (确定性候选, 是否需要补一次 LLM 调用)。

    调用方（图谱的 `suggest_followups` 节点）据此决定是否发起 LLM 调用，
    因此"省下来的那次调用"在 trace 里是**可验证**的（`llm.by_node` 里没有 followup）。
    """
    from backend import config

    resolved_mode = (mode or getattr(config, "FOLLOWUP_MODE", MODE_HYBRID) or MODE_HYBRID).lower()
    if resolved_mode == MODE_OFF:
        return [], False
    if resolved_mode == MODE_LLM:
        return [], True
    candidates = deterministic_followups(question, spec, pack_id, limit=limit)
    if resolved_mode == MODE_DETERMINISTIC:
        return candidates, False
    # hybrid：确定性凑不满才补 LLM（保底不降级）
    return candidates, len(candidates) < limit


__all__ = [
    "DEFAULT_LIMIT", "MODE_DETERMINISTIC", "MODE_HYBRID", "MODE_LLM", "MODE_OFF",
    "deterministic_followups", "plan_followups",
]
