"""Skill Retrieval V2：分阶段召回 → 可解释打分 → 重放准入。

设计动机（为什么不是「把 Top1 拿来就用」）：

- V1 只做一次词面打分就取 Top1，相似问题之间无法稳定区分（同指标不同维度 /
  同维度不同指标 / 同词面不同分析类型），Top1 错的时候错误会一路传到重放；
- 重放一旦复用错代码，返回的是「看起来对、口径错」的结果，比报错更危险；
- 反过来过度保守（全部走 Agent）会把 Token / 延迟 / 成本推高。

因此链路拆成三段，每段都确定性、零 token、可单测：

    Query
      → Semantic Resolution        resolve()（复用 backend.semantic，零 token）
      → Candidate Retrieval        词面召回 + 作用域/兼容性过滤（要召回广）
      → Candidate Scoring          6 个可解释信号线性加权（配置化权重）
      → Admission Decision         High→重放 / Medium→严格校验 / Low→放弃
      → Replay OR Full Agent

两条硬规则：

1. **宁可放弃重放，也不能错误重放**——metric / dimension / analysis_type /
   数据源不兼容一律是 blocker，直接落到 Agent。
2. **不新增 LLM 调用**——所有信号来自语义解析、metadata 与历史成功率。

`legacy_*` 系列是**冻结的 V1 实现**，只为 Baseline 对比与回归留档，不要改动它们
（改了基准就不可复现了）。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from backend.models import Skill, jload

# ---------------------------------------------------------------------------
# 词法：中文 2-gram + 英文/数字词（与 V1 保持一致，保证覆盖不退化）
# ---------------------------------------------------------------------------

_TOKEN_RE_CN = re.compile(r"[\u4e00-\u9fff]+")
_TOKEN_RE_EN = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> frozenset[str]:
    """中文 2-gram + 英文/数字词。"""
    words: set[str] = set(_TOKEN_RE_EN.findall(text.lower()))
    for seg in _TOKEN_RE_CN.findall(text):
        if len(seg) == 1:
            words.add(seg)
        else:
            words.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return frozenset(words)


def _dice(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


# ---------------------------------------------------------------------------
# 解析结果缓存：语义解析是纯函数，但每条候选都要跑一次，故按 (skill, 更新时间) 缓存。
# 并发安全：写入拿锁、读取拿的是不可变快照（dict 引用交换），
# 保证多线程下不会有半个解析结果被读到。
# ---------------------------------------------------------------------------

_intent_cache: dict[tuple, "Intent"] = {}
_intent_lock = threading.Lock()
_CACHE_LIMIT = 4096


def clear_caches() -> None:
    """清空解析缓存（测试与 Skill 变更后调用）。"""
    with _intent_lock:
        _intent_cache.clear()


# ---------------------------------------------------------------------------
# 意图 / 数据上下文
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Intent:
    """语义解析出来的意图（指标 / 维度 / 类型 / 排序 / 时间窗），不依赖 LLM。"""

    metrics: frozenset[str] = frozenset()
    dimensions: frozenset[str] = frozenset()
    analysis_type: str = "unknown"
    comparison: str = "none"
    pack_id: str | None = None
    tokens: frozenset[str] = frozenset()
    confidence: float = 0.0
    # 排序方向与 TopN：「最多」与「最少」词面极像，但代码里的 sort 方向相反
    ranking_order: str | None = None
    ranking_top_n: int | None = None
    # 时间窗：代码里往往写死了窗口（如近 90 天），窗口不同不能重放
    time_kind: str | None = None
    time_value: str | None = None
    time_unit: str | None = None
    time_grain: str | None = None

    @property
    def ranking(self) -> tuple[str | None, int | None]:
        return self.ranking_order, self.ranking_top_n

    @property
    def time_window(self) -> tuple[str | None, str | None, str | None, str | None]:
        return self.time_kind, self.time_value, self.time_unit, self.time_grain

    def to_dict(self) -> dict:
        return {
            "metrics": sorted(self.metrics),
            "dimensions": sorted(self.dimensions),
            "analysis_type": self.analysis_type,
            "comparison": self.comparison,
            "pack_id": self.pack_id,
            "confidence": self.confidence,
            "ranking": {"order": self.ranking_order, "top_n": self.ranking_top_n},
            "time": {"kind": self.time_kind, "value": self.time_value,
                     "unit": self.time_unit, "grain": self.time_grain},
        }


def intent_from_resolved(resolved: dict, tokens: frozenset[str] = frozenset()) -> Intent:
    """把 `semantic.resolve()` 的输出转成 Intent。"""
    ranking = resolved.get("ranking") or {}
    time_info = resolved.get("time") or {}
    return Intent(
        metrics=frozenset(m["name"] for m in resolved.get("metrics", [])),
        dimensions=frozenset(d["name"] for d in resolved.get("dimensions", [])),
        analysis_type=resolved.get("analysis_type", "unknown"),
        comparison=resolved.get("comparison", "none"),
        pack_id=resolved.get("pack") or None,
        tokens=tokens,
        confidence=float(resolved.get("confidence", 0.0) or 0.0),
        ranking_order=ranking.get("order"),
        ranking_top_n=ranking.get("top_n"),
        time_kind=time_info.get("kind"),
        time_value=(str(time_info["value"]) if time_info.get("value") is not None else None),
        time_unit=time_info.get("unit"),
        time_grain=time_info.get("grain"),
    )


def query_intent(question: str, pack_id: str | None = None) -> Intent:
    """解析用户问题（零 token、确定性）。pack_id 为空时用候选包推断。"""
    from backend.semantic import resolve, resolve_for_packs

    tokens = tokenize(question)
    if pack_id:
        resolved = resolve(question, pack_id)
        intent = intent_from_resolved(resolved, tokens)
        return replace(intent, pack_id=pack_id)
    resolved = resolve_for_packs(question, _pack_ids())
    return intent_from_resolved(resolved, tokens)


def _pack_ids() -> list[str]:
    from backend.semantic import list_packs as _list

    return [p["id"] for p in _list()]


def skill_intent(skill) -> Intent:
    """Skill 沉淀时那次提问的意图（同样走确定性解析）。"""
    pack_id = getattr(skill, "pack_id", None)
    key = ("skill", getattr(skill, "id", 0), getattr(skill, "updated_at", ""),
           getattr(skill, "question", ""), pack_id)
    cached = _intent_cache.get(key)
    if cached is not None:
        return cached
    tokens = tokenize(getattr(skill, "question", "") or "")
    if pack_id:
        from backend.semantic import resolve

        intent = intent_from_resolved(resolve(getattr(skill, "question", "") or "", pack_id),
                                      tokens)
    else:
        intent = Intent(tokens=tokens)
    with _intent_lock:
        if len(_intent_cache) >= _CACHE_LIMIT:
            _intent_cache.clear()
        _intent_cache[key] = intent
    return intent


@dataclass(frozen=True)
class DataContext:
    """当前请求的数据上下文：决定 Skill 能不能在**这批数据上**重放。"""

    pack_ids: frozenset[str] = frozenset()
    columns: frozenset[str] = frozenset()
    extensions: frozenset[str] = frozenset()
    source_ids: tuple[int, ...] = ()
    workspace_id: int | None = None

    @property
    def signature(self) -> str:
        """数据源指纹：语义包 + 文件类型。用于识别「数据源换了」。"""
        return f"{'|'.join(sorted(self.pack_ids)) or '-'}::{','.join(sorted(self.extensions)) or '-'}"

    def to_dict(self) -> dict:
        return {
            "pack_ids": sorted(self.pack_ids),
            "columns": sorted(self.columns),
            "extensions": sorted(self.extensions),
            "signature": self.signature,
            "workspace_id": self.workspace_id,
        }


def skill_signature(pack_id: str | None, file_name: str | None = None) -> str:
    """Skill 沉淀时的数据源指纹（与 `DataContext.signature` 严格同构）。

    只包含「语义包 + 文件类型」这类稳定特征：列结构单独存在 `columns_json` 里
    逐列判断（子集兼容是允许的），指纹用来识别「换了一批数据」这种整体变化。
    """
    ext = Path(file_name or "").suffix.lower().lstrip(".") or "_"
    return f"{pack_id or '-'}::{ext}"


# ---------------------------------------------------------------------------
# 候选与打分
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    skill_id: int
    name: str
    pack_id: str | None
    scores: dict = field(default_factory=dict)
    final_score: float = 0.0
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    # 「双方都没这个信息」的信号（如问题与 Skill 都没有维度 / 排序 / 时间窗）。
    # 这类中性 0.5 不是「匹配上了」，也不是「不匹配」——严格校验时应当跳过它们。
    absent: tuple[str, ...] = ()

    @property
    def replayable(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "pack_id": self.pack_id,
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "final_score": round(self.final_score, 4),
            "blockers": list(self.blockers),
            "absent_signals": list(self.absent),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class AdmissionDecision:
    """一次查询的检索 + 准入结论（写进 Run.trace，可回答「为什么没重放」）。"""

    decision: str  # replay | agent
    confidence: str  # high | medium | low
    reason_code: str
    reason: str = ""
    selected_skill_id: int | None = None
    final_score: float = 0.0
    margin: float = 0.0
    policy: str = "v2"
    candidates: tuple[Candidate, ...] = ()
    data_context: DataContext | None = None
    query_intent: Intent | None = None
    fallback_reason: str = ""  # 重放失败后 fallback 到 Agent 时的原因

    @property
    def replay(self) -> bool:
        return self.decision == "replay"

    @property
    def candidate_ids(self) -> list[int]:
        return [c.skill_id for c in self.candidates]

    def to_dict(self) -> dict:
        return {
            "policy": self.policy,
            "decision": self.decision,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "selected_skill_id": self.selected_skill_id,
            "final_score": round(self.final_score, 4),
            "margin": round(self.margin, 4),
            "candidate_ids": self.candidate_ids,
            "candidates": [c.to_dict() for c in self.candidates],
            "query_intent": self.query_intent.to_dict() if self.query_intent else {},
            "data_context": self.data_context.to_dict() if self.data_context else {},
            "fallback_reason": self.fallback_reason,
        }


def _set_score(a: frozenset[str], b: frozenset[str]) -> tuple[float, str]:
    """集合一致性打分：相同=1，部分重叠=0.5，完全不相交=0，缺一侧=中性 0.5。

    缺一侧给中性而不是 0：很多问题本来就不提维度（如「本月总销售额」），
    这时维度信号没有区分度，不该被当成「不匹配」扣分。
    """
    if not a and not b:
        return 0.5, "both_empty"
    if not a or not b:
        return 0.5, "one_side_empty"
    if a == b:
        return 1.0, "equal"
    if a & b:
        return 0.5, "partial"
    return 0.0, "disjoint"


def _type_score(a: str, b: str) -> float:
    if a == b and a != "unknown":
        return 1.0
    if a == "unknown" or b == "unknown":
        return 0.4  # 解析不出类型不是「类型冲突」，只是信息不足
    return 0.0


def _ranking_score(q: Intent, s: Intent) -> tuple[float, list[str], str]:
    """排序一致性：方向（最多/最少）与 TopN（Top10/Top5）。

    词面相似度对「谁的销售额最高」与「谁的销售额最低」几乎没有区分度，
    但两者代码里的 sort 方向是相反的——这正是必须显式校验的一类误匹配。
    """
    blockers: list[str] = []
    q_order, q_top = q.ranking
    s_order, s_top = s.ranking
    if q_order is None and q_top is None and s_order is None and s_top is None:
        return 0.5, blockers, "both_empty"
    if q_order is None and s_order is None:
        order_score, note = 0.5, "order_unknown"
    elif q_order is None or s_order is None:
        order_score, note = 0.5, "order_one_side"
    elif q_order == s_order:
        order_score, note = 1.0, "order_equal"
    else:
        order_score, note = 0.0, "order_conflict"
        blockers.append("ranking_direction_mismatch")

    if q_top is not None and s_top is not None and q_top != s_top:
        blockers.append("ranking_limit_mismatch")
        return 0.3, blockers, note + f"|top{s_top}!={q_top}"
    return order_score, blockers, note


def _time_score(q: Intent, s: Intent) -> tuple[float, list[str], str]:
    """时间窗一致性：代码里常写死窗口与粒度（如近 90 天 / 周趋势）。"""
    q_kind, q_val, q_unit, q_grain = q.time_window
    s_kind, s_val, s_unit, s_grain = s.time_window
    if q_kind is None and s_kind is None:
        return 0.5, [], "both_empty"
    if q_kind is None or s_kind is None:
        return 0.5, [], "one_side_empty"
    if (q_kind, q_val, q_unit, q_grain) == (s_kind, s_val, s_unit, s_grain):
        return 1.0, [], "equal"
    if q_unit == s_unit:
        # 同一粒度不同窗口（近 30 天 vs 近 90 天；本月 vs 上月）
        return 0.3, ["time_window_mismatch"], f"{s_val}{s_unit or ''}->{q_val}{q_unit or ''}"
    if q_grain == s_grain:
        return 0.3, ["time_window_mismatch"], f"range:{s_unit}/{s_val}->{q_unit}/{q_val}"
    return 0.15, ["time_window_mismatch"], f"{s_unit}->{q_unit}"


def _datasource_compat(skill, ctx: DataContext | None) -> tuple[float, list[str], list[str]]:
    """Skill 与当前数据上下文的兼容性：返回 (score, blockers, notes)。

    这里同时是**重放安全边界**：不兼容的 Skill 即使得分再高也不能重放。
    """
    blockers: list[str] = []
    notes: list[str] = []
    if ctx is None:
        return 1.0, blockers, notes

    skill_pack = getattr(skill, "pack_id", None)
    if skill_pack and ctx.pack_ids and skill_pack not in ctx.pack_ids:
        blockers.append("pack_mismatch")

    skill_cols = set(jload(getattr(skill, "columns_json", "[]"), []) or [])
    if skill_cols and ctx.columns and not skill_cols.issubset(ctx.columns):
        missing = sorted(skill_cols - ctx.columns)
        blockers.append("column_missing:" + ",".join(missing[:3]))

    if ctx.extensions:
        from backend.skills.engine import _reader_compatible

        mount = [f"x.{e}" for e in sorted(ctx.extensions)]
        if not _reader_compatible(getattr(skill, "code", "") or "", mount):
            blockers.append("reader_mismatch")

    stored = getattr(skill, "datasource_key", "") or ""
    if stored and stored != ctx.signature:
        blockers.append("datasource_changed")
        notes.append(f"stored={stored} now={ctx.signature}")

    if not (getattr(skill, "code", "") or "").strip():
        blockers.append("skill_incomplete")

    return (0.0 if blockers else 1.0), blockers, notes


def _history_score(skill) -> float:
    """历史成功率（Laplace 平滑）。没有使用记录时给中性先验 0.5。"""
    use = int(getattr(skill, "use_count", 0) or 0)
    success = int(getattr(skill, "success_count", 0) or 0)
    if use <= 0:
        return 0.5
    return (success + 1) / (use + 2)


def score_candidate(skill, intent: Intent, ctx: DataContext | None,
                    weights: dict | None = None) -> Candidate:
    """六个可解释信号 → 线性加权 final_score。"""
    from backend import config

    w = weights or config.SKILL_RETRIEVAL_WEIGHTS
    total = sum(w.values()) or 1.0

    sk_intent = skill_intent(skill)
    corpus_tokens = tokenize(
        " ".join([getattr(skill, "question", "") or "",
                  getattr(skill, "name", "") or "",
                  " ".join(jload(getattr(skill, "tags", "[]"), []) or [])]))

    # ① 语义相似度：对称相似（Dice）+ 查询被解释的比例
    symmetric = _dice(intent.tokens, corpus_tokens)
    covered = (len(intent.tokens & corpus_tokens) / len(intent.tokens)) if intent.tokens else 0.0
    semantic = 0.6 * symmetric + 0.4 * covered

    # ② 指标一致性 ③ 维度一致性
    metric, metric_note = _set_score(intent.metrics, sk_intent.metrics)
    dimension, dim_note = _set_score(intent.dimensions, sk_intent.dimensions)

    # ④ 分析类型一致性
    type_score = _type_score(intent.analysis_type, sk_intent.analysis_type)

    # ⑤ 排序方向 / TopN 一致性 ⑥ 时间窗一致性
    ranking, rank_blockers, rank_note = _ranking_score(intent, sk_intent)
    time_score, time_blockers, time_note = _time_score(intent, sk_intent)

    # ⑦ 数据源兼容性 ⑧ 历史成功率
    datasource, blockers, notes = _datasource_compat(skill, ctx)
    history = _history_score(skill)

    scores = {
        "semantic": round(semantic, 6),
        "metric": metric,
        "dimension": dimension,
        "type": type_score,
        "ranking": ranking,
        "time": time_score,
        "datasource": datasource,
        "history": round(history, 6),
    }
    final = sum(scores[k] * w.get(k, 0.0) for k in scores) / total

    # 硬约束：语义不同的分析路径不得重放（即便加权分很高）。
    # 这里的判据是「宁可放弃重放，也不能错误重放」：
    # - 完全不相交（disjoint）→ 明显的另一条分析路径；
    # - 部分重叠（partial）→ 结果表/结论覆盖的口径与用户所问不一致
    #   （例如用户还筛了地区、或多要一个指标），静默重放会给出「看起来对」的错误答案。
    if metric_note == "disjoint":
        blockers.append("metric_mismatch")
    elif metric_note == "partial":
        blockers.append("metric_partial")
    if dim_note == "disjoint":
        blockers.append("dimension_mismatch")
    elif dim_note == "partial":
        blockers.append("dimension_partial")
    if intent.analysis_type != "unknown" and sk_intent.analysis_type != "unknown" \
            and intent.analysis_type != sk_intent.analysis_type:
        blockers.append("analysis_type_mismatch")
    blockers.extend(rank_blockers)
    blockers.extend(time_blockers)

    notes.extend([f"metric:{metric_note}", f"dimension:{dim_note}",
                  f"ranking:{rank_note}", f"time:{time_note}"])
    absent = tuple(
        name for name, note in (("metric", metric_note), ("dimension", dim_note),
                                ("ranking", rank_note), ("time", time_note))
        if note == "both_empty")
    return Candidate(skill_id=getattr(skill, "id", 0), name=getattr(skill, "name", "") or "",
                     pack_id=getattr(skill, "pack_id", None), scores=scores,
                     final_score=round(final, 6), blockers=tuple(blockers),
                     notes=tuple(notes), absent=absent)


# ---------------------------------------------------------------------------
# 候选召回（要召回广，宁可多召回再排序）
# ---------------------------------------------------------------------------

def skill_scope_filter(workspace_id: int | None, user_id: int | None):
    """Skill 作用域过滤条件（SQLAlchemy 表达式，**不含** enabled）。

    三条规则，检索层 / 列表接口 / 单条读取必须完全一致：

    - `global`：全库可见；
    - `user`：**只看本人**（无论 workspace_id 是否为空）——只按 workspace_id 过滤会让
      同工作区的他人 user Skill 被看到，这里显式收紧；
    - `workspace`（含历史无归属数据）：限本工作区，`workspace_id` 为空视为全局兼容。
    """
    from sqlalchemy import and_, or_

    return or_(
        Skill.scope == "global",
        and_(Skill.scope == "user", Skill.user_id == user_id),
        and_(Skill.scope != "user", or_(Skill.workspace_id == workspace_id,
                                        Skill.workspace_id.is_(None))),
    )


def skill_visible(skill, workspace_id: int | None, user_id: int | None) -> bool:
    """单条 Skill 的可见性判断（与 `skill_scope_filter` 同一套规则）。"""
    if skill is None:
        return False
    if skill.scope == "user":
        return skill.user_id == user_id
    if skill.scope == "global":
        return True
    return skill.workspace_id in (workspace_id, None)


def visible_skills(db, workspace_id: int | None = None, user_id: int | None = None) -> list:
    """作用域过滤（含 enabled）：global 全库可见；workspace 限本工作区；user 限本人。"""
    query = db.query(Skill).filter(Skill.enabled == True)  # noqa: E712
    if workspace_id is not None:
        query = query.filter(skill_scope_filter(workspace_id, user_id))
    return query.all()


def retrieve(db, question: str, pack_id: str | None = None,
             workspace_id: int | None = None, user_id: int | None = None,
             ctx: DataContext | None = None, k: int | None = None,
             policy: str = "v2", weights: dict | None = None,
             skills: list | None = None) -> tuple[list[Candidate], Intent]:
    """召回 Top-K 候选并打分（policy="v1" 时走冻结的 V1 打分，仅用于基准对比）。

    这里**不做相关性裁剪**：召回阶段的目标是别漏（Recall），排序质量交给打分的名次。
    真正需要「低于某分数就别用」的地方有两处，各自有各自的阈值：

    - 重放准入：`SKILL_ADMISSION_LOW`（0.5），比任何召回下限都严；
    - few-shot 注入：`SKILL_RETRIEVAL_MIN_SCORE`，由 `engine.match_skills` 施用。

    在这两层加下限的好处是：召回率不因「给 prompt 做清洁」而下降。
    """
    k = k or config_top_k()
    skills = visible_skills(db, workspace_id, user_id) if skills is None else skills
    intent = query_intent(question, pack_id)

    if policy == "v1":
        ranked = legacy_rank(skills, question, pack_id, intent)
        return ranked[:max(k, 2)], intent

    out = [score_candidate(s, intent, ctx, weights) for s in skills]
    # 排序：final_score → 无 blocker 优先 → skill_id（保证确定性）
    out.sort(key=lambda c: (-c.final_score, len(c.blockers), c.skill_id))
    return out[:k], intent


def config_top_k() -> int:
    from backend import config

    return config.SKILL_RETRIEVAL_TOP_K


def admit(candidates: list[Candidate], intent: Intent, ctx: DataContext | None = None,
          policy: str = "v2") -> AdmissionDecision:
    """重放准入策略：High → 重放；Medium → 严格校验；Low → 放弃，走完整 Agent。

    两条规则：

    1. **硬约束优先于分数**：带 blocker 的候选一律不得重放；
    2. **在被否决的候选之外继续找**：Top1 不安全 ≠ 放弃重放——如果 Top2 干净
       且分数达标，就重放 Top2（对应测试场景「Top1 错误但 Top2 正确」）。
    """
    from backend import config

    if not candidates:
        return AdmissionDecision(
            decision="agent", confidence="low", reason_code="no_candidate",
            reason="库中没有可用 Skill", policy=policy, query_intent=intent,
            data_context=ctx)

    eligible = [c for c in candidates if not c.blockers]
    def _agent(code: str, reason: str, conf: str = "low",
               selected: Candidate | None = None, margin: float = 0.0) -> AdmissionDecision:
        return AdmissionDecision(
            decision="agent", confidence=conf, reason_code=code, reason=reason,
            selected_skill_id=(selected or candidates[0]).skill_id,
            final_score=(selected or candidates[0]).final_score, margin=margin,
            policy=policy, candidates=tuple(candidates), data_context=ctx,
            query_intent=intent)

    if not eligible:
        top = candidates[0]
        code = top.blockers[0].split(":")[0] if top.blockers else "unknown"
        return _agent(f"blocked:{code}", "；".join(top.blockers) or "候选不可重放", "low")

    top = eligible[0]
    runner_up = eligible[1] if len(eligible) > 1 else None
    margin = round(top.final_score - runner_up.final_score, 6) if runner_up else 1.0

    def _replay(conf: str, code: str, reason: str) -> AdmissionDecision:
        # 记录里保留**完整**候选列表（含被否决的），这样「为什么没选它」可回溯；
        # 选择与分差只在 eligible 上计算（被否决的候选不参与竞争）。
        return AdmissionDecision(
            decision="replay", confidence=conf, reason_code=code, reason=reason,
            selected_skill_id=top.skill_id, final_score=top.final_score, margin=margin,
            policy=policy, candidates=tuple(candidates), data_context=ctx,
            query_intent=intent)

    # ---- 1. High confidence：分数够高且与第二名有区分度 ----
    if top.final_score >= config.SKILL_ADMISSION_HIGH:
        if margin < config.SKILL_ADMISSION_MARGIN:
            return _verify(top, candidates, intent, ctx, policy, margin,
                           reason="候选歧义（Top1/Top2 分差过小）")
        return _replay("high", "high_confidence",
                       f"final_score={top.final_score:.3f} ≥ {config.SKILL_ADMISSION_HIGH}，"
                       f"领先第二名 {margin:.3f}")

    # ---- 2. Medium confidence：进入更严格的校验 ----
    if top.final_score >= config.SKILL_ADMISSION_LOW:
        return _verify(top, candidates, intent, ctx, policy, margin)

    # ---- 3. Low confidence：放弃重放，进入完整 Agent ----
    return _agent("low_confidence",
                  f"final_score={top.final_score:.3f} < {config.SKILL_ADMISSION_LOW}",
                  "low", selected=top, margin=margin)


def _verify(top: Candidate, candidates: list[Candidate], intent: Intent,
            ctx: DataContext | None, policy: str, margin: float,
            reason: str = "") -> AdmissionDecision:
    """Medium 档的严格校验：要求**完全一致**（不接受「一侧为空」的软匹配）。

    这一档存在的意义：分数中等往往意味着「大体像，但细节没对上」。High 档允许软匹配
    （如用户没提排序方向时的通配），Medium 档不再允许——除「双方都没这个信息」的信号
    之外，其余每个信号都必须满分。这样把「有点像」和「确认是同一条路径」明确分开。
    """
    def _agent(code: str, why: str) -> AdmissionDecision:
        return AdmissionDecision(
            decision="agent", confidence="medium", reason_code=code,
            reason=f"{reason}；{why}" if reason else why,
            selected_skill_id=top.skill_id, final_score=top.final_score, margin=margin,
            policy=policy, candidates=tuple(candidates), data_context=ctx,
            query_intent=intent)

    for key, why in (("metric", "指标集合未完全一致"),
                     ("dimension", "维度集合未完全一致"),
                     ("type", "分析类型未完全一致"),
                     ("ranking", "排序方向/数量未完全一致"),
                     ("time", "时间窗未完全一致"),
                     ("datasource", "数据源不兼容")):
        if key in top.absent:
            continue
        if top.scores.get(key, 0.0) < 1.0:
            return _agent(f"verify_failed:{key}", why)

    return AdmissionDecision(
        decision="replay", confidence="medium", reason_code="verify_passed",
        reason=f"严格校验通过（{top.final_score:.3f}）", selected_skill_id=top.skill_id,
        final_score=top.final_score, margin=margin, policy=policy,
        candidates=tuple(candidates), data_context=ctx, query_intent=intent)


def route(db, question: str, pack_id: str | None = None,
          workspace_id: int | None = None, user_id: int | None = None,
          data_source_ids: list[int] | None = None, ctx: DataContext | None = None,
          policy: str = "v2", skills: list | None = None,
          weights: dict | None = None) -> AdmissionDecision:
    """一步完成 Retrieval → Admission（对外主入口）。

    `skills`：显式传入候选池（评测与单测可脱离数据库直接跑策略）。
    """
    if ctx is None and data_source_ids:
        ctx = data_context(db, data_source_ids)
    candidates, intent = retrieve(db, question, pack_id, workspace_id, user_id,
                                  ctx=ctx, policy=policy, skills=skills,
                                  weights=weights)
    return admit(candidates, intent, ctx, policy=policy)


def data_context(db, data_source_ids: list[int]) -> DataContext:
    """从数据源集合构造 DataContext（列结构 / 文件类型 / 语义包 / 工作区）。"""
    from backend.models import DataSource

    packs: set[str] = set()
    columns: set[str] = set()
    exts: set[str] = set()
    ids: list[int] = []
    workspace_id: int | None = None
    for ds_id in data_source_ids or []:
        ds = db.get(DataSource, ds_id)
        if not ds:
            continue
        ids.append(ds.id)
        workspace_id = ds.workspace_id if workspace_id is None else workspace_id
        if ds.pack_id:
            packs.add(ds.pack_id)
        columns.update(jload(ds.columns_json, []) or [])
        if ds.type == "db":
            exts.add("parquet")
        else:
            name = ds.file_name or Path(ds.file_path or "").name
            if Path(name).suffix:
                exts.add(Path(name).suffix.lower().lstrip("."))
    return DataContext(pack_ids=frozenset(packs), columns=frozenset(columns),
                       extensions=frozenset(exts), source_ids=tuple(ids),
                       workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# 冻结的 V1 实现：仅用于 Baseline 对比与回归留档，请勿修改
# ---------------------------------------------------------------------------

def legacy_score(skill, question: str, pack_id: str | None, intent: Intent) -> int:
    """V1 原始打分：词面 2-gram 命中数 + 同包 +3 + 分析类型一致 +2。"""
    q_tokens = tokenize(question)
    corpus = " ".join([getattr(skill, "question", "") or "",
                       getattr(skill, "name", "") or "",
                       " ".join(jload(getattr(skill, "tags", "[]"), []) or [])])
    score = len(q_tokens & tokenize(corpus))
    if pack_id and getattr(skill, "pack_id", None) == pack_id:
        score += 3
    sk_type = skill_intent(skill).analysis_type
    if intent.analysis_type != "unknown" and intent.analysis_type == sk_type:
        score += 2
    return score


def legacy_rank(skills: list, question: str, pack_id: str | None,
                intent: Intent) -> list[Candidate]:
    """V1 候选排序（min_score=2 的原始阈值语义保留）。"""
    out: list[Candidate] = []
    for skill in skills:
        raw = legacy_score(skill, question, pack_id, intent)
        # V1 的 min_score=2 过滤在 Baseline 里同样生效，否则对比不公平
        if raw < 2:
            continue
        out.append(Candidate(skill_id=skill.id, name=skill.name or "",
                             pack_id=skill.pack_id, scores={"legacy": float(raw)},
                             final_score=float(raw)))
    out.sort(key=lambda c: (-c.final_score, c.skill_id))
    return out


def legacy_admit(db, question: str, pack_id: str | None = None,
                 workspace_id: int | None = None, user_id: int | None = None,
                 ctx: DataContext | None = None, skills: list | None = None
                 ) -> AdmissionDecision:
    """V1 重放准入 = `run_skill()` 当年的行为：Top1 结构守卫通过就重放。

    V1 只看「列是子集 + pandas 读取函数与文件扩展名兼容」，不校验指标 / 维度 /
    分析类型 / 排序方向 / 时间窗——这正是 Baseline 里 False Replay 的来源。
    """
    skills = visible_skills(db, workspace_id, user_id) if skills is None else skills
    intent = query_intent(question, pack_id)
    ranked = legacy_rank(skills, question, pack_id, intent)
    if not ranked:
        return AdmissionDecision(decision="agent", confidence="low",
                                 reason_code="no_candidate", policy="v1",
                                 query_intent=intent, data_context=ctx)
    top = ranked[0]
    skill = next((s for s in skills if s.id == top.skill_id), None)
    blockers: list[str] = []
    if skill is not None:
        score, blockers, _ = _datasource_compat(skill, ctx)
        top = Candidate(skill_id=top.skill_id, name=top.name, pack_id=top.pack_id,
                        scores=top.scores, final_score=top.final_score,
                        blockers=tuple(blockers))
    if blockers:
        return AdmissionDecision(decision="agent", confidence="low",
                                 reason_code="blocked:" + blockers[0].split(":")[0],
                                 selected_skill_id=top.skill_id,
                                 final_score=top.final_score, policy="v1",
                                 candidates=tuple(ranked), query_intent=intent,
                                 data_context=ctx)
    return AdmissionDecision(decision="replay", confidence="high",
                             reason_code="structure_only", selected_skill_id=top.skill_id,
                             final_score=top.final_score, policy="v1",
                             candidates=tuple(ranked), query_intent=intent,
                             data_context=ctx)


__all__ = [
    "AdmissionDecision", "Candidate", "DataContext", "Intent", "admit",
    "clear_caches", "data_context", "intent_from_resolved", "legacy_admit",
    "legacy_rank", "legacy_score", "query_intent", "retrieve", "route",
    "score_candidate", "skill_intent", "skill_signature", "tokenize",
    "visible_skills",
]
