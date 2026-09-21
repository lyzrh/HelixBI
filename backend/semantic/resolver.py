"""确定性语义解析器：问题 → 语义包内的指标 / 维度 / 时间 / 对比意图。

为什么需要一个「非 LLM」的解析器：

- LLM 的 `parse_intent` 能产出 QuerySpec，但它是**不可离线、不可回归、不可度量**的；
- 语义层的核心资产是「口径」，口径解析应当存在一个**确定性基线**——
  既可以给 LLM 提供先验，又可以被评测集直接度量（Semantic Resolution Accuracy）。

本模块是纯函数、零 token、无副作用，只依赖 `semantic_packs/` 配置。
"""

import re

from backend.semantic.registry import load_pack

# ---- 时间表达 ----

_RANGE_PATTERNS = [
    (re.compile(r"近\s*(\d+)\s*(?:天|日)"), "day"),
    (re.compile(r"最近\s*(\d+)\s*(?:天|日)"), "day"),
    (re.compile(r"过去\s*(\d+)\s*(?:天|日)"), "day"),
    (re.compile(r"近\s*(\d+)\s*(?:周|星期)"), "week"),
    (re.compile(r"近\s*(\d+)\s*个?月"), "month"),
    (re.compile(r"近\s*(\d+)\s*年"), "year"),
]

_NAMED_RANGES = [
    ("今天", "day"), ("昨天", "day"), ("前天", "day"),
    ("本周", "week"), ("上周", "week"),
    ("本月", "month"), ("上月", "month"),
    ("本季度", "quarter"), ("上季度", "quarter"),
    ("今年", "year"), ("去年", "year"),
]

_GRAIN_WORDS = [
    ("week", ["按周", "每周", "周度", "周趋势", "每周趋势"]),
    ("month", ["按月", "每月", "月度", "月趋势", "逐月"]),
    ("quarter", ["按季度", "季度趋势"]),
    ("year", ["按年", "年度", "逐年"]),
    ("day", ["按天", "按日", "每天", "每日"]),
]

_YOY_WORDS = ["同比", "去年同期", "年同比", "较去年同期"]
_MOM_WORDS = ["环比", "月环比", "上期", "上月比", "较上期", "上一周期", "前一周期"]
_TREND_WORDS = ["趋势", "走势", "变化情况", "随时间的", "逐日", "逐月", "逐周"]
_CHANGE_WORDS = ["变化", "上升", "下降", "增长", "减少", "下滑", "提升", "波动", "差异", "对比", "相差", "差距"]
_RANK_DESC_WORDS = ["最高", "最大", "最多", "最好", "最长", "最久", "最快",
                    "排名", "从高到低", "降序", "top"]
_RANK_ASC_WORDS = ["最低", "最小", "最少", "最差", "最短", "最慢", "从低到高", "升序"]

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# ---- 术语索引与匹配 ----

def _term_index(pack: dict) -> list[tuple[str, str, dict]]:
    """术语 → (term, kind, entry)，按长度降序（长词优先）。

    必须先长后短：否则「销售额」会被同义词「金额」抢先命中，留下「销售」残渣。
    """
    index: list[tuple[str, str, dict]] = []
    for m in pack.get("metrics", []):
        for t in {m.get("name", ""), *m.get("synonyms", [])}:
            if t:
                index.append((t, "metric", m))
    for d in pack.get("dimensions", []):
        for t in {d.get("name", ""), *d.get("synonyms", [])}:
            if t:
                index.append((t, "dimension", d))
    index.sort(key=lambda item: -len(item[0]))
    return index


def _match_terms(question: str, index: list[tuple[str, str, dict]]) -> list[tuple[str, str, dict]]:
    """最长优先 + 不重叠占位，返回命中的 (term, kind, entry)。"""
    taken = [False] * len(question)
    hits: list[tuple[str, str, dict]] = []
    for term, kind, entry in index:
        start = question.find(term)
        while start != -1:
            end = start + len(term)
            if not any(taken[start:end]):
                for i in range(start, end):
                    taken[i] = True
                hits.append((term, kind, entry))
                break
            start = question.find(term, start + 1)
    return hits


# ---- 各维度解析 ----

def _resolve_time(question: str) -> dict:
    """时间窗 =（区间, 粒度）。

    「区间」与「粒度」是两个独立概念：「近 90 天」是区间，「周趋势」是粒度。
    分开标注的原因是 Skill 重放——代码里的窗口与粒度都是写死的，
    「近 90 天的周趋势」与「近 30 天的日趋势」不能互相重放。
    """
    result = _resolve_range(question)
    grain_word = _match_grain_word(question)
    if result:
        result["grain"] = grain_word or result.get("grain")
        return result
    if grain_word:
        return {"kind": "grain", "value": None, "unit": grain_word,
                "text": grain_word, "grain": grain_word}
    return {}


def _match_grain_word(question: str) -> str | None:
    for grain, words in _GRAIN_WORDS:
        if any(w in question for w in words):
            return grain
    return None


def _resolve_range(question: str) -> dict:
    for pattern, unit in _RANGE_PATTERNS:
        m = pattern.search(question)
        if m:
            return {"kind": "recent", "value": int(m.group(1)), "unit": unit,
                    "text": m.group(0), "grain": unit}
    for word, unit in _NAMED_RANGES:
        if word in question:
            return {"kind": "named", "value": word, "unit": unit, "text": word, "grain": unit}
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", question)
    if m:
        value = f"{m.group(1)}-{int(m.group(2)):02d}"
        return {"kind": "month", "value": value, "unit": "month",
                "text": m.group(0), "grain": "month"}
    m = re.search(r"(20\d{2})\s*年", question)
    if m:
        return {"kind": "year", "value": m.group(1), "unit": "year",
                "text": m.group(0), "grain": "year"}
    return {}


def _resolve_comparison(question: str) -> str:
    if any(w in question for w in _YOY_WORDS):
        return "yoy"
    if any(w in question for w in _MOM_WORDS):
        return "mom"
    return "none"


def _resolve_ranking(question: str) -> dict | None:
    top_n = None
    m = re.search(r"(?:top|前)\s*(\d+)", question, re.IGNORECASE)
    if m:
        top_n = int(m.group(1))
    else:
        # 「销售额最高的 5 家门店」这类中文 T+数字 表达：最高/最低 与数字之间允许夹字，
        # 但夹的字数要短，避免把「最高的门店，共 12 家」误判成 Top12。
        m2 = re.search(r"最[高低多少大小好坏长短快慢][^\d]{0,4}(\d+)", question)
        if m2:
            top_n = int(m2.group(1))
        else:
            m3 = re.search(r"前([一二两三四五六七八九十]+)", question)
            if m3:
                top_n = _CN_NUM.get(m3.group(1)[0], None)
    lowered = question.lower()
    if any(w in question for w in _RANK_DESC_WORDS) or "top" in lowered:
        order = "desc"
    elif any(w in question for w in _RANK_ASC_WORDS):
        order = "asc"
    else:
        order = None
    if top_n is None and order is None:
        return None
    return {"top_n": top_n, "order": order}


def _infer_analysis_type(metrics, dimensions, time_info, comparison, ranking, question) -> str:
    if comparison in ("yoy", "mom"):
        return f"{comparison}_comparison"
    if ranking:
        return "ranking"
    if time_info and any(w in question for w in _TREND_WORDS):
        return "trend"
    if dimensions:
        return "breakdown"
    if metrics:
        return "aggregate"
    return "unknown"


def _confidence(metrics, dimensions, time_info, comparison, ranking) -> float:
    """启发式置信度：命中越完整越高（非概率，仅供排序与阈值判断）。"""
    score = 0.0
    if metrics:
        score += 0.45
    if dimensions:
        score += 0.25
    if time_info:
        score += 0.15
    if comparison != "none":
        score += 0.10
    if ranking:
        score += 0.05
    return round(min(score, 1.0), 2)


# ---- 对外入口 ----

def resolve(question: str, pack_id: str) -> dict:
    """把问题解析为语义包口径下的结构化意图（确定性、零 token）。

    返回：
        pack          命中的语义包
        metrics       [{name, field, agg, unit, formula, optional, matched}]
        dimensions    [{name, field, optional, matched}]
        time          {} 或 {kind, value, unit, text, grain}
        comparison    none | yoy | mom
        ranking       null 或 {top_n, order}
        analysis_type aggregate | breakdown | trend | ranking | yoy_comparison | mom_comparison
        matched_terms 命中的原始词面（用于解释与调试）
        confidence    启发式置信度 0~1
    """
    pack = load_pack(pack_id)
    empty = {
        "pack": pack_id, "metrics": [], "dimensions": [], "time": {},
        "comparison": "none", "ranking": None, "analysis_type": "unknown",
        "matched_terms": [], "confidence": 0.0,
    }
    if not pack or not question:
        return empty

    hits = _match_terms(question, _term_index(pack))
    metrics: list[dict] = []
    dimensions: list[dict] = []
    terms: list[str] = []
    seen_metrics: set[str] = set()
    seen_dims: set[str] = set()

    for term, kind, entry in hits:
        terms.append(term)
        if kind == "metric" and entry["name"] not in seen_metrics:
            seen_metrics.add(entry["name"])
            metrics.append({
                "name": entry["name"], "field": entry.get("field"),
                "agg": entry.get("agg"), "unit": entry.get("unit"),
                "formula": entry.get("formula"),
                "optional": bool(entry.get("optional")), "matched": term,
            })
        elif kind == "dimension" and entry["name"] not in seen_dims:
            seen_dims.add(entry["name"])
            dimensions.append({
                "name": entry["name"], "field": entry.get("field"),
                "optional": bool(entry.get("optional")), "matched": term,
            })

    time_info = _resolve_time(question)
    comparison = _resolve_comparison(question)
    ranking = _resolve_ranking(question)
    analysis_type = _infer_analysis_type(metrics, dimensions, time_info,
                                         comparison, ranking, question)

    return {
        "pack": pack_id,
        "metrics": metrics,
        "dimensions": dimensions,
        "time": time_info,
        "comparison": comparison,
        "ranking": ranking,
        "analysis_type": analysis_type,
        "matched_terms": terms,
        "confidence": _confidence(metrics, dimensions, time_info, comparison, ranking),
    }


def resolve_for_packs(question: str, pack_ids: list[str]) -> dict:
    """多语义包下取置信度最高的解析结果（场景 Agent 绑定多源时用）。"""
    best: dict = {}
    for pid in pack_ids:
        result = resolve(question, pid)
        if not best or result["confidence"] > best["confidence"]:
            best = result
    return best or resolve(question, "")


__all__ = ["resolve", "resolve_for_packs"]
