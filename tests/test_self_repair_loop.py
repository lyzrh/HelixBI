"""Self-Repair V2 的图谱级集成测试（真实 LangGraph 路由 + 桩化的 LLM/沙箱）。

这一组用例回答的是"机制到底有没有生效"：

- 首轮成功不进修复；失败时按**错误类别**给出定向提示（而不是同一段话）；
- 复读提前终止、`MAX_FIX_ATTEMPTS` 上限被守住、环境不可用直接兜底；
- 失败轮也要有结论（不把异常抛给用户）；
- `Run.trace.self_repair` 能落库、可序列化，并且 Skill 重放与 few-shot 链路完全不受影响。
"""

import json
import tempfile
from pathlib import Path

import pytest

from backend import config
from backend.agent import repair as repair_mod
from backend.agent.acceptance import acceptance_gate
from backend.analysis import runtime
from backend.evaluation import repair_cases as cases
from backend.evaluation import repair_bench as bench
from backend.models import jdump, jload

SCENARIOS = {s["id"]: s for s in cases.scenarios()}


class RecordingLLM(bench.StubLLM):
    """桩 LLM + 记录真实组装出来的消息（用来断言"提示确实换了"）。"""

    def __init__(self):
        super().__init__()
        self.messages: list[list] = []

    def invoke(self, messages, **_kwargs):
        self.messages.append(list(messages))
        return super().invoke(messages, **_kwargs)

    @property
    def repair_prompts(self) -> list[str]:
        """所有"修复轮"的 user 消息（generate 的第 2 次及以后调用）。"""
        from backend.agent import prompts

        out = []
        for messages in self.messages:
            system = str(getattr(messages[0], "content", ""))
            if system.startswith(prompts.GENERATE_SYSTEM) and len(messages) > 2:
                out.append(str(getattr(messages[-1], "content", "")))
        return out


def _run(scenario_id: str, policy: str = "v2", skill_block: str = "") -> dict:
    """在临时库里驱动真实图谱跑一条场景，返回 {nodes, merged, trace, llm}。"""
    from backend.agent import graph as graph_mod
    from backend.analysis import runtime as runtime_mod

    scenario = SCENARIOS[scenario_id]
    with bench._isolated_db():
        llm = RecordingLLM()
        ctx: dict = {"merged": {}}
        executor = bench.ScriptedExecutor(scenario, ctx,
                                          Path(tempfile.mkdtemp(prefix="helix_loop_")))
        original_llm, original_run = graph_mod.get_llm, graph_mod.run_in_sandbox
        graph_mod.get_llm = lambda: llm
        graph_mod.run_in_sandbox = executor
        nodes: list[str] = []
        states: list[tuple[str, dict]] = []
        merged: dict = {}
        try:
            for node, _delta, state in graph_mod.stream_analysis(
                scenario["question"], bench._sample_files(),
                skill_block=skill_block, repair_policy=policy, run_id=9001,
            ):
                nodes.append(node)
                merged = state
                ctx["merged"] = state   # 桩执行器要看到"上一轮用了哪个修复策略"
                # 注意：stream_analysis yield 的是同一个可变 dict，必须快照
                states.append((node, dict(state)))
        finally:
            graph_mod.get_llm, graph_mod.run_in_sandbox = original_llm, original_run
        trace = runtime_mod.build_self_repair_trace(merged, runtime_mod._llm_stats(9001))
    return {"nodes": nodes, "merged": merged, "trace": trace, "llm": llm,
            "scenario": scenario, "states": states}


# ---- 首轮成功：不进修复 ----

def test_first_pass_success_does_not_enter_repair():
    result = _run("sr01_first_pass_success")
    trace = result["trace"]
    assert result["nodes"].count("generate_code") == 1
    assert result["nodes"].count("classify") == 1
    assert trace["outcome"] == "success"
    assert trace["first_pass_success"] is True
    assert trace["repair_attempts"] == 0
    assert trace["attempts"] == []
    assert result["merged"]["previous_errors"] == []
    assert result["llm"].repair_prompts == []


# ---- 定向修复：提示不同、结果可解释 ----

@pytest.mark.parametrize("scenario_id,strategy,keyword", [
    ("sr02_syntax_fixed", "code_structure", "代码结构"),
    ("sr03_column_fixed", "schema_alignment", "列名"),
    ("sr04_type_fixed", "dtype_conversion", "dtype"),
    ("sr05_empty_fixed", "filter_and_scope", "过滤条件"),
    ("sr06_timeout_fixed", "bounded_compute", "超时"),
    ("sr07_resource_limit_fixed", "memory_bound", "内存"),
    ("sr11_acceptance_failed_code_ok", "filter_and_scope", "过滤条件"),
])
def test_targeted_repair_prompt_and_outcome(scenario_id, strategy, keyword):
    result = _run(scenario_id)
    trace = result["trace"]
    prompts = result["llm"].repair_prompts
    assert len(prompts) == 1, f"应当恰好修复一次，实际 {len(prompts)}"
    assert keyword in prompts[0]
    assert "历史失败" in prompts[0], "修复提示里必须带上「上次错在哪」"
    assert trace["repair_strategy"] == strategy
    assert trace["outcome"] == "success"
    assert trace["first_pass_success"] is False
    assert trace["repair_attempts"] == 1
    assert trace["error_category"] == SCENARIOS[scenario_id]["expect"]["error_category"]


def test_repair_prompts_differ_between_error_categories():
    """V1 的病灶就是"所有错误拿到同一段提示"，V2 必须换掉它。"""
    syntax = _run("sr02_syntax_fixed")["llm"].repair_prompts[0]
    column = _run("sr03_column_fixed")["llm"].repair_prompts[0]
    assert syntax != column
    assert "代码结构" in syntax and "代码结构" not in column
    assert "列名映射" in column and "列名映射" not in syntax


def test_repair_prompt_carries_previous_error_chain():
    """多轮修复时，提示里要能看到"前面几轮修了什么、变成什么错"。"""
    result = _run("sr09_multi_error_then_success")
    prompts = result["llm"].repair_prompts
    assert len(prompts) == 3
    assert "语法错误" in prompts[1]
    assert "列不存在" in prompts[2]


# ---- 复读与上限 ----

def test_repeated_identical_error_stops_early():
    result = _run("sr08_repeated_identical")
    trace = result["trace"]
    assert trace["outcome"] == "exhausted"
    assert trace["repeated_error"] is True
    assert trace["repeat_kind"] == "identical"
    assert trace["repair_attempts"] == 1, "复读必须提前终止，不能烧完 3 次额度"
    assert result["merged"]["attempts"] == 2
    assert trace["repair_status"] == repair_mod.STATUS_REPEATED
    assert "复读" in result["merged"]["repair_reason"]


def test_max_fix_attempts_is_enforced():
    result = _run("sr10_max_attempts_exhausted")
    trace = result["trace"]
    assert result["merged"]["attempts"] == 1 + config.MAX_FIX_ATTEMPTS
    assert trace["repair_attempts"] == config.MAX_FIX_ATTEMPTS
    assert trace["outcome"] == "exhausted"
    assert trace["max_fix_attempts"] == config.MAX_FIX_ATTEMPTS
    chain = trace["error_chain"]
    assert len(chain) == 4, chain
    assert result["nodes"].count("generate_code") == 1 + config.MAX_FIX_ATTEMPTS


def test_environment_failure_stops_immediately():
    result = _run("sr12_environment_unavailable")
    trace = result["trace"]
    assert trace["outcome"] == "fallback"
    assert trace["repair_attempts"] == 0
    assert result["merged"]["attempts"] == 1
    assert result["nodes"].count("generate_code") == 1
    assert trace["error_category"] == repair_mod.CATEGORY_ENVIRONMENT
    assert result["llm"].repair_prompts == []


def test_failure_round_still_produces_an_answer():
    """修复失败不能把异常抛给用户：必须走到 summarize，给出结构化失败结论。"""
    result = _run("sr10_max_attempts_exhausted")
    assert result["merged"]["answer"]
    validation = runtime.validate_final(result["merged"])
    assert validation["status"] == "fail"
    assert validation["failed"], "失败轮必须在 trace 里留下未通过的检查项"
    assert result["nodes"][-1] == "suggest_followups", "失败也要走完收尾节点"


# ---- 验收门与图谱结构 ----

def test_acceptance_failure_with_ok_execution_triggers_repair():
    """代码执行成功但没产出可用结果 → 也属于"验收未通过"，必须触发修复。"""
    result = _run("sr11_acceptance_failed_code_ok")
    first_execution = next(state["execution"] for node, state in result["states"]
                           if node == "execute")
    assert first_execution["ok"] is True, "这一步的前提是代码确实跑通了"
    assert acceptance_gate(first_execution)["passed"] is False
    assert acceptance_gate(first_execution)["failed"] == ["has_artifact"]
    assert result["trace"]["outcome"] == "success"
    assert result["trace"]["repair_attempts"] == 1


def test_node_order_is_linear_with_repair_cycle():
    result = _run("sr02_syntax_fixed")
    assert result["nodes"] == ["parse_intent", "generate_code", "execute", "classify",
                               "generate_code", "execute", "classify",
                               "summarize", "suggest_followups"]


# ---- trace：可观测、可落库 ----

def test_trace_contains_required_self_repair_fields():
    trace = _run("sr03_column_fixed")["trace"]
    for key in ("first_pass_success", "repair_attempts", "error_category", "error_signature",
                "repair_strategy", "outcome", "repair_status", "attempts", "repair_latency_ms",
                "llm", "error_chain", "errors"):
        assert key in trace, f"trace.self_repair 缺少 {key}"
    assert trace["llm"]["calls"] > 0
    assert trace["llm"]["input_tokens"] > 0 and trace["llm"]["output_tokens"] > 0


def test_repair_attempts_carry_latency_and_outcome():
    trace = _run("sr09_multi_error_then_success")["trace"]
    assert len(trace["attempts"]) == 3
    for attempt in trace["attempts"]:
        assert attempt["duration_ms"] is not None
        assert attempt["strategy"]
        assert attempt["trigger_category"]
    assert trace["repair_latency_ms"]["count"] == 3
    assert trace["repair_latency_ms"]["p50_ms"] >= 0
    assert trace["repair_latency_ms"]["p95_ms"] >= trace["repair_latency_ms"]["p50_ms"]


def test_run_trace_persists_self_repair_section():
    """落库往返：Run.trace 里的 self_repair 是可用 JSON，前端能直接读。"""
    result = _run("sr08_repeated_identical")
    run_id = 4242
    trace = runtime._build_trace(run_id, "q", 0.0, [], result["merged"], {}, "", [])
    stored = jload(jdump(trace), {})
    assert stored["self_repair"]["outcome"] == "exhausted"
    assert stored["self_repair"]["repeated_error"] is True
    assert stored["self_repair"]["error_category"] == repair_mod.CATEGORY_COLUMN
    assert json.dumps(trace, ensure_ascii=False)  # 必须可 JSON 序列化


def test_trace_marks_first_pass_success_for_happy_path():
    result = _run("sr01_first_pass_success")
    trace = runtime._build_trace(1, "q", 0.0, [], result["merged"], {}, "", [])
    assert trace["self_repair"]["first_pass_success"] is True
    assert trace["self_repair"]["repair_attempts"] == 0
    assert trace["execution"]["repair_count"] == 0


# ---- 与既有链路共存：Skill few-shot / 重放 / 基线路由 ----

def test_skill_few_shot_block_still_reaches_the_prompt():
    """Skill few-shot 注入不受 Self-Repair 影响（互不干扰）。"""
    block = "## 相似分析案例（few-shot 参考）\n### 案例：各品类销售额\n```python\nprint(1)\n```"
    result = _run("sr03_column_fixed", skill_block=block)
    first_generate = next(
        m for m in result["llm"].messages
        if str(getattr(m[0], "content", "")).startswith("你是资深数据分析师"))
    assert block in str(getattr(first_generate[1], "content", ""))
    assert result["trace"]["outcome"] == "success"


def test_replay_trace_declares_no_self_repair():
    """Skill 重放路径不进入 Self-Repair：0 次修复、0 次 LLM。"""
    from types import SimpleNamespace

    from backend.skills.engine import _replay_trace

    out_dir = Path(tempfile.mkdtemp(prefix="helix_replay_"))
    (out_dir / "result.json").write_text('{"text": "结论", "tables": {"t": [{"a": 1}]}}',
                                         encoding="utf-8")
    execution = {"ok": True, "stdout": "", "stderr": "", "text": "结论",
                 "tables": {"t": [{"a": 1}]}, "charts": [], "run_dir": str(out_dir)}
    skill = SimpleNamespace(id=7, name="重放", question="q", pack_id="retail_sales")
    trace = _replay_trace(1, skill, execution, "结论", True, 120)
    assert trace["self_repair"]["repair_status"] == "not_applicable"
    assert trace["self_repair"]["repair_attempts"] == 0
    assert trace["self_repair"]["policy"] == "not_applicable"
    assert trace["llm"]["calls"] == 0


def test_legacy_policy_still_retries_uniformly_through_the_graph():
    """V1 基线可复现：同一段提示、无复读检测、烧满额度（评估对比的基准）。"""
    result = _run("sr08_repeated_identical", policy="v1")
    trace = result["trace"]
    assert trace["repair_attempts"] == config.MAX_FIX_ATTEMPTS
    assert trace["repeated_error"] is False
    prompts = result["llm"].repair_prompts
    assert len(prompts) == config.MAX_FIX_ATTEMPTS
    assert all(repair_mod.LEGACY_UNIFORM_HINT in p for p in prompts)
