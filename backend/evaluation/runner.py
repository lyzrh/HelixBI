"""评估运行器：分阶段度量 Agent 链路质量。

设计原则（重要）：

1. **能离线的阶段必须离线、可复现**——语义解析、规划契约、Skill 匹配、重放准入
   全部是确定性计算，不依赖 LLM 与 Docker，因此可以在 CI 里跑、可以设阈值门禁。
2. **依赖外部资源的阶段不得编造数字**——代码执行 / 自修复 / 端到端需要 Docker 沙箱
   （自修复还需要 LLM）。这些阶段在资源缺失时返回 `status="skipped"` 并给出原因，
   报告里显示"未采集"，而不是填一个好看的数字。
3. **一次运行一份 report**——`run_all()` 产出结构化 dict，`metrics.render_report()`
   负责渲染；CLI、测试、README 共用同一份口径。
"""

import copy
import time

from backend.evaluation import datasets as ds
from backend.evaluation.metrics import Counter, Latency, new_report, prf

# ---- 1. 语义解析（离线、确定性） ----

def eval_semantic(cases: list[dict]) -> dict:
    """比对确定性解析器输出与人工标注：指标 / 维度 / 同比环比 / 分析类型。"""
    from backend.semantic import resolve

    strict, relaxed = Counter(), Counter()
    metric_exact, dimension_exact = Counter(), Counter()
    comparison_ok, type_ok = Counter(), Counter()
    metric_sets: list[tuple[set, set]] = []
    dimension_sets: list[tuple[set, set]] = []
    latency = Latency()
    failures: list[dict] = []
    unresolved_packs: set[str] = set()
    by_ds: dict[str, Counter] = {}

    for case in cases:
        expected = case.get("expected", {})
        t0 = time.perf_counter()
        got = resolve(case["question"], case["pack"])
        latency.add((time.perf_counter() - t0) * 1000)
        if got["analysis_type"] == "unknown" and not got["metrics"] and not got["dimensions"]:
            unresolved_packs.add(case["pack"])

        pred_m = {m["name"] for m in got["metrics"]}
        pred_d = {d["name"] for d in got["dimensions"]}
        truth_m = set(expected.get("metrics", []))
        truth_d = set(expected.get("dimensions", []))

        metric_sets.append((pred_m, truth_m))
        dimension_sets.append((pred_d, truth_d))
        m_ok, d_ok = pred_m == truth_m, pred_d == truth_d
        c_ok = got["comparison"] == expected.get("comparison", "none")
        t_ok = got["analysis_type"] == expected.get("analysis_type")

        metric_exact.add(m_ok)
        dimension_exact.add(d_ok)
        comparison_ok.add(c_ok)
        type_ok.add(t_ok)
        exact_ok = m_ok and d_ok and c_ok and t_ok
        relaxed.add(m_ok and d_ok)
        strict.add(exact_ok)
        by_ds.setdefault(case.get("dataset", "-"), Counter()).add(exact_ok)
        if not (m_ok and d_ok and c_ok and t_ok):
            failures.append({
                "id": case.get("id"), "question": case["question"],
                "expected": {"metrics": sorted(truth_m), "dimensions": sorted(truth_d),
                             "comparison": expected.get("comparison", "none"),
                             "analysis_type": expected.get("analysis_type")},
                "got": {"metrics": sorted(pred_m), "dimensions": sorted(pred_d),
                        "comparison": got["comparison"],
                        "analysis_type": got["analysis_type"]},
            })

    metric_prf = _micro(metric_sets)
    dimension_prf = _micro(dimension_sets)
    return {
        "status": "ok",
        "cases": len(cases),
        "accuracy": strict.rate,
        "accuracy_hits": strict.hits,
        "relaxed_accuracy": relaxed.rate,
        "relaxed_hits": relaxed.hits,
        "metric_exact": metric_exact.as_dict(),
        "dimension_exact": dimension_exact.as_dict(),
        "comparison": comparison_ok.as_dict(),
        "analysis_type": type_ok.as_dict(),
        "metric_prf": metric_prf,
        "dimension_prf": dimension_prf,
        "latency_ms": latency.as_dict(),
        "by_dataset": {k: {"cases": v.total, "hits": v.hits, "accuracy": v.rate}
                       for k, v in sorted(by_ds.items())},
        "failures": failures,
    }


def _micro(pairs: list[tuple[set, set]]) -> dict:
    tp = fp = fn = 0
    for pred, truth in pairs:
        p = prf(pred, truth)
        tp, fp, fn = tp + p["tp"], fp + p["fp"], fn + p["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


# ---- 2. 规划上下文契约（离线） ----

def eval_planning(cases: list[dict]) -> dict:
    """口径注入质量：**漏斗完整率** + **渲染保真度**（两个不同的问题）。

    链路是「期望口径 → 确定性解析 → QuerySpec / 语义块渲染 → 进入生成 prompt」。
    只报一个数字会掩盖问题，所以拆开：

    - `coverage`（漏斗）：期望的口径里，最终有多少条**完整**进了 prompt。
      它天然 ≤ 语义解析准确率，用于回答"口径有没有丢"。
    - `fidelity`（条件）：在**已解析到**的口径里，定义（字段名 / 派生公式）渲染完整的比例。
      这是纯渲染层契约，与解析准确率无关——能独立抓住"渲染漏字段"这类 bug。

    注意：spec 由**解析器输出**构造，绝不拿期望值回填，否则该指标恒为满分。
    """
    from backend.semantic import render_semantic_prompt, render_spec_prompt, resolve
    from backend.semantic.registry import load_pack

    covered = Counter()
    fidelity = Counter()
    formula = Counter()
    failures: list[dict] = []

    for case in cases:
        expected = case.get("expected", {})
        resolved = resolve(case["question"], case["pack"])
        resolved_names = ({m["name"] for m in resolved["metrics"]}
                          | {d["name"] for d in resolved["dimensions"]})

        pack = copy.deepcopy(load_pack(case["pack"]) or {})
        # 解析到的可选指标/维度视为「本次数据里存在」，否则渲染层会按设计跳过它们
        for bucket in ("metrics", "dimensions"):
            for entry in pack.get(bucket, []):
                if entry.get("name") in resolved_names:
                    entry["_present"] = True
        entries: dict = {}
        for bucket in ("metrics", "dimensions"):
            for entry in pack.get(bucket, []):
                entries[entry.get("name")] = entry

        spec = {
            "rewritten_question": case["question"],
            "metrics": [m["name"] for m in resolved["metrics"]],
            "dimensions": [d["name"] for d in resolved["dimensions"]],
            "comparison": resolved["comparison"],
        }
        spec_block = render_spec_prompt(spec)
        sem_block = render_semantic_prompt(case["pack"], pack)

        missing: list[str] = []
        wanted = sorted(set(expected.get("metrics", [])) | set(expected.get("dimensions", [])))
        for name in wanted:
            entry = entries.get(name) or {}
            # ① 计划（QuerySpec）里必须带上这个名字；② 语义块里必须有它的定义
            complete = name in spec_block
            if entry.get("field"):
                complete = complete and (f"`{entry['field']}`" in sem_block)
            if entry.get("formula"):
                formula_ok = entry["formula"] in sem_block
                formula.add(formula_ok)
                complete = complete and formula_ok
            if name in resolved_names:
                fidelity.add(complete)
            covered.add(complete)
            if not complete:
                missing.append(name)

        if missing:
            failures.append({"id": case.get("id"), "question": case["question"],
                             "not_injected": missing})

    return {
        "status": "ok",
        "cases": len(cases),
        "coverage": covered.rate,
        "coverage_hits": covered.hits,
        "coverage_total": covered.total,
        "fidelity": fidelity.rate if fidelity.total else 0.0,
        "fidelity_hits": fidelity.hits,
        "resolved_entries": fidelity.total,
        "formula_rate": formula.as_dict(),
        "failures": failures,
    }


# ---- 3. Skill 匹配（离线，真实调用匹配器） ----

def _skill_groups(cases: list[dict]) -> dict[tuple, list[dict]]:
    """把问题按「语义包 + 指标集合 + 分析类型」聚成"同一条已验证分析路径"。"""
    groups: dict[tuple, list[dict]] = {}
    for case in cases:
        exp = case.get("expected", {})
        key = (case["pack"], tuple(sorted(exp.get("metrics", []))),
               exp.get("analysis_type", "unknown"))
        groups.setdefault(key, []).append(case)
    return groups


def eval_skills(cases: list[dict], session=None) -> dict:
    """用真实 `match_skills` 度量：匹配到的是不是同一条分析路径。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.models import Base, Skill
    from backend.skills import engine as skill_engine

    owns_session = session is None
    if owns_session:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()

    try:
        groups = _skill_groups(cases)
        key_by_skill_id: dict[int, tuple] = {}
        for key, members in groups.items():
            pack_id, metrics, analysis_type = key
            skill = Skill(
                name=f"{analysis_type} 分析路径",
                description=f"{pack_id} / {'/'.join(metrics) or '无指标'}",
                pack_id=pack_id,
                question=members[0]["question"],
                spec="{}",
                code="",
                columns_json="[]",
                tags=_tags(analysis_type, metrics),
            )
            session.add(skill)
            session.flush()
            key_by_skill_id[skill.id] = key
        session.commit()

        # 每条查询只对应「一条」正确路径，因此 precision@k 会被 k 结构性截断（≤1/k）。
        # 对 Skill Router 而言真正有意义的是：Top1 是否命中，以及正确路径是否进了候选集。
        k = 2
        top1 = Counter()
        recall_at_k = Counter()
        hits = Counter()
        for case in cases:
            exp = case.get("expected", {})
            key = (case["pack"], tuple(sorted(exp.get("metrics", []))),
                   exp.get("analysis_type", "unknown"))
            matched = skill_engine.match_skills(session, case["question"],
                                                pack_id=case["pack"], limit=k)
            hits.add(bool(matched))
            pred_keys = [key_by_skill_id.get(s.id) for s in matched if s.id in key_by_skill_id]
            top1.add(bool(pred_keys) and pred_keys[0] == key)
            recall_at_k.add(key in pred_keys)
        return {
            "status": "ok",
            "queries": len(cases),
            "groups": len(groups),
            "k": k,
            "top1_accuracy": top1.rate,
            "top1_hits": top1.hits,
            "recall": recall_at_k.rate,
            "hit_rate": hits.rate,
        }
    finally:
        if owns_session:
            session.close()


def _tags(analysis_type: str, metrics: tuple) -> str:
    import json
    return json.dumps([analysis_type, *metrics], ensure_ascii=False)


# ---- 4. 重放准入判定（离线，真实调用重放守卫） ----

def eval_replay_guard(cases: list[dict]) -> dict:
    """度量重放守卫（列结构 + 读取函数兼容）的判定正确率。

    构造两组输入：列完全匹配（应放行）、少一列（应拒绝）。
    真正的"重放成功率"需要 Docker 实际执行，见 gated 阶段。
    """
    from backend.skills.engine import _columns_match, _reader_compatible
    from backend.semantic.registry import load_pack

    correct = Counter()
    codes = {"csv": "import pandas as pd\ndf = pd.read_csv('/data/x.csv')\n",
             "xlsx": "import pandas as pd\ndf = pd.read_excel('/data/x.xlsx')\n"}
    for case in cases:
        expected = case.get("expected", {})
        pack = load_pack(case["pack"]) or {}
        all_cols = [m["field"] for m in pack.get("metrics", []) if m.get("field")]
        all_cols += [d["field"] for d in pack.get("dimensions", []) if d.get("field")]
        skill_cols = sorted(set(all_cols))
        if not skill_cols:
            continue
        full = set(skill_cols)
        minus_one = set(skill_cols[:-1])
        # 正向：列齐全 + 读取函数与挂载扩展名一致 → 应可重放
        allow = (_columns_match(skill_cols, full)
                 and _reader_compatible(codes["csv"], ["x.csv"]))
        # 反向：少一列 → 必须拒绝
        deny = not _columns_match(skill_cols, minus_one)
        # 反向：xlsx 代码配 csv 数据 → 必须拒绝
        deny_reader = not _reader_compatible(codes["xlsx"], ["x.csv"])
        correct.add(allow and deny and deny_reader)
    return {
        "status": "ok",
        "cases": correct.total,
        "eligibility_rate": correct.rate,
        "eligibility_hits": correct.hits,
    }


# ---- 5. 端到端（需 Docker + LLM，缺失则跳过） ----

def pipeline_blocker() -> str | None:
    """返回阻塞原因；None 表示可以真实跑链路。

    只检查 "docker 可执行文件存在" 是不够的——Docker Desktop 未启动时
    命令行存在但守护进程不可用，那样端到端阶段会以"看起来能跑"的姿态失败。
    因此这里真实探测守护进程。
    """
    import subprocess
    from functools import lru_cache

    from backend import config
    from backend.agent.sandbox import resolve_docker

    @lru_cache(maxsize=1)
    def _daemon_status() -> tuple[bool, str]:
        exe = resolve_docker()
        if exe is None:
            return False, "未检测到 Docker 可执行文件"
        try:
            proc = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"],
                                  capture_output=True, text=True, timeout=15)
        except Exception as exc:  # noqa: BLE001
            return False, f"Docker 调用失败：{exc}"
        if proc.returncode != 0:
            return False, "Docker 守护进程不可用（请确认 Docker Desktop 已启动）"
        return True, ""

    ok, why = _daemon_status()
    if not ok:
        return why
    if not config.OPENAI_API_KEY:
        return "未配置 LLM API Key"
    return None


def eval_pipeline(cases: list[dict], limit: int = 3) -> dict:
    """真实跑 Agent 链路：执行成功率 / 自修复成功率 / 延迟 / LLM 调用次数。"""
    reason = pipeline_blocker()
    if reason:
        return {"status": "skipped", "reason": reason}

    from backend.agent.graph import stream_analysis
    from backend.evaluation.samples import sample_files_for_pack
    from backend.semantic import render_semantic_prompt

    exec_ok = Counter()
    repaired_total, repaired_ok = 0, 0
    latency = Latency()
    llm_calls = Latency()
    errors: list[dict] = []

    for case in cases[:limit]:
        files = sample_files_for_pack(case["pack"])
        if not files:
            errors.append({"id": case.get("id"), "error": "缺少该语义包的示例数据"})
            continue
        t0 = time.perf_counter()
        nodes: list[str] = []
        merged: dict = {}
        try:
            for node, _delta, state in stream_analysis(
                case["question"], files, semantic_block=render_semantic_prompt(case["pack"])
            ):
                nodes.append(node)
                merged = state
        except Exception as exc:  # noqa: BLE001 — 单条失败不应中断评估
            errors.append({"id": case.get("id"), "error": str(exc)[:200]})
            continue

        latency.add((time.perf_counter() - t0) * 1000)
        llm_calls.add(len([n for n in nodes if n != "execute"]))
        execution = merged.get("execution", {}) or {}
        ok = bool(execution.get("ok")) and bool(
            execution.get("text") or execution.get("tables") or execution.get("charts"))
        exec_ok.add(ok)
        attempts = merged.get("attempts", 0) or 0
        if attempts > 1:
            repaired_total += 1
            repaired_ok += int(ok)
        if not ok:
            errors.append({"id": case.get("id"), "error": (execution.get("stderr") or "")[-200:]})

    return {
        "status": "ok",
        "cases": exec_ok.total,
        "rate": exec_ok.rate,
        "ok_hits": exec_ok.hits,
        "repair_cases": repaired_total,
        "repair_rate": (repaired_ok / repaired_total) if repaired_total else 0.0,
        "latency_ms": latency.as_dict(),
        "llm_calls": llm_calls.as_dict(),
        "errors": errors,
    }


# ---- 汇总 ----

def run_all(with_pipeline: bool = False, limit: int = 3) -> dict:
    """跑完整评估：离线阶段必跑，端到端按需。"""
    cases = ds.all_cases()
    report = new_report(len(cases), ds.packs())

    report["semantic"] = eval_semantic(cases)
    report["planning"] = eval_planning(cases)
    report["skills"] = eval_skills(cases)
    report["replay"] = eval_replay_guard(cases)

    if with_pipeline:
        pipeline = eval_pipeline(cases, limit=limit)
        report["end_to_end"] = pipeline
        report["execution"] = pipeline
        report["self_repair"] = (
            {"status": "ok", "rate": pipeline.get("repair_rate", 0.0),
             "cases": pipeline.get("repair_cases", 0),
             "ok_hits": int(round(pipeline.get("repair_rate", 0.0) * pipeline.get("repair_cases", 0)))}
            if pipeline.get("status") == "ok" else pipeline
        )
    else:
        blocker = pipeline_blocker()
        skipped = {"status": "skipped", "reason": blocker or "未启用（加 --with-pipeline 开启）"}
        report["execution"] = skipped
        report["self_repair"] = skipped
        report["end_to_end"] = skipped

    return report


__all__ = ["run_all", "eval_semantic", "eval_planning", "eval_skills",
           "eval_replay_guard", "eval_pipeline", "pipeline_blocker"]
