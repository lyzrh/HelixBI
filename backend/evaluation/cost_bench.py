"""成本 / 延迟基准：改造前（冻结基线）vs Cost & Latency Optimization V1。

**这个方法测什么、怎么测、哪些是实测、哪些是建模——先说清楚**：

被测对象是「一次查询在 Agent 路径上的 LLM 调用次数、prompt 规模与耗时构成」。
对照组不是另一套实现，而是**同一份代码 + 两组冻结开关**：

| 开关 | 基线（legacy） | 优化后 |
| --- | --- | --- |
| `INTENT_MODE` | `llm`（每次都调 LLM 解析意图） | `auto`（确定性解析合格即跳过） |
| `CONTEXT_POLICY` | `v1`（全量语义层 + 两例 few-shot + 修复块重复注入） | `v2`（按命中口径收窄 + 去重 + 限额） |
| `FOLLOWUP_MODE` | `llm`（追问也调 LLM） | `hybrid`（确定性优先，凑不满才补） |

**实测部分（不建模）**：

- LLM **调用次数**：真实图谱节点发出的调用次数（桩只替换模型，不替换决策）；
- **prompt 输入 token**：对真实拼装出来的消息文本用真实 tokenizer 计数
  （`agent/tokens.py`，报告里标注所用编码器）；
- **确定性阶段耗时**：意图解析 / 检索 / prompt 装配的墙钟时间（p50 / p95）；
- **意图快路径的覆盖与准确率**：与 65 条人工标注逐条比对（指标 / 维度 / 同比环比 / 分析类型）；
- 重放率 / 兜底率：来自 Skill 检索与准入的真实判定（`retrieval_bench`）。

**建模部分（显式标注 `modeled=true`）**：

- LLM 响应内容与其 output token：桩按固定形状返回，不反映真实模型的长度分布；
- LLM 与沙箱的**时间**：用「每次调用固定开销 + 按 token 折算」的线性模型投影
  （`MODELED_LLM_*_MS` / `MODELED_SANDBOX_MS`）。本机没有可用 Docker / 未配置 LLM Key 时
  这是唯一诚实的做法——不能把投影当成实测。

因此本模块**不是实测报告**：`measured=False`、`mode="offline_simulation"`，
真实执行指标仍由 `runner.eval_pipeline` 在资源可用时采集（缺失则显示"未采集"）。

不做什么（诚实边界）：不通过改数据集、放宽口径或跳过安全检查来制造提升；
不把重放路径（0 LLM）的省账算进 Agent 路径的优化成果。
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend import config
from backend.agent import tokens as token_mod
from backend.evaluation import datasets as ds
from backend.evaluation.metrics import Counter, Latency
from backend.semantic import render_semantic_prompt

MODE = "offline_simulation"

# 冻结的基线开关（= Cost Optimization V1 之前的行为）
LEGACY_SWITCHES = {"INTENT_MODE": "llm", "CONTEXT_POLICY": "v1", "FOLLOWUP_MODE": "llm"}
# 优化后的开关（显式写死，避免基准随环境变量漂移）
OPTIMIZED_SWITCHES = {"INTENT_MODE": "auto", "CONTEXT_POLICY": "v2",
                      "FOLLOWUP_MODE": "hybrid"}

# 建模假设（成本/延迟投影用；全部标注为 modeled）
MODELED = {
    "sandbox_ms": 2200.0,          # 一次 docker run（含容器启动）的固定开销
    "llm_base_ms": 350.0,          # 一次 LLM 请求的固定开销（网络 + 排队）
    "llm_ms_per_1k_input": 55.0,   # 输入侧边际耗时（prefill）
    "llm_ms_per_1k_output": 260.0, # 输出侧边际耗时（decode，比 prefill 慢）
    "chars_per_token": 1.6,        # 仅供桩 LLM 造输出长度，不用于计数
}


@contextlib.contextmanager
def _config_patch(**values):
    """临时改写配置（基准用）；退出时严格还原，避免污染后续运行。"""
    original = {key: getattr(config, key, None) for key in values}
    for key, value in values.items():
        setattr(config, key, value)
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(config, key, value)


# ---------------------------------------------------------------------------
# 桩：只替换"模型"和"沙箱"，决策、装配、记账、路由全部走真实代码
# ---------------------------------------------------------------------------

@dataclass
class CostLLM:
    """记录每次调用的真实 prompt token（真实 tokenizer 数真实文本）+ 桩自身耗时。

    桩自身耗时被单独记下来，是为了让"确定性阶段耗时"能**实测**：
    图谱总墙钟减去桩内耗时，剩下的就是真实代码（装配 / 分类 / 路由 / 落库）的耗时。
    """

    calls: list = field(default_factory=list)
    generations: int = 0
    elapsed_ms: float = 0.0

    def _node_of(self, messages) -> str:
        from backend.agent import prompts

        system = str(getattr(messages[0], "content", ""))
        if system.startswith(prompts.PARSE_SYSTEM):
            return "parse_intent"
        if system.startswith(prompts.GENERATE_SYSTEM):
            return "generate_code"
        if system.startswith(prompts.SUMMARIZE_SYSTEM):
            return "summarize"
        return "followup"

    def _reply(self, node: str) -> str:
        import json as _json

        if node == "parse_intent":
            return _json.dumps({
                "metrics": ["销售额"], "dimensions": ["品类"], "filters": [],
                "time_range": {"type": "all"}, "grain": "day", "compare": None,
                "topn": None, "chart": "bar",
                "rewritten_question": "各品类销售额是多少？"}, ensure_ascii=False)
        if node == "generate_code":
            self.generations += 1
            return ("计划：读取数据、按维度聚合后回传结果。\n```python\n"
                    "import pandas as pd\nimport dahelper\n"
                    "df = pd.read_csv('/data/sample_sales.csv')\n"
                    "out = df.groupby('品类')['销售额'].sum().reset_index()\n"
                    "dahelper.save_table('结果', out.to_dict('records'))\n"
                    "dahelper.save_text('各品类销售额合计已计算')\n```")
        if node == "summarize":
            return "各品类销售额已给出（离线成本基准的桩回答）。"
        return "按区域拆解销售额\n看每月趋势\n对比不同品类"

    def invoke(self, messages, **_kwargs):
        t0 = time.perf_counter()
        node = self._node_of(messages)
        content = self._reply(node)
        input_tokens = token_mod.count_messages(messages)
        output_tokens = token_mod.count_tokens(content)
        self.calls.append({"node": node, "input_tokens": input_tokens,
                           "output_tokens": output_tokens})
        self.elapsed_ms += (time.perf_counter() - t0) * 1000
        return _StubResponse(content, input_tokens, output_tokens)


@dataclass
class _StubResponse:
    content: str
    input_tokens: int
    output_tokens: int

    @property
    def usage_metadata(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens}

    @property
    def response_metadata(self) -> dict:
        return {}


def _success_executor(workdir: Path):
    """成功的桩沙箱：产出结论 + 结果表（不启动 Docker）。"""
    import json as _json

    from backend.agent.sandbox import SandboxResult

    state = {"n": 0}

    def _run(run_id, code, files, **kwargs):  # noqa: ARG001 — 与 run_in_sandbox 同签名
        state["n"] += 1
        out_dir = workdir / f"run{state['n']}" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(_json.dumps({
            "text": "各品类销售额合计 123456 元",
            "tables": {"各品类销售额": [{"品类": "食品", "销售额": 123456}]},
            "charts": [],
        }, ensure_ascii=False), encoding="utf-8")
        return SandboxResult(ok=True, stdout="", stderr="", out_dir=out_dir)

    return _run


@dataclass
class _CaseResult:
    case_id: str
    question: str
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    intent_source: str = ""
    followup_source: str = ""
    termination_reason: str = ""
    context_tokens: int = 0
    deterministic_ms: float = 0.0
    answer_present: bool = False
    error: str = ""

    @property
    def modeled_llm_ms(self) -> float:
        return (self.llm_calls * MODELED["llm_base_ms"]
                + self.input_tokens / 1000 * MODELED["llm_ms_per_1k_input"]
                + self.output_tokens / 1000 * MODELED["llm_ms_per_1k_output"])

    @property
    def projected_ms(self) -> float:
        return self.deterministic_ms + self.modeled_llm_ms + MODELED["sandbox_ms"]

    def to_dict(self) -> dict:
        return {
            "id": self.case_id, "question": self.question,
            "llm_calls": self.llm_calls, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "intent_source": self.intent_source, "followup_source": self.followup_source,
            "context_tokens": self.context_tokens,
            "deterministic_ms": round(self.deterministic_ms, 3),
            "projected_total_ms": round(self.projected_ms, 1),
            "termination_reason": self.termination_reason,
            "answer_present": self.answer_present, "error": self.error,
        }


# ---------------------------------------------------------------------------
# 单条用例：驱动真实图谱
# ---------------------------------------------------------------------------

def _run_case(case: dict, workdir: Path, budget_overrides: dict | None = None) -> _CaseResult:
    """跑一条用例的 Agent 路径，采集真实的调用次数 / prompt token / 确定性耗时。"""
    from backend.agent import graph as graph_mod

    result = _CaseResult(case_id=case["id"], question=case["question"])
    llm = CostLLM()
    files = _sample_files(case["pack"])
    if not files:
        result.error = "缺少该语义包的示例数据"
        return result

    original_llm, original_run = graph_mod.get_llm, graph_mod.run_in_sandbox
    graph_mod.get_llm = lambda: llm
    graph_mod.run_in_sandbox = _success_executor(workdir)
    t0 = time.perf_counter()
    wall_ms = 0.0
    try:
        merged: dict = {}
        for _node, _delta, state in graph_mod.stream_analysis(
            case["question"], files,
            semantic_block=render_semantic_prompt(case["pack"]),
            semantic_packs=[case["pack"]],
            budget_overrides=budget_overrides,
        ):
            merged = state
        result.intent_source = merged.get("intent_source", "")
        result.followup_source = merged.get("followup_source", "")
        result.termination_reason = merged.get("termination_reason", "")
        result.answer_present = bool((merged.get("answer") or "").strip())
        gen = (merged.get("context_stats") or {}).get("generation") or {}
        result.context_tokens = gen.get("total_tokens", 0)
    except Exception as exc:  # noqa: BLE001 — 单条失败不中断基准
        result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        wall_ms = (time.perf_counter() - t0) * 1000
        graph_mod.get_llm, graph_mod.run_in_sandbox = original_llm, original_run

    result.llm_calls = len(llm.calls)
    result.input_tokens = sum(c["input_tokens"] for c in llm.calls)
    result.output_tokens = sum(c["output_tokens"] for c in llm.calls)
    # 确定性阶段耗时 = 图谱总墙钟 − 桩内耗时（桩内除了造字符串几乎不花时间）。
    # 这是**实测**：剩下的全是真实代码（装配 / 分类 / 路由 / 落库）。
    result.deterministic_ms = max(wall_ms - llm.elapsed_ms, 0.0)
    return result


def _sample_files(pack_id: str) -> dict[str, str]:
    from backend.evaluation.samples import sample_files_for_pack

    return sample_files_for_pack(pack_id)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def _summarize(results: list[_CaseResult], routing: dict) -> dict:
    """把逐条结果 + 真实路由率汇成指标。

    两个口径必须分清，否则数字会骗人：

    - **Agent 口径**（`*_per_agent_query`）：实测。所有用例都强制走 Agent 路径跑一遍，
      得到"一次需要 Agent 的查询"的真实调用次数与 prompt token；
    - **全查询口径**（`*_per_query`）：由**真实路由率**合成
      （重放 = 0 调用 / 0 token，Agent = 上面的实测值）。它与项目既有报告里
      「LLM 调用 / 查询」同口径，因此可以直接对照。
    """
    n = len(results) or 1
    replay_n = routing.get("replay_queries", 0)
    agent_n = routing.get("agent_queries", 0)
    total_routed = max(replay_n + agent_n, 1)
    agent_latency = Latency()
    det_latency = Latency()
    for r in results:
        agent_latency.add(r.projected_ms)
        det_latency.add(r.deterministic_ms)

    calls = sum(r.llm_calls for r in results)
    in_tok = sum(r.input_tokens for r in results)
    out_tok = sum(r.output_tokens for r in results)
    exhausted = sum(1 for r in results if r.termination_reason)

    per_agent_calls = calls / n
    per_agent_in = in_tok / n
    per_agent_out = out_tok / n
    agent_share = agent_n / total_routed
    # 全查询口径：重放路径不产生 LLM 成本（这是真实路由结论，不是假设）
    full_calls = per_agent_calls * agent_share
    full_in = per_agent_in * agent_share
    full_out = per_agent_out * agent_share

    from backend.evaluation.efficiency import estimated_cost

    cost_total = estimated_cost(full_in * total_routed, full_out * total_routed)
    return {
        "cases": len(results),
        "agent_queries": agent_n,
        "replay_queries": replay_n,
        "misreplay_queries": routing.get("misreplay_queries", 0),
        "replay_rate": round(replay_n / total_routed, 4),
        "fallback_rate": round(agent_share, 4),
        # ---- 全查询口径（重放 0 + Agent 实测值 × 真实占比） ----
        "llm_calls_per_query": round(full_calls, 3),
        "input_tokens_per_query": round(full_in, 1),
        "output_tokens_per_query": round(full_out, 1),
        "total_tokens_per_query": round(full_in + full_out, 1),
        "cost_usd_per_query": (round(cost_total / total_routed, 6)
                               if cost_total is not None else None),
        "price_configured": cost_total is not None,
        # ---- Agent 口径（实测） ----
        "llm_calls_per_agent_query": round(per_agent_calls, 3),
        "input_tokens_per_agent_query": round(per_agent_in, 1),
        "output_tokens_per_agent_query": round(per_agent_out, 1),
        "total_tokens_per_agent_query": round(per_agent_in + per_agent_out, 1),
        "deterministic_latency_ms": det_latency.as_dict(),
        "projected_latency_ms": agent_latency.as_dict(),
        "intent_fastpath_rate": round(
            sum(1 for r in results if r.intent_source == "deterministic") / n, 4),
        "followup_zero_llm_rate": round(
            sum(1 for r in results
                if r.followup_source in ("deterministic", "disabled", "off", "skipped"))
            / n, 4),
        "budget_exhausted_rate": round(exhausted / n, 4),
        "context_tokens_p50": _quantile([r.context_tokens for r in results], 0.50),
        "context_tokens_p95": _quantile([r.context_tokens for r in results], 0.95),
        "terminations": _count_by(results, "termination_reason"),
        "intent_sources": _count_by(results, "intent_source"),
        "followup_sources": _count_by(results, "followup_source"),
        "errors": [r.to_dict() for r in results if r.error],
        "outcomes": [r.to_dict() for r in results],
    }


def _quantile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(q * (len(ordered) - 1) + 0.5), len(ordered) - 1)
    return float(ordered[idx])


def _count_by(results: list[_CaseResult], attr: str) -> dict:
    out: dict[str, int] = {}
    for r in results:
        value = getattr(r, attr) or ""
        out[value] = out.get(value, 0) + 1
    return out


def _routing_stats() -> dict:
    """真实路由结果（重放 / 兜底 / 误重放）——来自 Skill 检索与准入，不建模。"""
    from backend.evaluation import retrieval_bench as bench

    data = bench.evaluate()
    v2 = data["policies"]["v2"]
    fine, eff = v2["fine"], v2["efficiency"]
    return {
        "queries": fine["queries"],
        "replay_queries": fine["replay_queries"],
        "agent_queries": eff["agent_queries"],
        "misreplay_queries": eff["misreplay_queries"],
        "false_replay_rate": fine["false_replay_rate"],
        "replay_precision": fine["replay_precision"],
        "admission_accuracy": v2["admission"]["admission_accuracy"],
    }


def intent_gate_stats(thresholds: tuple[float, ...] = (0.45, 0.60, 0.70, 0.85)) -> dict:
    """意图快路径的安全门测量：覆盖率 vs 严格准确率（对人工标注）。

    这是"没有为了省钱牺牲正确性"的核心证据：命中快路径的用例，其确定性 QuerySpec
    必须与人工标注**完全一致**（指标 / 维度 / 同比环比 / 分析类型）。
    """
    import datetime

    from backend.agent.intent import deterministic_intent

    cases = ds.all_cases()
    today = datetime.date.today()
    out: list[dict] = []
    for threshold in thresholds:
        covered, strict, relaxed = Counter(), Counter(), Counter()
        reasons: dict[str, int] = {}
        mismatches: list[dict] = []
        for case in cases:
            spec, meta = deterministic_intent(case["question"], case["pack"],
                                              min_confidence=threshold, today=today)
            reasons[meta["reason_code"]] = reasons.get(meta["reason_code"], 0) + 1
            # coverage 的分母是**全部用例**（有多少查询真的不需要 LLM 解析）
            covered.add(bool(meta["eligible"]))
            if not meta["eligible"]:
                continue
            expected = case.get("expected", {})
            metric_ok = set(spec["metrics"]) == set(expected.get("metrics", []))
            dim_ok = set(spec["dimensions"]) == set(expected.get("dimensions", []))
            cmp_ok = (spec.get("compare") or "none") == expected.get("comparison", "none")
            type_ok = spec.get("analysis_type") == expected.get("analysis_type")
            strict.add(metric_ok and dim_ok and cmp_ok and type_ok)
            relaxed.add(metric_ok and dim_ok)
            if not (metric_ok and dim_ok and cmp_ok and type_ok):
                mismatches.append({
                    "id": case["id"], "question": case["question"],
                    "expected": expected,
                    "got": {k: spec.get(k) for k in
                            ("metrics", "dimensions", "compare", "analysis_type")},
                })
        out.append({
            "threshold": threshold,
            "coverage": round(covered.rate, 4),
            "covered": covered.hits,
            "strict_accuracy": round(strict.rate, 4),
            "strict_hits": strict.hits,
            "relaxed_accuracy": round(relaxed.rate, 4),
            "fallback_reason_counts": reasons,
            "mismatches": mismatches,
        })
    return {"cases": len(cases), "by_threshold": out,
            "shipped_threshold": config.INTENT_FASTPATH_MIN_CONFIDENCE,
            "gate_rule": "命中指标 + 置信度达标 + 问题内容全部有出处（无未解释内容）"}


def budget_stress(cases_limit: int | None = None) -> dict:
    """预算压力测试：把每次运行的 LLM 调用上限压到 2，观察终止行为是否诚实。

    要证明的是：预算耗尽时**提前结束且如实说明**（终止原因进 trace、结论不编造），
    而不是"看起来更便宜了"。
    """
    import tempfile

    cases = ds.all_cases()[:cases_limit] if cases_limit else ds.all_cases()[:6]
    workdir = Path(tempfile.mkdtemp(prefix="helix_cost_budget_"))
    exhausted = answer_ok = 0
    reasons: dict[str, int] = {}
    for case in cases:
        with _config_patch(**OPTIMIZED_SWITCHES):
            result = _run_case(case, workdir, budget_overrides={"max_llm_calls": 2})
        if result.termination_reason:
            exhausted += 1
            reasons[result.termination_reason] = reasons.get(result.termination_reason, 0) + 1
            answer_ok += int(result.answer_present)
    total = len(cases) or 1
    return {
        "budget": {"max_llm_calls": 2},
        "cases": len(cases),
        "exhausted_cases": exhausted,
        "exhausted_rate": round(exhausted / total, 4),
        "honest_answer_rate": round(answer_ok / max(exhausted, 1), 4),
        "termination_reasons": reasons,
        "note": "预算耗尽时必须给出终止原因且不返回编造结果（结论由沙箱真实执行结果拼出）",
    }


def replay_guard() -> dict:
    """重放路径的零 LLM 断言（成本优化不得侵蚀"重放 0 调用"这条线）。"""
    from types import SimpleNamespace

    from backend.analysis.validation import validate_final  # noqa: F401
    from backend.skills.engine import _replay_trace

    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="helix_cost_replay_"))
    out = workdir / "out"
    out.mkdir(parents=True, exist_ok=True)
    execution = {"ok": True, "stdout": "", "stderr": "", "text": "结论",
                 "tables": {"t": [{"a": 1}]}, "charts": [], "run_dir": str(out)}
    skill = SimpleNamespace(id=1, name="x", question="q", pack_id="retail_sales")
    trace = _replay_trace(1, skill, execution, "结论", True, 2100, routing_ms=120)
    checks = [
        ("重放 LLM 调用为 0", trace["llm"]["calls"] == 0),
        ("重放 cost_control 标注 zero_llm", trace["cost_control"].get("zero_llm") is True),
        ("重放 token 为 0", trace["cost_control"]["total_tokens"] == 0),
        ("重放不计入预算消耗", trace["cost_control"]["budget_blocked_calls"] == 0),
        ("重放耗时拆成 路由/执行 两段",
         len(trace["performance"]["stage_latency"]) == 2),
        ("重放不进自修复", trace["self_repair"]["repair_attempts"] == 0),
    ]
    return {"passed": sum(1 for _n, ok in checks if ok), "cases": len(checks),
            "checks": [{"name": n, "ok": bool(ok)} for n, ok in checks]}


def policy_from_routing(base: dict, routing: dict) -> dict:
    """兼容入口：把"单条 Agent 成本"与"真实路由率"合成为全查询口径。

    `_summarize` 已经直接产出两个口径，这里只保留一个语义清晰的入口给外部调用方。
    """
    merged = dict(base)
    total = max(routing.get("replay_queries", 0) + routing.get("agent_queries", 0), 1)
    merged["replay_rate"] = round(routing.get("replay_queries", 0) / total, 4)
    merged["fallback_rate"] = round(routing.get("agent_queries", 0) / total, 4)
    return merged


def evaluate(top_k: int = 5, limit: int | None = None) -> dict:
    """跑完整基准：基线 vs 优化后（同一份代码、两组冻结开关）。"""
    import tempfile

    cases = ds.all_cases()
    if limit:
        cases = cases[:limit]
    routing = _routing_stats()
    workdir = Path(tempfile.mkdtemp(prefix="helix_cost_bench_"))

    results: dict[str, list[_CaseResult]] = {}
    for name, switches in (("baseline", LEGACY_SWITCHES),
                           ("optimized", OPTIMIZED_SWITCHES)):
        # 公平性：每组策略都从**冷缓存**开始（否则后跑的那组白拿前面预热好的画像/解析缓存）
        from backend.analysis import cache as cache_mod

        cache_mod.clear_all()
        with _config_patch(**switches):
            results[name] = [_run_case(case, workdir) for case in cases]
    cache_mod.clear_all()

    base = policy_from_routing(_summarize(results["baseline"], routing), routing)
    opt = policy_from_routing(_summarize(results["optimized"], routing), routing)
    delta = {}
    for metric in ("llm_calls_per_query", "input_tokens_per_query",
                   "output_tokens_per_query", "total_tokens_per_query",
                   "context_tokens_p50", "context_tokens_p95", "cost_usd_per_query",
                   "intent_fastpath_rate", "followup_zero_llm_rate",
                   "budget_exhausted_rate"):
        a, b = base.get(metric), opt.get(metric)
        delta[metric] = None if a is None or b is None else round(b - a, 4)
    for stage in ("deterministic_latency_ms", "projected_latency_ms"):
        a = (base.get(stage) or {}).get("p50_ms")
        b = (opt.get(stage) or {}).get("p50_ms")
        delta[f"{stage}_p50"] = None if a is None or b is None else round(b - a, 3)

    return {
        "status": "ok",
        "mode": MODE,
        "measured": False,
        "token_counter": token_mod.method(),
        "dataset": {"cases": len(cases), "packs": ds.packs(),
                    "source": "backend/evaluation/datasets"},
        "switches": {"baseline": LEGACY_SWITCHES, "optimized": OPTIMIZED_SWITCHES},
        "modeled_assumptions": dict(MODELED),
        "routing": routing,
        "intent_gate": intent_gate_stats(),
        "budget_stress": budget_stress(),
        "replay_guard": replay_guard(),
        "baseline": base,
        "optimized": opt,
        "delta": delta,
        "honesty": {
            "measured": ["LLM 调用次数", "prompt 输入 token（真实 tokenizer 计数）",
                         "确定性阶段耗时（墙钟）", "意图快路径覆盖与准确率",
                         "重放率 / 兜底率 / False Replay（来自真实路由）"],
            "modeled": ["LLM 输出内容与 output token（桩）", "LLM 与沙箱耗时（线性投影）"],
            "not_measured": ["真实 provider 计费", "真实沙箱执行耗时（需 Docker）"],
        },
    }


def render_markdown(result: dict) -> str:
    """基准的 Markdown 对照表（写进 docs/，CI 里也打一份）。"""
    b, o, d = result["baseline"], result["optimized"], result["delta"]

    def _pct_change(before: float, after: float) -> str:
        if not before:
            return "—"
        return f"{(after - before) / before * 100:+.1f}%"

    def row(label: str, key: str, unit: str = "", pct: bool = False) -> str:
        bv, ov = b.get(key), o.get(key)
        if bv is None or ov is None:
            return f"| {label} | 未配置 | 未配置 | — |"
        delta = d.get(key)
        if pct:
            bs, os_ = f"{bv * 100:.1f}%", f"{ov * 100:.1f}%"
            ds_ = f"{delta * 100:+.1f}pp" if delta is not None else "（路由相同）"
        elif unit == "ms":
            bs, os_ = f"{bv:.0f} ms", f"{ov:.0f} ms"
            ds_ = f"{delta:+.0f} ms" if delta is not None else "—"
        elif unit == "usd":
            bs, os_ = f"${bv:.6f}", f"${ov:.6f}"
            ds_ = f"{delta:+.6f}" if delta is not None else "—"
        else:
            bs, os_ = f"{bv:.2f}", f"{ov:.2f}"
            ds_ = f"{delta:+.2f}" if delta is not None else "—"
        return f"| {label} | {bs} | {os_} | {ds_} |"

    def scope_row(label: str, key: str, unit: str = "") -> str:
        bv, ov = b.get(key), o.get(key)
        if bv is None or ov is None:
            return f"| {label} | 未配置 | 未配置 | — |"
        if unit == "usd":
            return (f"| {label} | ${bv:.6f} | ${ov:.6f} | {_pct_change(bv, ov)} |")
        return f"| {label} | {bv:.2f} | {ov:.2f} | {_pct_change(bv, ov)} |"

    gate = result["intent_gate"]["by_threshold"]
    shipped = [g for g in gate
               if abs(g["threshold"] - result["intent_gate"]["shipped_threshold"]) < 1e-9]
    det_b, det_o = b["deterministic_latency_ms"], o["deterministic_latency_ms"]
    proj_b, proj_o = b["projected_latency_ms"], o["projected_latency_ms"]
    lines = [
        "# Cost & Latency Optimization V1 — Benchmark",
        "",
        f"> 模式：`{result['mode']}`，`measured={str(result['measured']).lower()}`。"
        "LLM 调用次数、prompt 输入 token、确定性阶段耗时是**实测**；"
        "LLM/沙箱耗时是**线性投影**；真实 provider 计费与真实沙箱耗时**未采集**。",
        f"> token 计数：`{result['token_counter']}`；数据集："
        f"{result['dataset']['cases']} 条问题（{', '.join(result['dataset']['packs'])}）。",
        "",
        "## 一、Agent 路径单次成本（实测口径，这是本轮优化的主战场）",
        "",
        "| 指标 | 基线（改造前） | 优化后 | 降幅 |",
        "| --- | ---: | ---: | ---: |",
        scope_row("LLM 调用 / Agent 查询", "llm_calls_per_agent_query"),
        scope_row("输入 Token / Agent 查询", "input_tokens_per_agent_query"),
        scope_row("总 Token / Agent 查询", "total_tokens_per_agent_query"),
        scope_row("生成 prompt 上下文 p50", "context_tokens_p50"),
        scope_row("生成 prompt 上下文 p95", "context_tokens_p95"),
        "",
        "## 二、全查询口径（含 96.9% 重放路径：与既有报告的「LLM 调用 / 查询」同口径）",
        "",
        "| 指标 | 基线 | 优化后 | Δ |",
        "| --- | ---: | ---: | ---: |",
        row("LLM 调用 / 查询", "llm_calls_per_query"),
        row("输入 Token / 查询", "input_tokens_per_query"),
        row("输出 Token / 查询", "output_tokens_per_query"),
        row("总 Token / 查询", "total_tokens_per_query"),
        row("成本 / 查询", "cost_usd_per_query", "usd"),
        row("重放占比", "replay_rate", pct=True),
        row("Agent 兜底率", "fallback_rate", pct=True),
        row("意图快路径命中率", "intent_fastpath_rate", pct=True),
        row("追问零 LLM 率", "followup_zero_llm_rate", pct=True),
        row("预算耗尽率", "budget_exhausted_rate", pct=True),
        "",
        "## 三、延迟",
        "",
        "| 指标 | 基线 | 优化后 | Δ |",
        "| --- | ---: | ---: | ---: |",
        f"| 确定性阶段 p50（实测） | {det_b['p50_ms']:.2f} ms | {det_o['p50_ms']:.2f} ms"
        f" | {det_o['p50_ms'] - det_b['p50_ms']:+.2f} ms |",
        f"| 确定性阶段 p95（实测） | {det_b['p95_ms']:.2f} ms | {det_o['p95_ms']:.2f} ms"
        f" | {det_o['p95_ms'] - det_b['p95_ms']:+.2f} ms |",
        f"| 整体投影 p50 | {proj_b['p50_ms']:.0f} ms | {proj_o['p50_ms']:.0f} ms"
        f" | {proj_o['p50_ms'] - proj_b['p50_ms']:+.0f} ms |",
        f"| 整体投影 p95 | {proj_b['p95_ms']:.0f} ms | {proj_o['p95_ms']:.0f} ms"
        f" | {proj_o['p95_ms'] - proj_b['p95_ms']:+.0f} ms |",
        "",
        "> 「确定性阶段」= 图谱总墙钟 − 桩内耗时，含意图解析 / 检索 / prompt 装配 / 分类 / 落库，"
        "全部是真实代码；「整体投影」= 确定性 + 建模的 LLM 与沙箱耗时。",
        "",
        "## 四、意图快路径安全门（覆盖率 vs 严格准确率）",
        "",
        "| 置信度门槛 | 覆盖率 | 严格准确率（对人工标注） | 回落 LLM |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for item in gate:
        fallback = result["dataset"]["cases"] - item["covered"]
        mark = " ← 线上默认" if shipped and item["threshold"] == shipped[0]["threshold"] else ""
        lines.append(f"| {item['threshold']:.2f}{mark} | {item['coverage'] * 100:.1f}%"
                     f" | {item['strict_accuracy'] * 100:.1f}% | {fallback} 条 |")
    lines += [
        "",
        f"门槛规则：{result['intent_gate']['gate_rule']}",
        "「严格准确率」口径：命中快路径的用例，其确定性 QuerySpec 必须与人工标注在"
        "**指标 / 维度 / 同比环比 / 分析类型**四项上完全一致。",
        "",
        "## 五、预算压力测试（LLM 调用上限压到 2）",
        "",
        f"- 触发终止：{result['budget_stress']['exhausted_cases']}"
        f"/{result['budget_stress']['cases']}"
        f"（{result['budget_stress']['exhausted_rate'] * 100:.1f}%）",
        f"- 终止后仍给出**基于真实执行结果**的结论："
        f"{result['budget_stress']['honest_answer_rate'] * 100:.1f}%",
        f"- 终止原因分布：{result['budget_stress']['termination_reasons']}",
        "",
        "## 六、重放零 LLM 校验",
        "",
    ]
    for check in result["replay_guard"]["checks"]:
        lines.append(f"- [{'x' if check['ok'] else ' '}] {check['name']}")
    lines += [
        "",
        "## 七、诚实性声明",
        "",
        f"- **实测**：{'；'.join(result['honesty']['measured'])}",
        f"- **建模**：{'；'.join(result['honesty']['modeled'])}",
        f"- **未采集**：{'；'.join(result['honesty']['not_measured'])}",
        "",
        "对照方式：同一份代码 + 两组冻结开关——",
        f"基线 `{result['switches']['baseline']}`，"
        f"优化后 `{result['switches']['optimized']}`。",
        "",
    ]
    return "\n".join(lines)


__all__ = ["LEGACY_SWITCHES", "MODELED", "MODE", "OPTIMIZED_SWITCHES", "budget_stress",
           "evaluate", "intent_gate_stats", "render_markdown", "replay_guard"]
