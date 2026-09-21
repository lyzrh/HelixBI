"""Skill Retrieval / Replay Admission Benchmark：V1（Baseline）vs V2 的同一把尺子。

为什么需要这个模块：现有 `eval_skills` 只能回答「召回得对不对」，而本次要回答的是
**「该不该重放、重放错了没有、省了多少 LLM 调用」**。所以这里做四件事：

1. **两种口径的路径库**
   - `coarse`：`(语义包, 指标集合, 分析类型)` —— 与既有 CI 门禁同口径，用于衔接历史数字；
   - `fine`：再加 `(维度集合, 排序方向/TopN, 时间窗)` —— **一条可复用分析路径的完整签名**。
     重放正确性必须按 fine 口径度量：维度/排序方向/时间窗不同，代码就不能互用。
2. **同一条用例、同一套候选池，跑两条策略**：`policy="v1"`（冻结的 V1 打分 + 仅结构守卫）
   与 `policy="v2"`（六/八路信号打分 + 分层准入）。基线定义写在
   `retrieval.legacy_admit`，与当年的 `run_skill()` 行为一致。
3. **对抗准入样例**（`datasets/admission/*.json`）：负样本明确，才能算 False Replay。
4. **效率**：把路由结论交给 `efficiency.estimate()` 折算 LLM 调用 / token / 延迟 / 成本。

口径与不变量：
- 只度量，不改判定；指标计算方式固定，V1/V2 完全一致的算法；
- 沙箱相关量测不到就标「未采集」，不编造；
- 结果是纯字典，可 JSON 落盘、可进 CI 门禁、可渲染 Markdown。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from backend.evaluation import efficiency
from backend.evaluation.datasets import admission_cases, all_cases
from backend.evaluation.metrics import Latency
from backend.semantic.registry import load_pack
from backend.skills import retrieval

DEFAULT_CODE = "import pandas as pd\n\ndf = pd.read_csv('/data/data.csv')\n"
ADMISSION_DATASET = "admission_cases"


@dataclass
class StubSkill:
    """脱离数据库的 Skill 替身（评测需要确定性，不能依赖运行期数据）。"""

    id: int
    name: str = ""
    question: str = ""
    pack_id: str | None = None
    tags: str = "[]"
    columns_json: str = "[]"
    code: str = DEFAULT_CODE
    use_count: int = 0
    success_count: int = 0
    datasource_key: str | None = None
    enabled: bool = True
    scope: str = "workspace"
    workspace_id: int | None = None
    user_id: int | None = None
    updated_at: str = "2026-01-01 00:00:00"


@dataclass
class BenchOutcome:
    """单条查询的路由结果（度量与效率估算的原子单位）。"""

    case_id: str
    dataset: str
    kind: str = ""
    truth_key: tuple | None = None
    selected_key: tuple | None = None
    top1_key: tuple | None = None
    candidate_keys: tuple = ()
    decision: str = "agent"
    reason_code: str = ""
    confidence: str = ""
    score: float = 0.0
    hit: bool = False
    top1_correct: bool = False
    recall_at_2: bool = False
    recall_at_k: bool = False
    selected_correct: bool = False
    expect_decision: str = "replay"
    expect_reason: str = ""
    reason_match: bool = True
    replay_failed: bool = False
    latency_ms: float = 0.0
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 路径库构建
# ---------------------------------------------------------------------------

def coarse_key(case: dict) -> tuple:
    exp = case.get("expected", {})
    return (case["pack"], tuple(sorted(exp.get("metrics", []))),
            exp.get("analysis_type", "unknown"))


def fine_key(case: dict, intent) -> tuple:
    """完整路径签名：语义包 + 指标 + 维度 + 分析类型 + 排序 + 时间窗。"""
    exp = case.get("expected", {})
    return (case["pack"], tuple(sorted(exp.get("metrics", []))),
            tuple(sorted(exp.get("dimensions", []))),
            exp.get("analysis_type", "unknown"), intent.ranking, intent.time_window)


def _fields_for(pack_id: str, metrics, dimensions) -> list[str]:
    pack = load_pack(pack_id) or {}
    fields: list[str] = []
    wanted = set(metrics) | set(dimensions)
    for bucket in ("metrics", "dimensions"):
        for entry in pack.get(bucket, []):
            if entry.get("name") in wanted and entry.get("field"):
                fields.append(entry["field"])
    return sorted(set(fields))


def pack_context(pack_id: str) -> retrieval.DataContext:
    """「数据就在手边」的上下文：该语义包的全部字段 + csv。"""
    pack = load_pack(pack_id) or {}
    columns = set()
    for bucket in ("metrics", "dimensions"):
        columns.update(e["field"] for e in pack.get(bucket, []) if e.get("field"))
    if pack.get("time_field"):
        columns.add(pack["time_field"])
    return retrieval.DataContext(pack_ids=frozenset({pack_id}),
                                 columns=frozenset(columns),
                                 extensions=frozenset({"csv"}))


def _skill_from_key(pack_id: str, key: tuple, question: str, members: int,
                    key_kind: str, next_id: int) -> tuple[StubSkill, tuple]:
    """把一条路径签名变成一个 Skill 替身，返回 (skill, 归属的 key)。"""
    if key_kind == "coarse":
        metrics, analysis_type = key[1], key[2]
        dimensions: tuple = ()
        own_key = key
    else:
        metrics, dimensions, analysis_type = key[1], key[2], key[3]
        own_key = key
    tags = json.dumps([analysis_type, *metrics, *dimensions], ensure_ascii=False)
    skill = StubSkill(
        id=next_id, name=f"{analysis_type} · {'/'.join(metrics) or '无指定指标'}",
        question=question, pack_id=pack_id, tags=tags,
        columns_json=json.dumps(_fields_for(pack_id, metrics, dimensions)),
        use_count=max(members, 1), success_count=max(members, 1),
        datasource_key=f"{pack_id}::csv",
    )
    return skill, own_key


def build_library(cases: list[dict], key_kind: str = "fine"):
    """按路径签名聚合成 Skill 库；返回 (skills, key_by_skill_id)。"""
    groups: dict[tuple, list[dict]] = {}
    for case in cases:
        intent = retrieval.query_intent(case["question"], case.get("pack"))
        key = coarse_key(case) if key_kind == "coarse" else fine_key(case, intent)
        groups.setdefault(key, []).append(case)

    skills: list[StubSkill] = []
    key_by_id: dict[int, tuple] = {}
    for i, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0]))):
        skill, own_key = _skill_from_key(key[0], key, members[0]["question"],
                                         len(members), key_kind, i + 1)
        skills.append(skill)
        key_by_id[skill.id] = own_key
    return skills, key_by_id


# ---------------------------------------------------------------------------
# 跑策略
# ---------------------------------------------------------------------------

def run_library_case(case: dict, skills: list, key_by_id: dict, policy: str,
                     key_kind: str, top_k: int = 5,
                     weights: dict | None = None) -> BenchOutcome:
    intent = retrieval.query_intent(case["question"], case.get("pack"))
    truth = coarse_key(case) if key_kind == "coarse" else fine_key(case, intent)
    ctx = pack_context(case["pack"])

    import time

    latency = Latency()
    t0 = time.perf_counter()
    if policy == "v1":
        decision = retrieval.legacy_admit(None, case["question"], case.get("pack"),
                                         ctx=ctx, skills=skills)
    else:
        decision = retrieval.route(None, case["question"], case.get("pack"), ctx=ctx,
                                  policy="v2", skills=skills, weights=weights)
    latency.add((time.perf_counter() - t0) * 1000)

    cand_keys = [key_by_id.get(c.skill_id) for c in decision.candidates]
    top1 = cand_keys[0] if cand_keys else None
    selected = decision.selected_skill_id
    selected_key = key_by_id.get(selected)
    return BenchOutcome(
        case_id=case["id"], dataset=case.get("dataset", "-"),
        truth_key=truth, selected_key=selected_key, top1_key=top1,
        candidate_keys=tuple(cand_keys), decision=decision.decision,
        reason_code=decision.reason_code, confidence=decision.confidence,
        score=decision.final_score,
        hit=bool(cand_keys), top1_correct=top1 == truth,
        recall_at_2=truth in cand_keys[:2], recall_at_k=truth in cand_keys[:top_k],
        selected_correct=selected_key == truth,
        # 常规数据集里「正确的可复用路径」一定在库中（库是它自己聚合出来的），
        # 且数据上下文就是它沉淀时的数据 → 期望判定必然是 replay。
        expect_decision="replay",
        latency_ms=latency.as_dict()["avg_ms"],
        detail={"candidates": decision.candidate_ids,
                "scores": [c.to_dict() for c in decision.candidates[:3]]},
    )


def run_admission_case(case: dict, policy: str, base_id: int = 10000,
                       weights: dict | None = None) -> BenchOutcome:
    """对抗样例：候选池与上下文由 case 自带，期望判定由人工标注。"""
    import time

    key_by_id: dict[int, str] = {}
    skills: list[StubSkill] = []
    for i, raw in enumerate(case.get("candidates", [])):
        skill = StubSkill(
            id=base_id + i, name=raw.get("name", ""), question=raw.get("question", ""),
            pack_id=raw.get("pack_id") or case.get("pack"),
            columns_json=json.dumps(raw.get("columns", []), ensure_ascii=False),
            code=raw.get("code") or DEFAULT_CODE,
            use_count=raw.get("use_count", 0), success_count=raw.get("success_count", 0),
            datasource_key=raw.get("datasource_key"),
        )
        skills.append(skill)
        key_by_id[skill.id] = raw.get("key", str(i))

    cfg = case.get("context", {})
    ctx = retrieval.DataContext(
        pack_ids=frozenset(cfg.get("pack_ids") or []),
        columns=frozenset(cfg.get("columns") or []),
        extensions=frozenset(cfg.get("extensions") or []),
        workspace_id=cfg.get("workspace_id"),
    )

    t0 = time.perf_counter()
    if policy == "v1":
        decision = retrieval.legacy_admit(None, case["question"], case.get("pack"),
                                          ctx=ctx, skills=skills)
    else:
        decision = retrieval.route(None, case["question"], case.get("pack"), ctx=ctx,
                                   policy="v2", skills=skills, weights=weights)
    elapsed = (time.perf_counter() - t0) * 1000

    expect = case.get("expect", {})
    expect_decision = expect.get("decision", "agent")
    expect_reason = expect.get("reason_code", "")
    expect_selected = expect.get("selected_skill")
    selected_name = key_by_id.get(decision.selected_skill_id)
    selected_ok = (expect_selected is None) or (selected_name == expect_selected)
    reason_ok = (not expect_reason) or decision.reason_code == expect_reason
    return BenchOutcome(
        case_id=case["id"], dataset=ADMISSION_DATASET, kind=case.get("kind", ""),
        truth_key=expect_selected, selected_key=selected_name,
        top1_key=key_by_id.get(decision.candidates[0].skill_id)
        if decision.candidates else None,
        candidate_keys=tuple(key_by_id.get(c.skill_id) for c in decision.candidates),
        decision=decision.decision, reason_code=decision.reason_code,
        confidence=decision.confidence, score=decision.final_score,
        hit=bool(decision.candidates), top1_correct=selected_ok,
        recall_at_2=selected_ok, recall_at_k=selected_ok, selected_correct=selected_ok,
        expect_decision=expect_decision, expect_reason=expect_reason,
        reason_match=reason_ok, latency_ms=round(elapsed, 4),
        detail={"expect_reason": expect_reason, "candidates": decision.candidate_ids,
                "scores": [c.to_dict() for c in decision.candidates[:3]]},
    )


# ---------------------------------------------------------------------------
# 指标聚合
# ---------------------------------------------------------------------------

def _rate(pairs: list[bool]) -> float:
    return sum(1 for p in pairs if p) / len(pairs) if pairs else 0.0


def summarize_library(outcomes: list[BenchOutcome], top_k: int = 5) -> dict:
    admitted = [o for o in outcomes if o.decision == "replay"]
    correct_admits = [o for o in admitted if o.selected_correct]
    wrong_admits = [o for o in admitted if not o.selected_correct]
    return {
        "queries": len(outcomes),
        "top1_accuracy": _rate([o.top1_correct for o in outcomes]),
        "recall_at_2": _rate([o.recall_at_2 for o in outcomes]),
        f"recall_at_{top_k}": _rate([o.recall_at_k for o in outcomes]),
        "hit_rate": _rate([o.hit for o in outcomes]),
        "selection_accuracy": _rate([o.selected_correct for o in outcomes]),
        "replay_queries": len(admitted),
        "replay_share": len(admitted) / len(outcomes) if outcomes else 0.0,
        "replay_precision": (len(correct_admits) / len(admitted)) if admitted else 0.0,
        "replay_recall": len(correct_admits) / len(outcomes) if outcomes else 0.0,
        "false_replay_rate": (len(wrong_admits) / len(admitted)) if admitted else 0.0,
        "false_replay_per_query": len(wrong_admits) / len(outcomes) if outcomes else 0.0,
        "agent_fallback_rate": 1 - (len(admitted) / len(outcomes) if outcomes else 0.0),
        "false_replay_cases": [
            {"id": o.case_id, "question_kind": o.dataset,
             "reason_code": o.reason_code, "score": round(o.score, 4)}
            for o in wrong_admits[:10]],
    }


def summarize_admission(outcomes: list[BenchOutcome]) -> dict:
    """对抗集：判定正确率、误放行（false accept）、误拒绝（false reject）、拒绝原因覆盖率。"""
    tp = [o for o in outcomes if o.expect_decision == "replay" and o.decision == "replay"]
    tn = [o for o in outcomes if o.expect_decision == "agent" and o.decision == "agent"]
    fp = [o for o in outcomes if o.expect_decision == "agent" and o.decision == "replay"]
    fn = [o for o in outcomes if o.expect_decision == "replay" and o.decision == "agent"]
    by_kind: dict[str, list[bool]] = {}
    for o in outcomes:
        by_kind.setdefault(o.kind or "-", []).append(
            o.decision == o.expect_decision and o.reason_match)
    return {
        "cases": len(outcomes),
        "admission_accuracy": _rate([o.decision == o.expect_decision for o in outcomes]),
        "decision_and_reason_accuracy": _rate(
            [o.decision == o.expect_decision and o.reason_match for o in outcomes]),
        "reason_accuracy": _rate([o.reason_match for o in outcomes]),
        "false_accept": len(fp),
        "false_accept_rate": len(fp) / len(outcomes) if outcomes else 0.0,
        "false_reject": len(fn),
        "false_reject_rate": len(fn) / len(outcomes) if outcomes else 0.0,
        "true_replay": len(tp), "true_agent": len(tn),
        "by_kind": {k: {"cases": len(v), "accuracy": _rate(v)}
                    for k, v in sorted(by_kind.items())},
        "failures": [
            {"id": o.case_id, "kind": o.kind, "expect": o.expect_decision,
             "got": o.decision, "expected_reason": o.expect_reason,
             "got_reason": o.reason_code, "score": round(o.score, 4),
             "selected": o.selected_key}
            for o in outcomes
            if o.decision != o.expect_decision or not o.reason_match][:10],
    }


def _efficiency_inputs(outcomes: list[BenchOutcome]) -> list[dict]:
    """效率估算的输入：对抗集里的 agent 类是「刻意构造」的，不参与效率统计（会失真）。"""
    return [{"decision": o.decision, "expected": o.expect_decision,
             "correct": o.selected_correct, "replay_failed": o.replay_failed}
            for o in outcomes if o.dataset != ADMISSION_DATASET]


def evaluate(top_k: int = 5) -> dict:
    """跑两套口径 × 两条策略 + 对抗集 + 效率，返回可 JSON 化的完整结果。"""
    cases = all_cases()
    adversarial = admission_cases()

    fine_skills, fine_map = build_library(cases, "fine")
    coarse_skills, coarse_map = build_library(cases, "coarse")

    result: dict = {
        "dataset": {"cases": len(cases), "admission_cases": len(adversarial),
                    "fine_paths": len(fine_skills), "coarse_paths": len(coarse_skills),
                    "top_k": top_k},
        "policies": {},
    }

    for policy in ("v1", "v2"):
        fine = [run_library_case(c, fine_skills, fine_map, policy, "fine", top_k)
                for c in cases]
        coarse = [run_library_case(c, coarse_skills, coarse_map, policy, "coarse", top_k)
                  for c in cases]
        adv = [run_admission_case(c, policy) for c in adversarial]
        entry = {
            "fine": summarize_library(fine, top_k),
            "coarse": summarize_library(coarse, top_k),
            "admission": summarize_admission(adv),
            "efficiency": efficiency.estimate(_efficiency_inputs(fine)),
        }
        result["policies"][policy] = entry
        result.setdefault("_outcomes", {})[policy] = {
            "fine": fine, "coarse": coarse, "admission": adv}
    return result


def public_view(result: dict) -> dict:
    """去掉明细对象（BenchOutcome），得到适合 JSON 落盘的结构。"""
    return {"dataset": result["dataset"], "policies": result["policies"]}


# ---- 权重标定（配置化权重的来源，不是拍脑袋） ----

WEIGHT_GRID = (0.02, 0.04, 0.06, 0.09, 0.13, 0.17, 0.21, 0.25, 0.29)


def _objective(summary_fine: dict, adv: dict) -> tuple:
    """先消灭误放行，再提高正确重放，最后看 Top1（字典序，避免用高召回掩盖错重放）。"""
    return (adv["admission_accuracy"], adv["decision_and_reason_accuracy"],
            -adv["false_accept"], summary_fine["replay_precision"],
            summary_fine["replay_recall"], summary_fine["top1_accuracy"])


def tune_weights(rounds: int = 2) -> dict:
    """坐标下降标定权重：每轮逐信号尝试网格值，取字典序目标最优的一组。

    说明：这是**标定工具**，不是线上逻辑。评测集只有几十条，因此最终配置取
    搜索结果附近的整数值（见 config.SKILL_RETRIEVAL_WEIGHTS 与设计文档），
    避免把权重过拟合到这几十条样例上。
    """
    from backend import config

    cases = all_cases()
    adversarial = admission_cases()
    skills, key_map = build_library(cases, "fine")

    def _eval(weights: dict) -> tuple:
        fine = [run_library_case(c, skills, key_map, "v2", "fine") for c in cases]
        adv = [run_admission_case(c, "v2") for c in adversarial]
        return _objective(summarize_library(fine), summarize_admission(adv))

    current = dict(config.SKILL_RETRIEVAL_WEIGHTS)
    best_obj = _eval(current)
    history = [{"weights": dict(current), "objective": list(best_obj)}]
    for _ in range(rounds):
        improved = False
        for signal in list(current):
            for value in WEIGHT_GRID:
                if abs(value - current[signal]) < 1e-9:
                    continue
                trial = dict(current)
                trial[signal] = value
                obj = _eval(trial)
                history.append({"signal": signal, "value": value,
                                "objective": list(obj)})
                if obj > best_obj:
                    best_obj, current, improved = obj, trial, True
        if not improved:
            break
    return {"weights": current, "objective": list(best_obj),
            "evaluations": len(history), "history": history[-20:]}


def sensitivity(base: dict | None = None, top_k: int = 5) -> list[dict]:
    """单信号敏感性：逐个把某一路权重推到高低两端，看指标怎么动。

    用途：证明「权重不是拍脑袋」——如果随便取极值指标就崩，说明该信号确实在起作用；
    如果某一路怎么调都不影响，说明它冗余，应当删掉而不是留着装样子。
    """
    from backend import config

    cases = all_cases()
    adversarial = admission_cases()
    skills, key_map = build_library(cases, "fine")
    base = dict(base or config.SKILL_RETRIEVAL_WEIGHTS)

    def _eval(weights: dict) -> dict:
        fine = [run_library_case(c, skills, key_map, "v2", "fine", top_k=top_k,
                                 weights=weights) for c in cases]
        adv = [run_admission_case(c, "v2", weights=weights) for c in adversarial]
        sf, sa = summarize_library(fine, top_k), summarize_admission(adv)
        return {"admission_accuracy": sa["admission_accuracy"],
                "false_accept": sa["false_accept"],
                "top1_accuracy": sf["top1_accuracy"],
                "replay_precision": sf["replay_precision"],
                "replay_recall": sf["replay_recall"]}

    rows = [{"signal": "(current)", "value": None, **_eval(base)}]
    for signal in base:
        for value in (0.01, 0.06, 0.17, 0.29):
            trial = dict(base)
            trial[signal] = value
            rows.append({"signal": signal, "value": value, **_eval(trial)})
    return rows


def _money(value: float | None) -> str:
    return "未配置单价" if value is None else f"${value:.6f}"


def render_markdown(result: dict) -> str:
    """Baseline(V1) vs V2 对照表（Markdown，可直接贴进 README / 交付文档）。"""
    ds = result["dataset"]
    v1, v2 = result["policies"]["v1"], result["policies"]["v2"]
    f1, f2 = v1["fine"], v2["fine"]
    a1, a2 = v1["admission"], v2["admission"]
    e1, e2 = v1["efficiency"], v2["efficiency"]
    c1, c2 = v1["coarse"], v2["coarse"]

    def row(label, a, b, fmt="{:.1%}", better="↑"):
        try:
            delta = b - a
        except TypeError:
            return f"| {label} | {a} | {b} | — |"
        if fmt == "{:.1%}":
            return f"| {label} | {fmt.format(a)} | {fmt.format(b)} | {delta * 100:+.1f}pp |"
        return f"| {label} | {fmt.format(a)} | {fmt.format(b)} | {delta:+.2f} |"

    lines = [
        "# Skill Retrieval / Replay Admission — Baseline vs V2",
        "",
        f"- 问题集：{ds['cases']} 条（零售 + 制造 + 口语化对抗）"
        f"，对抗准入样例：{ds['admission_cases']} 条",
        f"- 路径库：fine（完整签名）{ds['fine_paths']} 条 / coarse（语义包+指标+类型）"
        f"{ds['coarse_paths']} 条",
        f"- V1 = 冻结的 Baseline 策略（词面 Top1 + 仅结构守卫）；V2 = Candidate Scoring + "
        f"分层 Admission",
        "- fine 口径 = 语义包 / 指标 / 维度 / 分析类型 / 排序方向与 TopN / 时间窗",
        "",
        "## 1. 检索（coarse 口径，与既有 CI 门禁同定义）",
        "",
        "| Metric | Baseline | V2 | Delta |",
        "| --- | ---: | ---: | ---: |",
        row("Top1", c1["top1_accuracy"], c2["top1_accuracy"]),
        row("Recall@2", c1["recall_at_2"], c2["recall_at_2"]),
        row(f"Recall@{ds['top_k']}", c1[f"recall_at_{ds['top_k']}"],
            c2[f"recall_at_{ds['top_k']}"]),
        row("Hit Rate", c1["hit_rate"], c2["hit_rate"]),
        "",
        "## 2. 检索 + 重放（fine 口径：一条可复用分析路径的完整签名）",
        "",
        "| Metric | Baseline | V2 | Delta |",
        "| --- | ---: | ---: | ---: |",
        row("Top1", f1["top1_accuracy"], f2["top1_accuracy"]),
        row("Recall@2", f1["recall_at_2"], f2["recall_at_2"]),
        row("Replay Precision", f1["replay_precision"], f2["replay_precision"]),
        row("Replay Recall", f1["replay_recall"], f2["replay_recall"]),
        row("False Replay Rate", f1["false_replay_rate"], f2["false_replay_rate"]),
        f"| 错误重放条数 | {len(f1['false_replay_cases'])} | "
        f"{len(f2['false_replay_cases'])} | "
        f"{len(f2['false_replay_cases']) - len(f1['false_replay_cases']):+d} |",
        "",
        "## 3. 对抗准入（自带候选池的负样本，判定 + 原因全对才算过）",
        "",
        "| Metric | Baseline | V2 | Delta |",
        "| --- | ---: | ---: | ---: |",
        row("Admission Accuracy", a1["admission_accuracy"], a2["admission_accuracy"]),
        row("判定+原因一致性", a1["decision_and_reason_accuracy"],
            a2["decision_and_reason_accuracy"]),
        f"| 误放行 False Accept | {a1['false_accept']} | {a2['false_accept']} | "
        f"{a2['false_accept'] - a1['false_accept']:+d} |",
        f"| 误拒绝 False Reject | {a1['false_reject']} | {a2['false_reject']} | "
        f"{a2['false_reject'] - a1['false_reject']:+d} |",
        "",
        "## 4. 效率（LLM 侧：调用 / token / 延迟 / 成本）",
        "",
        f"- Agent 画像来源：`{e2['profile']['source']}`"
        f"（单次 Agent ≈ {e2['profile']['llm_calls']:.1f} 次调用 / "
        f"{e2['profile']['total_tokens']:.0f} tokens / "
        f"{e2['profile']['latency_ms']:.0f} ms）",
        f"- 延迟口径：{e2['latency_scope']}",
        "",
        "| Metric | Baseline | V2 | Delta |",
        "| --- | ---: | ---: | ---: |",
        row("重放占比", e1["replay_share"], e2["replay_share"]),
        row("Agent 兜底率", e1["agent_fallback_rate"], e2["agent_fallback_rate"]),
        row("LLM 调用 / 查询", e1["llm_calls_per_query"], e2["llm_calls_per_query"],
            fmt="{:.2f}"),
        row("└ 计入误重放后", e1["llm_calls_per_query_incl_misreplay"],
            e2["llm_calls_per_query_incl_misreplay"], fmt="{:.2f}"),
        row("平均 Token / 查询", e1["avg_tokens_per_query"], e2["avg_tokens_per_query"],
            fmt="{:.0f}"),
        row("平均 LLM 延迟 / 查询（ms）", e1["avg_latency_ms"], e2["avg_latency_ms"],
            fmt="{:.0f}"),
        f"| 估算成本 / 查询 | {_money(e1['cost_usd_per_query'])} | "
        f"{_money(e2['cost_usd_per_query'])} | — |",
        f"| 重放失败率 | {e1['replay_failure_rate']:.1%} | "
        f"{e2['replay_failure_rate']:.1%} | "
        f"{(e2['replay_failure_rate'] - e1['replay_failure_rate']) * 100:+.1f}pp |",
        "| 沙箱执行耗时 | 未采集 | 未采集 | — |",
        "",
        "> 「沙箱执行耗时」需要 Docker 实测，缺失时标注未采集而不是填估计值；",
        "> 两条策略的沙箱成本相同，因此上表的 LLM 侧差异就是策略差异。",
        "> Baseline 的 LLM 调用量更低，是因为它**错误重放**后直接返回了错结果——",
        "> 「计入误重放后」一行把这类查询还原成 Agent 成本，才是可比的口径。",
    ]
    return "\n".join(lines)
