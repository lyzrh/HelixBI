"""确定性意图快路径：能用零 token 解析出来的意图，就不要问 LLM。

背景（成本瓶颈）：`parse_intent` 是每轮 Agent 分析的**第一次 LLM 调用**，而项目里已经
有一个确定性解析器（`backend/semantic/resolver.py`）——它在 65 条固定问题集上的严格
准确率 90.8%，且完全离线可复现。对"各品类销售额是多少"这类问题，让 LLM 再解析一遍
是纯开销：多一次调用、多一份语义层 token、多一段等待。

本模块把"要不要跳过 LLM"做成**可解释的门**，而不是无脑信任解析器：

- 只有当解析器对整句话**每个内容词都有出处**（命中语义层术语 / 时间词 / 排序词 /
  对比词 / 趋势词 / 通用疑问词）时，才走快路径；
- 只要有解释不了的内容（如「华东地区的营收」里的「华东」——这是**取值过滤**，
  解析器只能识别「区域」这个维度、识别不了「华东」这个值），一律回落 LLM；
- 门槛（最低置信度）与开关都在 `config`，默认 `INTENT_MODE="auto"`。

这条"未覆盖即回落"的规则是本模块的核心：它挡住的正是确定性解析最容易出错的一类
——把取值过滤误当成分组维度（评测集里的 `hard_001` 就是这个反例）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from backend.semantic.resolver import resolve

# ---- 快路径开关与门槛 ----

MODE_AUTO = "auto"     # 确定性优先，不满足门条件则回落 LLM（默认）
MODE_LLM = "llm"       # 冻结的旧行为：一律调用 LLM 解析（评估基线用）

REASON_ELIGIBLE = "deterministic_high_confidence"
REASON_MODE = "intent_mode_llm"
REASON_LOW_CONFIDENCE = "confidence_below_threshold"
REASON_NO_METRIC = "no_metric_matched"
REASON_UNCOVERED = "uncovered_terms"

# 通用疑问词 / 助词 / 量词 / 祈使词：它们不带业务语义，未命中术语时不算"未覆盖"。
# 这份表是**保守**的：宁可多回落几次 LLM，也不要把不理解的内容猜成意图。
_STOPWORDS = {
    # 疑问 / 指示
    "是", "多少", "什么", "哪些", "哪个", "哪家", "哪儿", "怎么", "如何", "为什么",
    "是否", "有没有", "几", "多", "哪", "个", "家", "条", "只", "次", "种",
    # 结构助词 / 连词
    "的", "了", "和", "与", "及", "或", "以及", "跟", "同", "把", "被", "对", "按",
    "从", "到", "在", "给", "让", "为", "中", "上", "下", "里", "内", "外", "并",
    "呢", "吗", "啊", "吧", "请", "吧", "这", "那", "其", "该", "本", "各", "每",
    # 祈使 / 动作（不带口径）
    "帮我", "帮忙", "看", "看看", "查", "查询", "查一下", "分析", "分析下", "统计",
    "计算", "算", "算一下", "列出", "列一列", "给出", "显示", "展示", "输出", "生成",
    "整理", "总结", "汇总", "拆解", "拆分", "拆", "分别", "一下", "下", "来", "去",
    # 元数据 / 量纲（口径变量已在语义层术语里）
    "数据", "情况", "表现", "结果", "报告", "明细", "详情", "样子", "时候", "期间",
    "范围", "对比", "比较", "相比", "差值", "差距", "占比", "比例", "结构", "构成",
    "分布", "排名", "排行", "排序", "顺序", "清单", "列表", "表格", "图", "图表",
    "总计", "合计", "总体", "整体", "一共", "总共", "总量", "平均", "均值", "口径",
}

_NUM_RE = re.compile(r"\d+")
_ASCII_RE = re.compile(r"[A-Za-z]{2,}")


def _spans(text: str, phrase: str) -> list[tuple[int, int]]:
    out, start = [], text.find(phrase)
    while start != -1:
        out.append((start, start + len(phrase)))
        start = text.find(phrase, start + 1)
    return out


def _covered_mask(question: str, phrases: list[str]) -> list[bool]:
    """把命中短语占的位置标记为"已解释"（长词优先、不重叠）。"""
    mask = [False] * len(question)
    for phrase in sorted({p for p in phrases if p}, key=len, reverse=True):
        for start, end in _spans(question, phrase):
            if not any(mask[start:end]):
                for i in range(start, end):
                    mask[i] = True
                break
    return mask


def uncovered_terms(question: str, phrases: list[str]) -> list[str]:
    """返回问题里**解释不了**的内容词（长度 ≥2 的连续片段）。

    单字、数字与纯符号不计：它们是量词/助词/计数，不构成业务语义。两字及以上的
    中文片段与 2 位以上的英文单词必须找到出处，否则这条问题不能走快路径。
    """
    mask = _covered_mask(question, phrases)
    leftovers: list[str] = []
    buf = ""
    for i, ch in enumerate(question):
        if mask[i]:
            if buf:
                leftovers.append(buf)
                buf = ""
            continue
        if ch.isspace() or ch in "，。！？、；：,.!?;:()（）[]【】-—~～'\"“”‘’/\\|+*%=<>@#$^&":
            if buf:
                leftovers.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        leftovers.append(buf)

    out: list[str] = []
    for chunk in leftovers:
        remainder = chunk
        # 剔除已知停用词（长的先剔，单字也要剔——单字本就不构成业务语义）
        for word in sorted(_STOPWORDS, key=len, reverse=True):
            if word in remainder:
                remainder = remainder.replace(word, "")
        remainder = _NUM_RE.sub("", remainder)
        remainder = "".join(ch for ch in remainder if not ch.isdigit())
        # 返回**剔除停用词后的残渣**（例如「华东地区的」→「华东」）：
        # 它才是"解释不了的内容词"，写进 trace 与错误提示时更精确
        if len(remainder) >= 2 and remainder not in out:
            out.append(remainder)
    return out


# ---- 时间表达 → QuerySpec.time_range ----

def _month_bounds(year: int, month: int) -> tuple[str, str]:
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def _quarter_bounds(year: int, quarter: int) -> tuple[str, str]:
    start_month = (quarter - 1) * 3 + 1
    return _month_bounds(year, start_month)[0], _month_bounds(year, start_month + 2)[1]


def _time_range_from_resolved(time_info: dict, today: date) -> tuple[dict, str]:
    """确定性时间窗 → QuerySpec.time_range + 粒度。"""
    if not time_info:
        return {}, ""
    grain = time_info.get("grain") or ""
    kind = time_info.get("kind")
    if kind == "recent":
        value = int(time_info.get("value") or 0)
        unit = time_info.get("unit") or "day"
        days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(unit, 1) * max(value, 1)
        return {"type": "last_N_days", "n": days}, grain or unit
    if kind == "named":
        word = str(time_info.get("value") or "")
        if word in ("今天", "昨天", "前天"):
            offset = {"今天": 0, "昨天": 1, "前天": 2}[word]
            day = (today - timedelta(days=offset)).isoformat()
            return {"type": "custom", "start": day, "end": day}, grain or "day"
        if word in ("本周", "上周"):
            monday = today - timedelta(days=today.weekday())
            if word == "上周":
                monday -= timedelta(days=7)
            end = monday + timedelta(days=6) if word == "上周" else today
            return {"type": "custom", "start": monday.isoformat(),
                    "end": end.isoformat()}, grain or "week"
        if word == "本月":
            return {"type": "this_month"}, grain or "month"
        if word == "上月":
            year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            start, end = _month_bounds(year, month)
            return {"type": "custom", "start": start, "end": end}, grain or "month"
        if word in ("本季度", "上季度"):
            quarter = (today.month - 1) // 3 + 1
            year = today.year
            if word == "上季度":
                quarter -= 1
                if quarter == 0:
                    quarter, year = 4, year - 1
            start, end = _quarter_bounds(year, quarter)
            if word == "本季度":
                end = today.isoformat()
            return {"type": "custom", "start": start, "end": end}, grain or "quarter"
        if word == "今年":
            return {"type": "custom", "start": f"{today.year}-01-01",
                    "end": today.isoformat()}, grain or "year"
        if word == "去年":
            year = today.year - 1
            return {"type": "custom", "start": f"{year}-01-01",
                    "end": f"{year}-12-31"}, grain or "year"
        return {}, grain
    if kind == "month":
        year, month = str(time_info.get("value") or "").split("-")
        start, end = _month_bounds(int(year), int(month))
        return {"type": "custom", "start": start, "end": end}, grain or "month"
    if kind == "year":
        year = int(time_info.get("value") or 0)
        return {"type": "custom", "start": f"{year}-01-01",
                "end": f"{year}-12-31"}, grain or "year"
    return {}, grain


def spec_from_resolved(resolved: dict, question: str, today: date | None = None) -> dict:
    """确定性解析结果 → QuerySpec（与 LLM 产出的字段形状一致 + 两个显式补充字段）。

    补充 `ranking` 与 `time_phrase`：LLM 版本的 QuerySpec 用 `rewritten_question`
    承载排序/时间措辞，确定性版本没有改写环节，因此把「用户原话里的时间短语」与
    「排序方向 + TopN」显式给出，避免生成代码时丢掉升/降序这类关键约束。
    """
    today = today or date.today()
    time_range, grain = _time_range_from_resolved(resolved.get("time") or {}, today)
    ranking = resolved.get("ranking") or None
    comparison = resolved.get("comparison") or "none"
    spec = {
        "metrics": [m["name"] for m in resolved.get("metrics") or []],
        "dimensions": [d["name"] for d in resolved.get("dimensions") or []],
        "filters": [],
        "time_range": time_range,
        "grain": grain or None,
        "compare": None if comparison == "none" else comparison,
        "topn": int(ranking["top_n"]) if ranking and ranking.get("top_n") else None,
        "chart": "auto",
        "rewritten_question": question,
        "analysis_type": resolved.get("analysis_type", "unknown"),
        "ranking": ({"top_n": ranking.get("top_n"), "order": ranking.get("order")}
                    if ranking else None),
        "time_phrase": (resolved.get("time") or {}).get("text", ""),
        "source": "deterministic",
    }
    return spec


def covered_phrases(resolved: dict) -> list[str]:
    """解析器"有出处"的全部词面（术语 + 时间 + 排序 + 对比 + 趋势 + 粒度）。"""
    phrases = list(resolved.get("matched_terms") or [])
    phrases += list(resolved.get("covered_spans") or [])
    for bucket in ("metrics", "dimensions"):
        for entry in resolved.get(bucket) or []:
            if entry.get("field"):
                phrases.append(str(entry["field"]))
    return phrases


def deterministic_intent(question: str, pack_id: str, *,
                         min_confidence: float | None = None,
                         mode: str | None = None,
                         today: date | None = None) -> tuple[dict, dict]:
    """尝试用确定性解析生成 QuerySpec。

    返回 `(spec, meta)`；`meta.eligible=False` 时 `spec` 为 `{}`，调用方应回落 LLM。
    meta 里的每个字段都会进 `Run.trace`，所以"为什么用了/没用快路径"可回溯。
    """
    from backend import config

    mode = (mode or getattr(config, "INTENT_MODE", MODE_AUTO) or MODE_AUTO).lower()
    threshold = (min_confidence if min_confidence is not None
                 else float(getattr(config, "INTENT_FASTPATH_MIN_CONFIDENCE", 0.70)))

    resolved = resolve(question or "", pack_id)
    confidence = float(resolved.get("confidence") or 0.0)
    meta: dict = {
        "eligible": False,
        "source": "llm",
        "reason_code": REASON_MODE,
        "confidence": confidence,
        "threshold": threshold,
        "resolved_metrics": [m["name"] for m in resolved.get("metrics") or []],
        "resolved_dimensions": [d["name"] for d in resolved.get("dimensions") or []],
        "analysis_type": resolved.get("analysis_type", "unknown"),
        "uncovered": [],
    }
    if mode == MODE_LLM:
        meta["reason"] = "INTENT_MODE=llm：保留 LLM 解析（基线行为）"
        return {}, meta

    if not resolved.get("metrics"):
        meta["reason_code"] = REASON_NO_METRIC
        meta["reason"] = "未命中任何语义层指标，无法确定性解析"
        return {}, meta
    if confidence < threshold:
        meta["reason_code"] = REASON_LOW_CONFIDENCE
        meta["reason"] = f"解析置信度 {confidence} < 门槛 {threshold}"
        return {}, meta

    uncovered = uncovered_terms(question, covered_phrases(resolved))
    if uncovered:
        meta["reason_code"] = REASON_UNCOVERED
        meta["uncovered"] = uncovered
        meta["reason"] = ("问题中存在解析器无法解释的内容（可能是取值过滤 / 新维度）："
                          + "、".join(uncovered[:5]))
        return {}, meta

    spec = spec_from_resolved(resolved, question, today=today)
    meta.update({"eligible": True, "source": "deterministic",
                 "reason_code": REASON_ELIGIBLE,
                 "reason": f"确定性解析置信度 {confidence} ≥ {threshold}，"
                           f"且问题内容全部有出处（0 次 LLM 解析）"})
    return spec, meta


__all__ = [
    "MODE_AUTO", "MODE_LLM", "REASON_ELIGIBLE", "REASON_LOW_CONFIDENCE",
    "REASON_MODE", "REASON_NO_METRIC", "REASON_UNCOVERED", "covered_phrases",
    "deterministic_intent", "spec_from_resolved", "uncovered_terms",
]
