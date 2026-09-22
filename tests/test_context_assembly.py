"""上下文装配器测试：省 token 的裁剪必须"省得对"。

这一组用例把每条裁剪规则钉住，并守住底线——**该给的信息一个都不能少**：

- 语义层收窄只去掉"本次没被引用的口径"，命中口径的定义与业务口径必须留下；
- 修复轮不再重复注入文件清单（同一次请求的基础块里已有），但错误上下文必须留下；
- few-shot 只在"确有候选"时收敛，且可以一条都不给；
- 结论轮表格按行收敛，但**真实数字不变**、自修复说明必须保留。
"""

import json

import pytest

from backend.agent import context as ctx
from backend.agent import prompts
from backend.evaluation import repair_cases as cases
from backend.evaluation import repair_bench as bench
from backend.semantic import render_semantic_prompt, resolve
from backend.semantic.registry import load_pack


class _Skill:
    """最小 Skill 替身（装配器只读 name / question / code）。"""

    def __init__(self, name: str, question: str, code: str):
        self.name, self.question, self.code = name, question, code


def _long_code(lines: int = 120) -> str:
    return "\n".join(f"line_{i} = {i}" for i in range(lines))


def _state(**overrides) -> dict:
    base = {
        "question": "各品类的总销售额是多少？",
        "profile": "### 数据集 `sample_sales.csv`\n- 形状: 100 行 x 5 列",
        "files": {"sample_sales.csv": "/data/sample_sales.csv"},
        "semantic_block": render_semantic_prompt("retail_sales"),
        "semantic_packs": ["retail_sales"],
        "skill_block": "",
        "skill_candidates": [],
        "spec": {"metrics": ["销售额"], "dimensions": ["品类"], "compare": None},
        "history": [{"question": f"问{i}", "answer": "答" * 800} for i in range(3)],
        "execution": {},
        "attempts": 0,
    }
    base.update(overrides)
    return base


# ---- 1. 语义层收窄 ----

def test_focused_semantic_block_keeps_hit_entries_only():
    full = render_semantic_prompt("retail_sales")
    focused = render_semantic_prompt("retail_sales", focus=["销售额", "品类"])
    assert "  - 销售额（" in focused and "  - 品类（" in focused
    assert "  - 销量（" not in focused, "未被引用的指标条目不该注入"
    assert "  - 客单价（" not in focused
    assert "订单日期" in focused, "时间字段是过滤依据，必须保留"
    assert len(focused) < len(full)
    # 口径定义（字段名 / 派生公式）不能因为收窄而丢
    pack = load_pack("retail_sales")
    metric = next(m for m in pack["metrics"] if m["name"] == "销售额")
    assert f"`{metric['field']}`" in focused


def test_domain_hints_survive_narrowing():
    focused = render_semantic_prompt("retail_sales", focus=["销售额"])
    hints = load_pack("retail_sales")["domain_hints"].strip()
    assert hints[:20] in focused, "业务口径属于必须知道的背景，收窄时不能删"


def test_semantic_block_falls_back_to_full_without_focus():
    """没有确定口径 / 拿不到包名时不能收窄（宁可贵一点，也不能猜）。"""
    state = _state(spec={})
    block = ctx.semantic_block(state, ctx.POLICY_V2)
    assert block.text == state["semantic_block"]

    state2 = _state(semantic_packs=[])
    assert ctx.semantic_block(state2, ctx.POLICY_V2).text == state2["semantic_block"]


def test_v1_policy_keeps_the_full_block():
    state = _state()
    assert ctx.semantic_block(state, ctx.POLICY_V1).text == state["semantic_block"]


# ---- 2. few-shot 收敛与截断 ----

def test_skill_examples_are_capped_with_a_marker():
    skills = [_Skill("案例A", "问题A", _long_code(120)),
              _Skill("案例B", "问题B", _long_code(120))]
    state = _state(skill_block=render_semantic_prompt("retail_sales"),
                   skill_candidates=skills)
    block = ctx.skill_block(state, ctx.POLICY_V2)
    assert "案例A" in block.text
    assert "案例B" not in block.text, "默认只注入最相关的一例"
    assert "省略" in block.text, "截断必须留下可读标注，不能静默丢代码"


def test_v1_policy_injects_both_skills_fully():
    skills = [_Skill("案例A", "问题A", _long_code(120)),
              _Skill("案例B", "问题B", _long_code(120))]
    state = _state(skill_block="FULL", skill_candidates=skills)
    block = ctx.skill_block(state, ctx.POLICY_V1)
    assert block.text == "FULL"


def test_skill_block_without_candidates_is_untouched():
    state = _state(skill_block="已有 few-shot 文本", skill_candidates=[])
    assert ctx.skill_block(state, ctx.POLICY_V2).text == "已有 few-shot 文本"


# ---- 3. 修复轮：去重但保留病因 ----

def test_repair_context_drops_duplicated_file_list():
    execution = {"stdout": "a" * 5000, "stderr": "b" * 5000}
    state = _state(attempts=1, code="x = 1", execution=execution,
                   repair_hint="【定向修复】检查列名")
    v1 = ctx.repair_context(state, ctx.POLICY_V1)
    v2 = ctx.repair_context(state, ctx.POLICY_V2)
    assert "## 可用文件" in v1.text and "sample_sales.csv" in v1.text
    assert "## 可用文件" not in v2.text, "v2 不再重复注入文件清单"
    assert "sample_sales.csv" not in v2.text
    assert v2.tokens < v1.tokens, "去重后必须更省"


def test_repair_context_keeps_error_tail_and_hint():
    execution = {"stdout": "x" * 5000, "stderr": "traceback 尾部" }
    state = _state(attempts=1, code="df['销售额']", execution=execution,
                   repair_hint="【定向修复】对照真实 schema 检查列名")
    text = ctx.repair_context(state, ctx.POLICY_V2).text
    assert "traceback 尾部" in text
    assert "对照真实 schema" in text
    assert "df['销售额']" in text


def test_repair_context_uses_category_hint_when_missing():
    state = _state(attempts=1, code="c", execution={"stderr": "e"},
                   error_category="syntax_error", repair_hint="")
    text = ctx.repair_context(state, ctx.POLICY_V2).text
    assert "代码结构" in text


# ---- 4. 历史收敛 ----

def test_history_is_trimmed_by_policy():
    state = _state()
    v1 = ctx.history_block(state["history"], ctx.POLICY_V1)
    v2 = ctx.history_block(state["history"], ctx.POLICY_V2)
    assert v1.tokens > v2.tokens
    assert v1.text.count("- 问：") == 3
    assert v2.text.count("- 问：") == 2


def test_history_block_empty_when_no_history():
    assert ctx.history_block([], ctx.POLICY_V2).text == ""


# ---- 5. 结论轮：收敛行数但不动数字 ----

def test_summarize_trims_rows_but_keeps_real_numbers():
    tables = {"结果": [{"品类": "食品", "销售额": i} for i in range(100)]}
    state = _state(execution={"ok": True, "text": "合计 4950", "tables": tables,
                              "charts": [], "stderr": ""})
    v1 = ctx.summarize_context(state, ctx.POLICY_V1)
    v2 = ctx.summarize_context(state, ctx.POLICY_V2)
    assert v2.tokens < v1.tokens
    payload = json.loads(v2.text.split("```json")[1].split("```")[0])
    assert len(payload["tables"]["结果"]) == 30
    assert payload["tables"]["结果"][0] == {"品类": "食品", "销售额": 0}
    assert payload["text"] == "合计 4950"


def test_summarize_keeps_repair_narrative_on_failure():
    state = _state(attempts=3, repair_attempt=1, repair_status="exhausted",
                   error_category="column_error", repair_reason="复读，提前终止",
                   previous_errors=[{"category": "column_error", "label": "列不存在",
                                     "message": "KeyError: '销售额'"}],
                   execution={"ok": False, "stderr": "KeyError", "tables": {},
                              "charts": [], "text": ""})
    text = ctx.summarize_context(state, ctx.POLICY_V2).text
    assert "自修复过程" in text
    assert "列不存在" in text
    assert "复读" in text


# ---- 6. 分块记账（"Token 花在哪里"的数据来源） ----

def test_generation_context_reports_per_block_tokens():
    state = _state(skill_candidates=[_Skill("案例A", "问题A", _long_code(40))],
                   skill_block="few-shot")
    assembly = ctx.generation_context(state, ctx.POLICY_V2)
    stats = assembly.stats()
    names = [b["name"] for b in stats["blocks"]]
    assert names == ["history", "semantic", "skill", "spec", "data"]
    assert stats["total_tokens"] == sum(b["tokens"] for b in stats["blocks"])
    assert stats["token_counter"]
    assert stats["policy"] == ctx.POLICY_V2


def test_v2_generation_context_is_smaller_than_v1():
    skills = [_Skill("案例A", "问题A", _long_code(40)),
              _Skill("案例B", "问题B", _long_code(40))]
    state = _state(skill_candidates=skills, skill_block="few-shot 文本")
    v1 = ctx.generation_context(state, ctx.POLICY_V1)
    v2 = ctx.generation_context(state, ctx.POLICY_V2)
    assert v2.tokens < v1.tokens


def test_context_policy_defaults_to_v2_and_can_be_pinned():
    assert ctx.resolve_policy(None) in (ctx.POLICY_V1, ctx.POLICY_V2)
    assert ctx.resolve_policy("v1") == ctx.POLICY_V1
    assert ctx.resolve_policy("V2") == ctx.POLICY_V2


def test_prompts_templates_differ_between_policies():
    """去重是模板级差异，不是运行时字符串替换的巧合。"""
    v1 = prompts.repair_user_prompt(code="c", stdout="", stderr="", files_block="F",
                                    repair_hint="h", policy="v1")
    v2 = prompts.repair_user_prompt(code="c", stdout="", stderr="", files_block="F",
                                    repair_hint="h", policy="v2")
    assert "可用文件" in v1 and "可用文件" not in v2
    assert "定向修复" not in v1  # 处方由 repair_hint 传入
    assert "h" in v2


def test_repair_scenarios_still_produce_distinct_prompts():
    """V2 的病因定向能力不得被去重削弱（回归 Self-Repair V2 的核心结论）。"""
    import tempfile
    from pathlib import Path

    from backend.agent import graph as graph_mod

    scenario_map = {s["id"]: s for s in cases.scenarios()}
    prompts_by_scenario = {}
    for sid in ("sr02_syntax_fixed", "sr03_column_fixed"):
        scenario = scenario_map[sid]
        with bench._isolated_db():
            llm = bench.StubLLM()
            ctx_holder = {"merged": {}}
            executor = bench.ScriptedExecutor(scenario, ctx_holder,
                                              Path(tempfile.mkdtemp()))
            original_llm, original_run = graph_mod.get_llm, graph_mod.run_in_sandbox
            graph_mod.get_llm = lambda: llm
            graph_mod.run_in_sandbox = executor
            try:
                merged = {}
                for _n, _d, state in graph_mod.stream_analysis(
                    scenario["question"], bench._sample_files(),
                    semantic_block=render_semantic_prompt("retail_sales"),
                    semantic_packs=["retail_sales"], run_id=8123,
                ):
                    merged = state
                    ctx_holder["merged"] = state
            finally:
                graph_mod.get_llm, graph_mod.run_in_sandbox = original_llm, original_run
            stats = (merged.get("context_stats") or {}).get("repair")
            prompts_by_scenario[sid] = stats
    assert prompts_by_scenario["sr02_syntax_fixed"], "修复轮必须留下上下文记账"
    assert prompts_by_scenario["sr03_column_fixed"]


@pytest.mark.parametrize("policy", [ctx.POLICY_V1, ctx.POLICY_V2])
def test_context_is_json_serializable(policy):
    state = _state(skill_candidates=[_Skill("A", "q", "code")], skill_block="few")
    stats = ctx.generation_context(state, policy).stats()
    assert json.loads(json.dumps(stats, ensure_ascii=False))["policy"] == policy


def test_resolved_entries_are_consistent_with_focus():
    """收窄用的 focus 必须来自解析结果本身（口径一致性）。"""
    resolved = resolve("各品类的总销售额是多少？", "retail_sales")
    focus = [m["name"] for m in resolved["metrics"]] + \
            [d["name"] for d in resolved["dimensions"]]
    block = ctx.narrowed_semantic_block("retail_sales", focus)
    for name in focus:
        assert name in block
